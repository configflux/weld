"""Public-benchmark runner (ADR 0059).

Dispatches each adapter (``weld``, ``grep``, ``tree_sitter``,
``graphify``) at each task in a loaded corpus and collects raw per-task
results. Manifest loading lives in :mod:`weld.bench._public_corpus`;
rendering lives in :mod:`weld.bench._public_report`. Keeping each
module focused keeps every file under the 400-line cap.

Determinism: per-task results are scoped to facts that are stable across
runs (which files an adapter returned, how many tokens, deterministic
status codes). Wall-clock fields (``duration_ms`` / ``ttft_ms``) are
NOT included in the rendered markdown -- the renderer drops latency
into ``status`` so ``--verify`` can assert byte-identity without
flapping on jitter. Raw timing is still available on the
:class:`AdapterResult` dataclass for callers that want a JSON sidecar.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

# Aliased: ``PublicRunReport`` carries a ``weld_version`` field and is built
# with a ``weld_version=`` keyword, so one name must not mean two things here.
from weld._version import weld_version as resolve_weld_version

# Re-export manifest types so callers continue to use one import surface.
from weld.bench._public_corpus import (  # noqa: F401
    CorpusSource,
    PublicCorpus,
    PublicRepo,
    PublicTask,
    load_public_corpus,
)


# --- Accuracy metric --------------------------------------------------------


@dataclass(frozen=True)
class AccuracyMetrics:
    """Precision / recall / F1 of a result against an answer key."""

    precision: float
    recall: float
    f1: float
    found_count: int
    expected_count: int
    hit_count: int


def accuracy_metrics(
    found: Sequence[str], expected: Sequence[str],
) -> AccuracyMetrics:
    """Score ``found`` against ``expected`` as precision / recall / F1.

    Deduplicates both sides before scoring (a tool that returns the same
    file three times does not get triple credit). When either side is
    empty, all three scores are 0.0.
    """
    found_set = {f for f in found if f}
    expected_set = {e for e in expected if e}
    hit = found_set & expected_set
    tp = len(hit)
    precision = tp / len(found_set) if found_set else 0.0
    recall = tp / len(expected_set) if expected_set else 0.0
    f1 = (
        (2 * precision * recall) / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )
    return AccuracyMetrics(
        precision=precision,
        recall=recall,
        f1=f1,
        found_count=len(found_set),
        expected_count=len(expected_set),
        hit_count=tp,
    )


# --- Adapter result envelope ------------------------------------------------


@dataclass
class AdapterResult:
    """Result envelope returned by every adapter.

    ``status`` is one of:

      - ``ok``           -- adapter produced a normal answer.
      - ``unavailable``  -- adapter's external binary is missing.
      - ``degraded``     -- adapter ran but failed gracefully (timeout,
                            non-zero exit, malformed output). Reported in
                            the Caveats section.
    """

    status: str
    files: list[str]
    tokens: int
    duration_ms: float
    cost_usd: float = 0.0
    ttft_ms: float = 0.0
    error: str = ""


@dataclass
class PublicRowResult:
    """One task scored across all configured adapters."""

    task: PublicTask
    adapter_results: dict[str, AdapterResult] = field(default_factory=dict)


@dataclass
class PublicRunReport:
    """Aggregate run output -- enough to render markdown + sidecar JSON."""

    corpus_id: str
    schema_version: int
    weld_version: str
    rows: list[PublicRowResult]


# --- Adapter dispatch -------------------------------------------------------


def dispatch_adapter(
    name: str, task: PublicTask, repo_root: Path,
) -> AdapterResult:
    """Route ``name`` to the matching adapter module.

    Raises :class:`ValueError` when the adapter is not recognized so a
    typo in the corpus manifest does not silently drop a tool from the
    comparison.
    """
    from weld.bench.adapters import (  # local import to avoid cycles
        graphify as graphify_mod,
        grep as grep_mod,
        tree_sitter as ts_mod,
        weld as weld_mod,
        weld_libclang as weld_libclang_mod,
    )

    table = {
        "weld": weld_mod.run,
        "weld_libclang": weld_libclang_mod.run,
        "grep": grep_mod.run,
        "tree_sitter": ts_mod.run,
        "graphify": graphify_mod.run,
    }
    fn = table.get(name)
    if fn is None:
        raise ValueError(
            f"Unknown adapter {name!r} (known: {sorted(table)})"
        )
    return fn(task, repo_root)


# --- Smoke-corpus materialization ------------------------------------------


def materialize_smoke_corpus(
    manifest_path: Path, workdir: Path,
) -> None:
    """Copy each ``local`` repo from the smoke fixtures into ``workdir``.

    Adapters expect a writable repo root (e.g. a fresh ``.weld/`` cache).
    Copying the fixture into a temp dir keeps the source tree clean and
    makes the smoke run hermetic.
    """
    corpus = load_public_corpus(manifest_path)
    for repo in corpus.repos:
        if repo.source.kind != "local":
            continue
        src = manifest_path.parent / repo.source.path
        dst = workdir / repo.id
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)


def _resolve_repo_root(repo: PublicRepo, workdir: Path) -> Path:
    """Resolve the concrete on-disk path for a corpus repo."""
    if repo.source.kind == "local":
        return workdir / repo.id
    # Production git source: assume callers cloned into workdir/<repo.id>.
    return workdir / repo.id


# --- Orchestration ----------------------------------------------------------

# Default adapter set used by `wd bench --public`. The order is the
# canonical column order in the markdown report; ``weld_libclang`` sits
# adjacent to ``weld`` so the tree-sitter-vs-libclang variant comparison
# reads at a glance for C++ rows.
DEFAULT_ADAPTERS: tuple[str, ...] = (
    "weld",
    "weld_libclang",
    "grep",
    "tree_sitter",
    "graphify",
)

# Adapters that only apply to a specific repo language. The dispatcher
# silently skips an adapter for repos that don't match so the per-task
# table doesn't carry a 100%-unavailable column on inapplicable rows.
_LANGUAGE_SCOPED_ADAPTERS: dict[str, frozenset[str]] = {
    "weld_libclang": frozenset({"cpp"}),
}


def _adapter_applies(adapter_name: str, repo: PublicRepo) -> bool:
    """Return True when ``adapter_name`` should run against ``repo``.

    Generic adapters (weld, grep, tree-sitter, graphify) always apply.
    Language-scoped adapters only apply when the repo's declared
    ``language`` is in the adapter's scope map. Empty/missing language
    tags are conservatively treated as "applies" so the dispatcher
    never silently drops an adapter when the manifest forgets to set
    a language tag.
    """
    scope = _LANGUAGE_SCOPED_ADAPTERS.get(adapter_name)
    if scope is None:
        return True
    return repo.language in scope


def run_public(
    corpus: PublicCorpus,
    workdir: Path,
    *,
    adapters: Iterable[str] = DEFAULT_ADAPTERS,
    statuses: dict[str, str] | None = None,
) -> PublicRunReport:
    """Run every adapter on every task and collect the raw results.

    The wall-clock per-call duration is captured for the
    :class:`AdapterResult` envelope but is NOT carried into the rendered
    markdown (see module docstring on determinism). Failure modes are
    surfaced via ``AdapterResult.status`` rather than as exceptions.

    ``statuses`` is an optional ``{repo.id: "materialized" | "skipped"}``
    map produced by :func:`weld.bench._public_setup.materialize_corpus`.
    When a repo is marked skipped, every adapter for every task in that
    repo emits a placeholder :class:`AdapterResult` with
    ``status="skipped"`` so the row still appears in the per-task table
    (honest output) but does NOT enter family rollups. The caller is
    expected to either pass ``statuses`` from a prior materialize call
    or rely on the in-process default below.
    """
    # Default-materialize so direct callers (and the legacy smoke test
    # path) keep working without having to thread statuses through.
    if statuses is None:
        statuses = _default_materialize(corpus, workdir)

    rows: list[PublicRowResult] = []
    adapter_list = tuple(adapters)
    for repo in corpus.repos:
        repo_root = _resolve_repo_root(repo, workdir)
        repo_status = statuses.get(repo.id, "materialized")
        for task in repo.tasks:
            row = PublicRowResult(task=task)
            if repo_status == "skipped":
                # Honest skipped row: every adapter reports the same
                # reason. We DO NOT shell out to the adapter because the
                # repo isn't on disk; the report renders SKIPPED cells.
                reason = _skip_reason_for(repo)
                for name in adapter_list:
                    if not _adapter_applies(name, repo):
                        continue
                    row.adapter_results[name] = AdapterResult(
                        status="skipped",
                        files=[],
                        tokens=0,
                        duration_ms=0.0,
                        error=reason,
                    )
                rows.append(row)
                continue
            for name in adapter_list:
                if not _adapter_applies(name, repo):
                    # Language-scoped adapters: weld_libclang only runs
                    # against cpp tasks. Skipping at dispatch time keeps
                    # the per-task table from carrying a 100%-unavailable
                    # column on Python/C# rows and keeps the rendered
                    # C++ narrative honest (no cpp rows -> no section).
                    continue
                start = time.perf_counter()
                try:
                    result = dispatch_adapter(name, task, repo_root)
                except Exception as exc:  # pragma: no cover - defensive
                    elapsed = (time.perf_counter() - start) * 1000.0
                    result = AdapterResult(
                        status="degraded",
                        files=[],
                        tokens=0,
                        duration_ms=elapsed,
                        cost_usd=0.0,
                        ttft_ms=0.0,
                        error=str(exc),
                    )
                row.adapter_results[name] = result
            rows.append(row)
    return PublicRunReport(
        corpus_id=corpus.corpus_id,
        schema_version=corpus.schema_version,
        weld_version=_weld_version(),
        rows=rows,
    )


def _default_materialize(
    corpus: PublicCorpus, workdir: Path,  # noqa: ARG001 - workdir kept for symmetry with materialize_corpus
) -> dict[str, str]:
    """Compute a status map without materializing repos on disk.

    The runner uses this when the caller didn't supply a ``statuses``
    arg. We do NOT shell out to ``git`` here -- the CLI path
    (:func:`weld.bench.bench_cli._run_public_bench`) calls
    :func:`weld.bench._public_setup.materialize_corpus` which actually
    clones; this default short-circuits to the same answer for the
    obvious cases (local sources are assumed pre-copied, placeholder
    SHAs are skipped). Other git-source repos get ``"materialized"``
    optimistically; if the directory doesn't exist the adapter reports
    degraded and the row appears in Caveats.
    """
    from weld.bench._public_setup import is_placeholder_sha

    statuses: dict[str, str] = {}
    for repo in corpus.repos:
        if repo.source.kind == "git" and (
            repo.source.placeholder
            or is_placeholder_sha(repo.source.sha)
        ):
            statuses[repo.id] = "skipped"
        else:
            statuses[repo.id] = "materialized"
    return statuses


def _skip_reason_for(repo: PublicRepo) -> str:
    """Stable reason string for skipped rows of ``repo``.

    Pulled out so the reason is identical across every adapter in a
    single skipped row -- byte-identity of the rendered report depends
    on this being deterministic.
    """
    from weld.bench._public_setup import (
        PLACEHOLDER_REASON,
        is_placeholder_sha,
    )

    if repo.source.placeholder:
        return PLACEHOLDER_REASON
    if repo.source.kind == "git" and is_placeholder_sha(repo.source.sha):
        return PLACEHOLDER_REASON
    return "clone failed or fixture missing"


def _weld_version() -> str:
    """Return the weld version for the report header, or ``"unknown"``.

    :mod:`weld._version` owns resolution, so a published report cannot
    disagree with ``wd --version`` about the same install. Resolving it
    here is what caused the bug this replaced: reading only
    ``<package>/../../VERSION`` finds the repo root in a source checkout
    but site-packages in an installed wheel -- where none exists -- so
    every installed run headered ``"unknown"``.

    The fallback stays prose rather than the sqlite sidecar's
    version-shaped ``"0"``: this is the first line a reader of a
    published report sees. Determinism is unaffected -- the resolver is a
    pure function of the environment, so the two runs ``--verify``
    compares answer identically.
    """
    return resolve_weld_version() or "unknown"


__all__ = [
    "AccuracyMetrics",
    "AdapterResult",
    "CorpusSource",
    "DEFAULT_ADAPTERS",
    "PublicCorpus",
    "PublicRepo",
    "PublicRowResult",
    "PublicRunReport",
    "PublicTask",
    "accuracy_metrics",
    "dispatch_adapter",
    "load_public_corpus",
    "materialize_smoke_corpus",
    "run_public",
]
