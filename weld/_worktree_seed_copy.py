"""Cross-checkout ``.weld/`` copies: what may be borrowed, on what proof.

Seeding is the only place in weld where one checkout reads another
checkout's derived state, so every rule about *when that is safe* lives
here, beside the code that does it. :mod:`weld._worktree_seed` owns the
gates that decide whether to seed at all; :mod:`weld._worktree_seed_mode_a`
owns the Mode A pipeline; this module owns the bytes moving across the
boundary.

Two callers, one underlying rule:

* **Gate 4 (Mode B)** already has ``graph.json`` from git and wants only
  the derived state that explains it, so the sibling must prove its
  graph is **byte-identical** to ours before its state is borrowed --
  :func:`borrow_state_from_identical_sibling`.
* **Gate 5 (Mode A)** has no graph at all and copies the whole bundle,
  graph bytes *and* the state written beside them. Identity is then true
  by construction: the landed graph *is* the source's bytes. What must be
  proven instead is that the source did not change *between* the two
  reads, which is the job of :func:`stat_snapshot` taken around the copy.

Both rules exist for one reason. ``discovery-state.json`` records content
hashes with no binding back to the graph it was written beside (unlike
``file-index-state.json``, which self-rejects a foreign index via
``meta.index_sha256``). Pair it with the wrong graph and the next
incremental discover marks a file "unchanged" whose nodes in our graph
came from a different revision -- it then skips the very file it needed
to re-extract, and the damage is silent. Dropping the state instead only
downgrades the reconcile to a full pass: slower, never wrong.

What never crosses the boundary is **configuration**. ``discover.yaml``
decides which strategies execute, so a checkout only ever runs the config
in its own tree. Only files ``wd discover`` regenerates from scratch are
copied, addressed by fixed basename -- no caller value ever reaches a
path join. Source bytes are treated purely as data: parsed, never
executed.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Iterable, Sequence

from weld._git_worktree import list_worktrees
from weld.workspace_state import atomic_write_bytes

__all__ = [
    "GRAPH_NAME",
    "SEED_STATE_FILES",
    "borrow_state_from_identical_sibling",
    "copy_state_files",
    "drop_state_files",
    "is_graph_payload",
    "read_bytes",
    "realpath",
    "stat_snapshot",
]

#: Derived state a fresh checkout may borrow from another checkout, by
#: exact basename inside ``.weld/``. Fixed and closed on purpose: no
#: caller value ever reaches a path join, and nothing here is source or
#: config -- only files ``wd discover`` regenerates from scratch.
#:
#: ``graph.db`` is absent because it is a lazily-rebuilt sqlite index
#: (ADR 0058) that would multiply tens of megabytes per checkout; the
#: first read that needs it rebuilds it from ``graph.json``.
SEED_STATE_FILES: tuple[str, ...] = (
    "discovery-state.json",
    "file-index.json",
    "file-index-state.json",
)

GRAPH_NAME = "graph.json"

#: Chunk size for the graph-identity digest. The graph is multi-megabyte
#: on a real repo and this runs once per checkout, never in steady state.
_DIGEST_CHUNK = 1 << 20


def borrow_state_from_identical_sibling(root: Path, graph_path: Path) -> list[str]:
    """Copy derived state from a byte-identical sibling; return what landed.

    Best-effort throughout: gate 4's fix is the synthesized sidecar, and
    this only decides whether the refresh that follows is incremental or
    full. An empty list means the full path.

    Returns early when *root* already holds every borrowable file, because
    :func:`_copy_state_file` would then decline all of them anyway and the
    proof below costs a full ``sha256`` of ``graph.json`` to establish it.
    Gate 4 can re-enter on a root that keeps its sidecar decision open -- a
    pre-ADR-0065 graph records its basis inside ``graph.json`` and never gets
    a sidecar, so gate 2 never closes -- and paying a multi-megabyte digest
    on every read to discover there was nothing to copy is the read-path cost
    ADR 0101 section 4 exists to keep out.
    """
    if all((root / ".weld" / name).exists() for name in SEED_STATE_FILES):
        return []
    source = _identical_sibling(root, graph_path)
    if source is None:
        return []
    return copy_state_files(source, root)


def _identical_sibling(root: Path, graph_path: Path) -> Path | None:
    """Return a sibling checkout whose graph is byte-identical to ours.

    Candidates come from ``git worktree list`` (primary first), so the
    search is pure git plumbing: no path patterns, no assumption about
    which tool created a worktree or where it put it. *root* itself is
    skipped by realpath so a symlinked spelling cannot select itself.

    Because ``graph.json`` is content-addressable (ADR 0065), identical
    bytes prove the sibling's whole derived bundle describes our content
    exactly -- the proof this module requires. ``None`` when no candidate
    matches, including the plain-clone case where the only checkout is
    our own.
    """
    ours = _graph_digest(graph_path)
    root_key = realpath(root)
    if ours is None or root_key is None:
        # Without our own digest there is nothing to match against, and
        # without our own identity we could select ourselves as the
        # source. Either way: decline rather than guess.
        return None
    size, digest = ours
    for candidate in list_worktrees(root):
        if realpath(candidate) == root_key:
            continue
        their_graph = candidate / ".weld" / GRAPH_NAME
        if _file_size(their_graph) != size:
            continue
        theirs = _graph_digest(their_graph)
        if theirs is not None and theirs[1] == digest:
            return candidate
    return None


def copy_state_files(source: Path, root: Path) -> list[str]:
    """Copy every :data:`SEED_STATE_FILES` entry that is absent at *root*."""
    seeded: list[str] = []
    for name in SEED_STATE_FILES:
        if _copy_state_file(source / ".weld" / name, root / ".weld" / name):
            seeded.append(name)
    return seeded


def drop_state_files(root: Path, names: Iterable[str]) -> None:
    """Remove state files *we* copied; anything else at *root* is untouched."""
    for name in names:
        try:
            (root / ".weld" / name).unlink()
        except OSError:
            continue


def _copy_state_file(src: Path, dst: Path) -> bool:
    """Copy *src* to *dst* atomically if *dst* is absent. True when copied.

    Never overwrites: a file already present at *dst* belongs to this
    checkout -- written by it, or arrived tracked with it -- and is
    authoritative for it. The bytes are treated purely as data; their
    consumers (:func:`weld.discovery_state.load_state`,
    :mod:`weld._file_index_incremental`) validate schema and integrity and
    fall back to a full rebuild on anything unexpected, so a damaged or
    unreadable source can only cost time.
    """
    if dst.exists():
        return False
    try:
        atomic_write_bytes(dst, src.read_bytes())
    except OSError:
        return False
    return True


def is_graph_payload(data: bytes) -> bool:
    """True when *data* parses as a graph object -- a sanity check, not trust.

    Mirrors :func:`weld.warm._land_artifact`'s shape test so a truncated
    or unrelated file never becomes a checkout's graph. Parsing is the
    only thing done with these bytes; nothing in them is executed.
    """
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    return isinstance(parsed, dict) and "nodes" in parsed


def stat_snapshot(paths: Sequence[Path]) -> tuple[tuple[int, int] | None, ...]:
    """``(size, mtime_ns)`` per path, ``None`` for any that cannot be stat-ed.

    The same identity pair :func:`weld._graph_meta_sidecar.read_staleness_meta`
    uses to prove its sidecar mirror still belongs to the graph beside it:
    cheap, and it changes on every rewrite because weld writes these files
    by atomic rename -- a fresh inode, hence a fresh mtime. A file's
    absence is itself part of the snapshot, so a state file appearing or
    vanishing mid-copy counts as the source having moved on.
    """
    return tuple(_stat_pair(path) for path in paths)


def _stat_pair(path: Path) -> tuple[int, int] | None:
    try:
        info = path.stat()
    except OSError:
        return None
    return info.st_size, info.st_mtime_ns


def read_bytes(path: Path) -> bytes | None:
    """Read *path* whole, or ``None`` when it cannot be read."""
    try:
        return path.read_bytes()
    except OSError:
        return None


def _graph_digest(path: Path) -> tuple[int, str] | None:
    """Return ``(size, sha256)`` for *path*, or ``None`` if unreadable."""
    try:
        size = path.stat().st_size
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(_DIGEST_CHUNK), b""):
                digest.update(chunk)
    except OSError:
        return None
    return size, digest.hexdigest()


def _file_size(path: Path) -> int | None:
    """Cheap pre-filter so only same-sized candidates are hashed."""
    try:
        return path.stat().st_size
    except OSError:
        return None


def realpath(path: Path) -> str | None:
    """Canonical string form of *path*, or ``None`` when it cannot resolve."""
    try:
        return os.path.realpath(path)
    except OSError:
        return None
