"""``wd warm`` -- distribute graphs via CI artifacts, not git (ADR 0067).

On a fresh clone the default Mode A workflow (config committed, graph
gitignored) pays a cold ``wd discover`` before the graph is usable. ``wd warm``
instead fetches the CI-published ``graph.json`` for the nearest-ancestor commit
through a pluggable artifact source (:mod:`weld._warm_source`), verifies it by
content hash, lands it as the local graph, and then runs an ordinary
``wd discover`` refresh to reconcile it against the working tree. When no
artifact is reachable -- nothing published yet, source unreachable, every
candidate failed its hash check, or this is not a git checkout -- warm falls
back to a **full local discover** (a hard guarantee: warm is never worse than
``wd discover``).

The graph is content-addressable (ADR 0065): two discovers at a fixed commit
produce byte-identical ``graph.json``, so a commit SHA fully identifies the
graph content and the published ``graph.json.sha256`` is a true integrity tag.
Warm refuses any artifact whose bytes do not hash to the published tag, so a
corrupt, truncated, or tampered artifact can never become the local graph.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from pathlib import Path

from weld._git import ancestor_shas, get_git_sha
from weld._graph_meta_sidecar import (
    SIDECAR_VERSION,
    sidecar_path_for,
    write_graph_with_meta,
)
from weld._safe_text import dumps_safe_json
from weld._warm_source import ArtifactSource, source_from_spec
from weld.workspace_state import atomic_write_text
from weld._notice import emit

__all__ = [
    "WarmResult",
    "warm",
    "main",
    "DEFAULT_MAX_ANCESTORS",
    "ENV_SOURCE",
]

# Default ancestor-probe depth. CI publishes per main commit; a developer is
# usually only a handful of commits ahead of the last built ancestor, but a
# generous window absorbs a stale clone or a brief CI outage without forcing a
# cold discover. Bounded so a force-push / orphan history fails fast.
DEFAULT_MAX_ANCESTORS = 50

# Repo-wide default source, so a team sets the artifact location once (e.g. in
# a shell profile or CI matrix) instead of every developer passing --source.
ENV_SOURCE = "WELD_WARM_SOURCE"


@dataclass
class WarmResult:
    """Outcome of a :func:`warm` run (for the CLI and for tests).

    *outcome* is one of:

    * ``"warmed"``     -- a verified artifact was fetched, landed, and refreshed.
    * ``"discovered"`` -- no artifact was usable; a local discover ran instead.

    *artifact_sha* is the commit whose artifact was landed (``None`` for the
    discover fallback). *rejected* counts candidates refused for a hash
    mismatch / unverifiable tag. *refreshed* is True when the post-land discover
    refresh ran (always True for ``"discovered"``).
    """

    outcome: str
    artifact_sha: str | None = None
    candidates_probed: int = 0
    rejected: int = 0
    refreshed: bool = False


def _is_federated(root: Path) -> bool:
    """True when *root* is a polyrepo workspace root (federation out of scope)."""
    return (root / ".weld" / "workspaces.yaml").is_file()


def verify_artifact(data: bytes, expected_sha256: str | None) -> bool:
    """Return True iff *data* hashes to *expected_sha256* (ADR 0067 §4).

    A ``None`` / empty expected tag means the artifact is **unverifiable** and
    is rejected (returns False): warm only lands bytes it can prove match what
    CI published, so an artifact with no integrity tag never becomes the local
    graph. The comparison is case-insensitive on the expected side and uses a
    constant-time compare to avoid leaking digest bytes via timing.
    """
    if not expected_sha256:
        return False
    actual = hashlib.sha256(data).hexdigest()
    return hmac.compare_digest(actual, expected_sha256.strip().lower())


def _write_sidecar(graph_path: Path, artifact_sha: str) -> None:
    """Stamp ``graph-meta.json`` with ``git_sha = artifact_sha`` (ADR 0065).

    The fetched ``graph.json`` carries no volatile meta (ADR 0065 stripped it),
    so without this the sidecar would be absent and ``wd stale`` would treat the
    graph as source-stale. Stamping the *artifact's* SHA records the true basis
    of the fetched graph so freshness and ``wd prime`` report the artifact
    commit, and a later refresh computes its delta from the right point.
    """
    payload = {"version": SIDECAR_VERSION, "git_sha": artifact_sha}
    atomic_write_text(
        sidecar_path_for(graph_path),
        json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
    )


def _land_artifact(graph_path: Path, data: bytes) -> bool:
    """Write fetched *data* to ``graph.json`` via the canonical paired writer.

    Parses the bytes as JSON and re-emits through
    :func:`weld._graph_meta_sidecar.write_graph_with_meta` so the on-disk graph
    is canonical and any volatile keys are split exactly as a local discover
    would write them. Returns False if the bytes are not a valid graph object
    (so warm rejects the artifact and probes older candidates) -- artifact
    content is parsed as data, never executed.
    """
    try:
        graph = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(graph, dict) or "nodes" not in graph:
        return False
    write_graph_with_meta(graph_path, graph)
    return True


def _refresh(root: Path, *, full: bool) -> None:
    """Run a ``wd discover`` refresh against *root* and write the canonical graph.

    *full* forces a complete rescan (the fallback path); otherwise discovery
    auto-detects incremental vs full from the presence of a local
    ``discovery-state.json``. Imported lazily so the warm CLI stays cheap and
    free of the full discovery dependency graph until it is actually needed.

    Single-repo discover() returns the graph but leaves canonical-file writing
    to its caller (mirroring ``weld.discover.main``), so warm persists it here.
    A federated root instead writes its own meta-graph inside the workspace
    lock: warm asks for ``write_root_graph=True`` (and ``recurse=True`` so each
    child is refreshed) and must not double-write afterwards.
    """
    from weld._discover_state_check import mark_state_published
    from weld.discover import discover as _discover

    incremental = False if full else None
    if _is_federated(root):
        _discover(
            root,
            incremental=incremental,
            recurse=True,
            write_root_graph=True,
        )
        return
    graph = _discover(root, incremental=incremental)
    graph_path = root / ".weld" / "graph.json"
    write_graph_with_meta(graph_path, graph)
    # Same tail as the ``wd discover`` CLI: discovery wrote an inventory it
    # could not yet claim describes a readable graph, and the canonical copy
    # has just landed from that very run (ADR 0101 amended, bd esww/hfm6).
    mark_state_published(root, graph_path)


def _probe_and_land(
    root: Path,
    source: ArtifactSource,
    graph_path: Path,
    max_ancestors: int,
) -> WarmResult | None:
    """Probe ancestors nearest-first; land the first verified artifact.

    Returns a ``"warmed"`` :class:`WarmResult` after a successful land+refresh,
    or ``None`` when no candidate yielded a verified, valid artifact (so the
    caller falls back to a full discover). A per-candidate transport error or
    hash mismatch is swallowed and counted, never raised.
    """
    candidates = ancestor_shas(root, max_count=max_ancestors)
    rejected = 0
    for sha in candidates:
        try:
            fetched = source.fetch(sha)
        except Exception:
            fetched = None
        if fetched is None:
            continue
        data, expected = fetched
        if not verify_artifact(data, expected):
            rejected += 1
            emit(
                f"[weld] warm: artifact for {sha[:12]} failed integrity check, "
                "skipping"
            )
            continue
        if not _land_artifact(graph_path, data):
            rejected += 1
            emit(
                f"[weld] warm: artifact for {sha[:12]} is not a valid graph, "
                "skipping"
            )
            continue
        _write_sidecar(graph_path, sha)
        emit(
            f"[weld] warm: fetched graph for {sha[:12]}; refreshing to HEAD"
        )
        _refresh(root, full=False)
        return WarmResult(
            outcome="warmed",
            artifact_sha=sha,
            candidates_probed=len(candidates),
            rejected=rejected,
            refreshed=True,
        )
    return WarmResult(
        outcome="discovered",  # placeholder; caller decides fallback
        artifact_sha=None,
        candidates_probed=len(candidates),
        rejected=rejected,
        refreshed=False,
    )


def warm(
    root: Path,
    *,
    source_spec: str | None = None,
    max_ancestors: int = DEFAULT_MAX_ANCESTORS,
    allow_fallback: bool = True,
) -> WarmResult:
    """Warm the graph at *root* from a CI artifact, else discover (ADR 0067).

    Resolves an :class:`ArtifactSource` from *source_spec* (falling back to the
    ``WELD_WARM_SOURCE`` env var), probes the nearest ancestors, and lands the
    first verified artifact, then refreshes. When no artifact is usable and
    *allow_fallback* is True, runs a full local discover; with
    *allow_fallback* False it returns a ``"discovered"`` result with
    ``refreshed=False`` and leaves any existing graph untouched (for callers
    that only want a fetch).

    Federated roots are out of scope for v1: warm degrades to a plain discover
    refresh (or no-op when fallback is disabled).
    """
    root = Path(root)
    graph_path = root / ".weld" / "graph.json"

    if _is_federated(root):
        emit(
            "[weld] warm: federated root -- artifact warm is not supported yet, "
            "running a normal discover"
        )
        if allow_fallback:
            _refresh(root, full=True)
        return WarmResult(outcome="discovered", refreshed=allow_fallback)

    source = source_from_spec(source_spec or os.environ.get(ENV_SOURCE))
    probed = WarmResult(outcome="discovered")
    if source is not None and get_git_sha(root) is not None:
        result = _probe_and_land(root, source, graph_path, max_ancestors)
        if result is not None and result.outcome == "warmed":
            return result
        if result is not None:
            probed = result
    elif source is None:
        emit(
            "[weld] warm: no artifact source configured "
            f"(pass --source or set {ENV_SOURCE}); running a full discover"
        )

    if not allow_fallback:
        return probed

    emit("[weld] warm: building graph with a full local discover")
    _refresh(root, full=True)
    probed.refreshed = True
    return probed


def main(argv: list[str] | None = None) -> int:
    from weld._warm_cli import build_parser

    args = build_parser(
        default_max_ancestors=DEFAULT_MAX_ANCESTORS, env_source=ENV_SOURCE
    ).parse_args(argv)
    try:
        result = warm(
            Path(args.root),
            source_spec=args.source,
            max_ancestors=args.max_ancestors,
            allow_fallback=not args.no_fallback,
        )
    except KeyboardInterrupt:  # pragma: no cover - operator abort
        return 130
    if args.json:
        print(dumps_safe_json({
            "outcome": result.outcome,
            "artifact_sha": result.artifact_sha,
            "candidates_probed": result.candidates_probed,
            "rejected": result.rejected,
            "refreshed": result.refreshed,
        }, indent=2, ensure_ascii=True))
    else:
        if result.outcome == "warmed":
            print(
                f"[weld] warm: graph warmed from artifact "
                f"{(result.artifact_sha or '')[:12]} and refreshed to HEAD"
            )
        elif result.refreshed:
            print("[weld] warm: graph built by full local discover")
        else:
            print("[weld] warm: no artifact landed and fallback disabled")
    return 0
