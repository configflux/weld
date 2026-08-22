"""Mode A copy-seed: give a fresh worktree a graph, then make it its own.

ADR 0096 §2 gate 5. In Mode A (the ADR 0076 default) ``.weld/graph.json``
is gitignored, so a worktree created five seconds ago has none -- while
the checkout it was branched from holds one that is, at worst, a branch
delta away from correct. The first read here copies that graph in and
reconciles it, instead of refusing to answer or paying a cold full
discover.

The pipeline is deliberately the ADR 0067 ``wd warm`` one -- **land,
stamp, refresh** -- with a local checkout standing in for the CI
artifact:

1. :func:`_seed_source` picks a checkout to copy from, using nothing but
   ``git worktree list``.
2. :func:`_land_seed` copies graph and state under the per-root write
   lock (ADR 0094), proving the two came from the same generation of the
   source, and stamps the source's basis.
3. :func:`_reconcile` re-derives the graph from *this* worktree's tree.

Step 3 is unconditional, and that is the load-bearing part. The seed
describes the source checkout, which may sit on a different branch or
carry uncommitted edits, and no staleness signal can see the latter: a
source discovered dirty at our own HEAD reads as perfectly fresh. Only
re-deriving makes the graph ours, and the same pass stamps this
worktree's HEAD and branch (ADR 0096 §3). The seeded content-hash state
is what keeps that pass incremental, which is the whole saving.

Warm's *pattern* is reused, not its plumbing: its ancestor-probe model
fails once the source checkout has rebased past the fork point, whereas
a plain copy plus content-hash reconcile still converges.
"""

from __future__ import annotations

import json
from pathlib import Path

from weld._git import get_git_sha
from weld._git_worktree import get_git_branch, list_worktrees
from weld._graph_meta_sidecar import (
    SIDECAR_VERSION,
    read_sidecar_meta,
    sidecar_path_for,
)
from weld._graph_write_lock import GraphWriteLockTimeout, graph_write_lock
from weld._notice import emit
from weld._worktree_seed_copy import (
    GRAPH_NAME,
    SEED_STATE_FILES,
    copy_state_files,
    drop_state_files,
    is_graph_payload,
    read_bytes,
    realpath,
    stat_snapshot,
)
from weld.workspace_state import atomic_write_bytes, atomic_write_text

__all__ = ["copy_seed_worktree"]

#: How many times the copy is retried when the source is rewritten
#: mid-copy. One retry absorbs a single concurrent ``wd discover`` in the
#: source checkout; a source being rewritten continuously is not worth
#: waiting on, because dropping the state is already a safe outcome.
_COPY_ATTEMPTS = 2


def copy_seed_worktree(root: Path, graph_path: Path) -> dict | None:
    """Land a sibling's graph at *root*, then reconcile it to our branch.

    Returns the seed summary -- ``action``, the ``source`` checkout, the
    ``git_sha`` stamped, the ``seeded_state`` basenames, and whether the
    reconcile succeeded -- or ``None`` when no seed happened at all: no
    usable source, another process got there first, or the copy failed.

    The caller has already established the gate (``graph.json`` missing,
    *root* a linked worktree, ``discover.yaml`` present).
    """
    source = _seed_source(root)
    if source is None:
        return None
    landed = _land_seed(root, graph_path, source)
    if landed is None:
        return None
    reconciled = _reconcile(root, graph_path, landed["sidecar_bytes"])
    _emit_seed_notice(root, source, reconciled=reconciled)
    return {
        "action": "worktree_copy_seed",
        "source": str(source),
        "git_sha": landed["git_sha"],
        "seeded_state": landed["seeded_state"],
        "reconciled": reconciled,
    }


def _seed_source(root: Path) -> Path | None:
    """First checkout of *root*'s repository that can seed it, or ``None``.

    Candidates come from ``git worktree list --porcelain`` in git's own
    order -- the main worktree first -- so the primary checkout is
    preferred and the rest are tried in registration order. Being pure
    plumbing, a bare-repo hub needs no special case: its primary has no
    working tree and therefore no graph, so the scan falls through to the
    first sibling worktree that has one. The same holds for any layout
    and any tool that created the worktree.

    A candidate qualifies on a readable ``graph.json`` **and** its
    ``graph-meta.json`` sidecar. The sidecar is not optional: it carries
    the ``git_sha`` this seed is stamped with, and a checkout that cannot
    say what its own graph describes is not worth preferring over the
    next candidate.

    *root* itself is excluded by realpath so a symlinked spelling cannot
    select itself as its own source.
    """
    root_key = realpath(root)
    if root_key is None:
        return None
    for candidate in list_worktrees(root):
        if realpath(candidate) == root_key:
            continue
        graph = candidate / ".weld" / GRAPH_NAME
        if graph.is_file() and sidecar_path_for(graph).is_file():
            return candidate
    return None


