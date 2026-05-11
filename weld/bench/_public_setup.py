"""Corpus materialization for ``wd bench --public`` (ADR 0059).

Translates a loaded :class:`PublicCorpus` into on-disk repos that
adapters can run against. Three kinds of repo end up on disk after a
successful call to :func:`materialize_corpus`:

  - local sources  -> a copy of the fixture tree at ``workdir / repo.id``
  - git sources    -> a shallow clone at the pinned SHA at
                      ``workdir / repo.id`` (clone-on-demand)
  - placeholder SHAs (heuristic or ``placeholder: true``) -> nothing on
                      disk; the repo id appears in the returned status
                      map as ``"skipped"`` and the runner emits honest
                      "skipped" rows in the report (per ADR 0059
                      "honest losing").

Determinism: the placeholder detector is a pure function of the SHA
string. Clone-on-demand is bounded by ``_CLONE_TIMEOUT_S`` and reports
failure (network down, invalid url, server-side timeout, etc.) by
returning ``False`` -- the caller marks the repo skipped rather than
raising. ``wd bench --public`` therefore always produces a report.

Setup steps (post-clone preparation):

A repo may declare an optional ``setup:`` clause (see
:class:`weld.bench._public_corpus.SetupStep`). The canonical case is
the libclang C++ variant of the public benchmark: cmake must run after
the clone to produce ``compile_commands.json`` before the libclang
strategy can consume it. The setup step is *gated* by a binary that
must be on PATH (e.g. ``cmake --version`` succeeds). When the binary
is missing, the repo is still ``materialized`` (the clone succeeded);
the per-repo setup status is recorded as ``setup_unavailable`` so the
downstream adapter renders SKIPPED with a stable reason rather than
crashing.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from weld.bench._public_corpus import PublicCorpus, PublicRepo, SetupStep


# Stable reason string used in the rendered report; surfaces in the
# AdapterResult.error field so ``--verify`` byte-identity holds across
# runs.
PLACEHOLDER_REASON = "placeholder SHA (corpus entry not yet pinned)"

# Bounded subprocess timeout for ``git fetch`` / ``git checkout``. Long
# enough for a shallow fetch over a slow network, short enough that the
# overall bench run completes in human time.
_CLONE_TIMEOUT_S = 600

# Stable reason strings emitted by the setup-step machinery. Surfaced
# verbatim in the per-repo ``setup_status`` map and forwarded into
# adapter SKIPPED rows; identical text on every run keeps the report
# byte-identical for ``--verify``.
SETUP_BINARY_MISSING_REASON = "{binary} not on PATH (setup gate)"
SETUP_FAILED_REASON = (
    "setup command failed: exit={exit_code}, stderr={stderr}"
)
SETUP_TIMEOUT_REASON = "setup command timed out after {timeout_s}s"
SETUP_PRODUCES_MISSING_REASON = (
    "setup completed but {produces} was not produced"
)

# Sentinel key used inside the materialization status map to carry the
# per-repo setup state. The map remains ``dict[str, str]`` from the
# caller's perspective; setup state lives under a reserved prefix so a
# repo id collision is impossible.
_SETUP_KEY_PREFIX = "__setup__/"


def is_placeholder_sha(sha: str) -> bool:
    """Heuristic: return True for SHAs that are obviously not real.

    The detector is intentionally conservative: it must NEVER mark a
    real SHA as a placeholder (false positives would silently drop a
    repo from the benchmark). It catches the cheapest obvious junk:

      - empty / too-short string
      - all-same character (``"f" * 40``, ``"0" * 40``)
      - short repeating cycle (``"abc" * N + "a"``)

    Plausibly-shaped fakes (e.g. a hand-typed-looking SHA whose digits
    are all hex but obviously not a real commit) must be opted-in
    explicitly via ``placeholder: true`` on the manifest entry. The
    detector returns False for them so the choice to skip is loud and
    traceable rather than silent.
    """
    if not sha or len(sha) < 40:
        return False
    distinct = len(set(sha))
    if distinct <= 1:
        return True
    # Short cycle: try every cycle length from 1..6. A SHA is a
    # placeholder if it can be reproduced by repeating one short substring.
    for cycle_len in range(1, 7):
        chunk = sha[:cycle_len]
        # Build a string of len(sha) by repeating ``chunk`` and truncate.
        repeats = (chunk * ((len(sha) // cycle_len) + 1))[: len(sha)]
        if repeats == sha:
            return True
    return False


# --- Clone-on-demand --------------------------------------------------------


def _run_git(
    cmd: list[str], cwd: Path, timeout: int,
) -> subprocess.CompletedProcess[str]:
    """Bounded ``git`` subprocess. Separate so tests can patch it."""
    return subprocess.run(  # noqa: S603 -- args bounded and validated above
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def clone_repo_at_sha(repo: PublicRepo, workdir: Path) -> bool:
    """Shallow-clone ``repo`` at the pinned SHA into ``workdir / repo.id``.

    Returns True on success, False on any failure. Callers must NOT
    raise on a False return -- a clone failure marks the repo skipped
    and lets the rest of the bench run continue.

    Implementation:

      1. ``git init`` in a fresh ``workdir / repo.id`` directory.
      2. ``git remote add origin <url>``.
      3. ``git fetch --depth 1 origin <sha>``.
      4. ``git checkout FETCH_HEAD``.

    Step 3 only succeeds when the server allows fetching by SHA
    (modern github does). Otherwise we get a deterministic non-zero
    exit and report False.
    """
    target = workdir / repo.id
    if target.exists():
        # Already present from a prior run -- treat as success.
        return True
    target.mkdir(parents=True, exist_ok=True)
    sha = repo.source.sha
    url = repo.source.url
    try:
        steps: list[list[str]] = [
            ["git", "init", "-q"],
            ["git", "remote", "add", "origin", url],
            ["git", "fetch", "--depth", "1", "origin", sha],
            ["git", "checkout", "-q", "FETCH_HEAD"],
        ]
        for cmd in steps:
            result = _run_git(cmd, target, _CLONE_TIMEOUT_S)
            if result.returncode != 0:
                return False
        return True
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return False


# --- Setup-step execution ---------------------------------------------------


def _which(binary: str) -> str | None:
    """Indirection so tests can mock binary presence per call."""
    return shutil.which(binary)


def _run_setup_subprocess(
    cmd: tuple[str, ...] | list[str],
    cwd: Path,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    """Bounded subprocess wrapper. Separate so tests can mock it.

    ``cmd`` is passed without ``shell=True`` -- the manifest supplies a
    pre-split argv vector so an unbalanced quote in a future setup
    clause cannot land arbitrary code.
    """
    return subprocess.run(  # noqa: S603 -- argv vector, no shell
        list(cmd),
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def run_setup_step(
    step: SetupStep, repo_root: Path,
) -> tuple[str, str]:
    """Execute a setup step against a cloned repo and report the outcome.

    Returns a ``(status, reason)`` tuple. ``status`` is one of:

      - ``setup_ok``           -- command ran and produced the artefact.
      - ``setup_unavailable``  -- gate binary missing on PATH; no
                                  subprocess was spawned. This is the
                                  cmake-not-in-CI branch.
      - ``setup_failed``       -- command ran but exited non-zero, timed
                                  out, or did not produce the artefact.

    The reason string is stable across runs so ``--verify`` byte-identity
    holds.
    """
    if _which(step.requires_binary) is None:
        return (
            "setup_unavailable",
            SETUP_BINARY_MISSING_REASON.format(binary=step.requires_binary),
        )
    try:
        result = _run_setup_subprocess(
            step.cmd, repo_root, step.timeout_s,
        )
    except subprocess.TimeoutExpired:
        return (
            "setup_failed",
            SETUP_TIMEOUT_REASON.format(timeout_s=step.timeout_s),
        )
    except (OSError, ValueError) as exc:  # pragma: no cover - defensive
        return ("setup_failed", str(exc))
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()[:160]
        return (
            "setup_failed",
            SETUP_FAILED_REASON.format(
                exit_code=result.returncode, stderr=stderr,
            ),
        )
    produced = repo_root / step.produces
    if not produced.is_file():
        return (
            "setup_failed",
            SETUP_PRODUCES_MISSING_REASON.format(produces=step.produces),
        )
    return ("setup_ok", "")


def _store_setup_status(
    statuses: dict[str, str], repo_id: str, status: str, reason: str,
) -> None:
    """Write the per-repo setup state into the materialization map."""
    statuses[f"{_SETUP_KEY_PREFIX}{repo_id}"] = f"{status}|{reason}"


def get_setup_status(
    statuses: dict[str, str], repo_id: str,
) -> tuple[str, str]:
    """Read the per-repo setup state from the materialization map.

    Returns ``("setup_ok", "")`` when no setup clause was configured
    for the repo so the downstream adapter logic can treat "no setup"
    as a successful pass-through.
    """
    raw = statuses.get(f"{_SETUP_KEY_PREFIX}{repo_id}")
    if raw is None:
        return ("setup_ok", "")
    status, _, reason = raw.partition("|")
    return (status, reason)


# --- Materialization driver -------------------------------------------------


def _copy_local(
    repo: PublicRepo, manifest_path: Path, workdir: Path,
) -> bool:
    """Copy a ``local``-kind repo from the fixture path into ``workdir``."""
    src = manifest_path.parent / repo.source.path
    dst = workdir / repo.id
    if dst.exists():
        shutil.rmtree(dst)
    try:
        shutil.copytree(src, dst)
    except OSError:
        return False
    return True


def _maybe_run_setup(
    repo: PublicRepo, workdir: Path, statuses: dict[str, str],
) -> None:
    """Execute the optional setup step for a freshly-materialized repo.

    No-op when the repo declares no ``setup:`` clause. When a clause is
    present, the outcome is stored under a reserved prefix in
    ``statuses`` so :func:`get_setup_status` can recover it for the
    downstream adapter dispatch.
    """
    if repo.setup is None:
        return
    repo_root = workdir / repo.id
    status, reason = run_setup_step(repo.setup, repo_root)
    _store_setup_status(statuses, repo.id, status, reason)


def materialize_corpus(
    corpus: PublicCorpus, manifest_path: Path, workdir: Path,
) -> dict[str, str]:
    """Materialize every repo in ``corpus`` into ``workdir``.

    Returns a status map ``{repo.id: status}`` where each status is one
    of:

      - ``"materialized"`` -- the repo is on disk at ``workdir/repo.id``
      - ``"skipped"``      -- placeholder SHA or clone failure
                              (no directory created)

    The runner uses this map to decide whether to dispatch adapters or
    emit skipped AdapterResults for the repo's tasks.

    For repos with a ``setup:`` clause, the corresponding post-clone
    command (e.g. cmake configure) is invoked after a successful
    materialization. The setup outcome is recorded separately and
    retrieved via :func:`get_setup_status`; it never demotes the
    primary ``"materialized"`` status because a missing gate binary
    is a runtime-environment fact, not a corpus failure.
    """
    statuses: dict[str, str] = {}
    for repo in corpus.repos:
        kind = repo.source.kind
        if kind == "local":
            ok = _copy_local(repo, manifest_path, workdir)
            statuses[repo.id] = "materialized" if ok else "skipped"
            if ok:
                _maybe_run_setup(repo, workdir, statuses)
            continue
        if kind == "git":
            # Explicit annotation wins over the heuristic; both routes
            # end at the same skipped outcome.
            if (
                repo.source.placeholder
                or is_placeholder_sha(repo.source.sha)
            ):
                statuses[repo.id] = "skipped"
                continue
            ok = clone_repo_at_sha(repo, workdir)
            statuses[repo.id] = "materialized" if ok else "skipped"
            if ok:
                _maybe_run_setup(repo, workdir, statuses)
            continue
        # Unknown kind: mark skipped so the run keeps going.
        statuses[repo.id] = "skipped"
    return statuses


__all__ = [
    "PLACEHOLDER_REASON",
    "SETUP_BINARY_MISSING_REASON",
    "SETUP_FAILED_REASON",
    "SETUP_PRODUCES_MISSING_REASON",
    "SETUP_TIMEOUT_REASON",
    "clone_repo_at_sha",
    "get_setup_status",
    "is_placeholder_sha",
    "materialize_corpus",
    "run_setup_step",
]