def _land_seed(root: Path, graph_path: Path, source: Path) -> dict | None:
    """Copy *source*'s graph and state into *root* under the write lock.

    The lock (ADR 0094, per-root) is what makes the double-check
    meaningful: a second process that was waiting on it re-reads
    ``graph.json``, finds the winner's graph, and no-ops instead of
    landing a second copy over a reconcile already in flight.

    Each attempt snapshots the ``(size, mtime_ns)`` of every file it is
    about to read -- graph, sidecar, and state -- reads the graph bytes
    once, lands them **raw** (re-serializing would break the byte-identity
    that makes the graph content-addressable, ADR 0065), copies the state,
    and re-stats. An unchanged snapshot is the proof that all three came
    from one generation of the source.

    Watching the sidecar matters as much as watching the state.
    ``write_graph_with_meta`` lands ``graph.json`` and ``graph-meta.json``
    as two separate atomic writes, so a copy that starts between them
    would otherwise pair one generation's graph with the next
    generation's basis -- and if that basis happened to match our own
    HEAD, the result reads as perfectly fresh while describing content we
    do not have. Nothing that is not proven to describe the landed bytes
    gets recorded: a mismatch drops the state *and* the basis, leaving a
    graph that reads as stale until the reconcile replaces it.

    A first mismatch means a ``wd discover`` ran there mid-copy, which is
    worth one retry. The graph bytes themselves are safe either way,
    because the source writes them by atomic rename and a single read
    therefore always yields one complete generation.

    ``None`` when nothing was landed. Never raises: a read must not fail
    because a bootstrap optimization could not run.
    """
    src_graph = source / ".weld" / GRAPH_NAME
    watched = [
        src_graph,
        sidecar_path_for(src_graph),
        *(source / ".weld" / name for name in SEED_STATE_FILES),
    ]
    try:
        with graph_write_lock(root):
            if graph_path.exists():
                return None
            seeded: list[str] = []
            git_sha: str | None = None
            for _attempt in range(_COPY_ATTEMPTS):
                before = stat_snapshot(watched)
                data = read_bytes(src_graph)
                if data is None or not is_graph_payload(data):
                    return None
                git_sha = read_sidecar_meta(src_graph).get("git_sha")
                _land_graph_bytes(graph_path, data)
                seeded = copy_state_files(source, root)
                if stat_snapshot(watched) == before:
                    break
                drop_state_files(root, seeded)
                seeded, git_sha = [], None
            return {
                "git_sha": git_sha,
                "seeded_state": seeded,
                "sidecar_bytes": _write_seed_sidecar(graph_path, git_sha),
            }
    except (OSError, GraphWriteLockTimeout):
        return None


def _land_graph_bytes(graph_path: Path, data: bytes) -> None:
    """Write the source's graph bytes verbatim, creating ``.weld/`` if needed."""
    graph_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(graph_path, data)


def _reconcile(root: Path, graph_path: Path, stamped: bytes | None) -> bool:
    """Re-derive the seeded graph from *root*'s own working tree.

    Incremental when the seeded content-hash state survived the copy
    *and* can vouch for the graph copied beside it; full otherwise --
    ``incremental=None`` auto-detects both from the state file.

    That second condition is not ours to enforce (bd nwyq).
    :func:`_land_seed` proves graph and state came from one generation of
    the source, but a single generation of a source whose last run
    resolved files without publishing a graph is already incoherent: its
    inventory sits ahead of its own ``graph.json``. Copied verbatim, the
    borrowed hashes then match our tree, nothing reads as dirty, and the
    seeded body survives a pass that stamps it with our HEAD. The
    auto-detect refuses such a state as a delta basis (ADR 0101, second
    amendment), so the incoherent case lands here as a full derivation
    and the ordinary coherent one keeps the incremental saving.

    ``with_sqlite=False`` mirrors auto-refresh: the sqlite sidecar
    (ADR 0058) is a self-healing derived index, rebuilt lazily by the
    first read that actually needs it rather than on this one.

    On failure the source-derived basis is **withdrawn**. A graph with no
    recorded ``git_sha`` reads as stale and refreshes on the next read,
    whereas keeping the source's sha could report a dirty-source seed as
    fresh -- the one outcome ADR 0096 forbids.
    """
    try:
        from weld.discover import _discover_single_repo

        _discover_single_repo(
            root, incremental=None, write_graph=True, with_sqlite=False,
        )
    except Exception:  # noqa: BLE001 -- a failed reconcile must not fail the read.
        _withdraw_sidecar(graph_path, stamped)
        return False
    return True


def _write_seed_sidecar(graph_path: Path, git_sha: str | None) -> bytes | None:
    """Stamp the source's basis, mirroring :func:`weld.warm._write_sidecar`.

    Returns the exact bytes written so :func:`_withdraw_sidecar` can prove
    it is removing its own stamp rather than a sidecar the reconcile wrote
    afterwards. ``None`` when the source recorded no basis: there is
    nothing honest to stamp, and a basis-less graph simply reads as stale
    until the reconcile writes the real one.
    """
    if git_sha is None:
        return None
    payload = {"version": SIDECAR_VERSION, "git_sha": git_sha}
    text = json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    atomic_write_text(sidecar_path_for(graph_path), text)
    return text.encode("utf-8")


def _withdraw_sidecar(graph_path: Path, stamped: bytes | None) -> None:
    """Remove the seed's sidecar if it is still byte-for-byte our stamp."""
    if stamped is None:
        return
    path = sidecar_path_for(graph_path)
    try:
        if path.read_bytes() == stamped:
            path.unlink()
    except OSError:
        return


def _emit_seed_notice(root: Path, source: Path, *, reconciled: bool) -> None:
    """Report the seed on stderr -- one line, whatever the outcome.

    Seeding writes ``.weld/`` during what the user asked to be a read, so
    it is never silent; naming the source and the branch the answer now
    belongs to is what makes a wrong-checkout seed visible instead of
    something the user has to go looking for.
    """
    if not reconciled:
        emit(
            f"[weld] seeded worktree graph from {source}; reconcile failed -- "
            "the graph will refresh on the next read"
        )
        return
    branch = get_git_branch(root) or "detached HEAD"
    sha = (get_git_sha(root) or "")[:12] or "unknown"
    emit(f"[weld] seeded worktree graph from {source}; reconciled to {branch}@{sha}")
