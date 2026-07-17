"""Process-local cache that skips redundant dirty-tree refreshes (bd o18k).

Working-tree-aware staleness (ADR 0066) marks the graph ``source_stale`` for as
long as an agent holds *any* uncommitted edit to a tracked source file. The
auto-refresh path (ADR 0051) then re-runs discovery (~0.7-6s) on **every** read
that follows, even when the working tree has not changed a single byte between
two reads. This module caches "discovery has already run for this exact tree
state" so the second and later reads *between* edits short-circuit.

Correctness -- a cache hit must be provably equivalent to re-running discovery
for that exact tree state:

* The cache key is a *working-tree signature*: the current ``HEAD`` sha plus,
  for every dirty tracked path, the path and a sha256 of its working-tree bytes
  (or an ``<absent>`` sentinel when the path was deleted). ``HEAD`` pins every
  committed, unmodified file; the per-file content hashes pin every modified,
  added, or untracked file. Together they uniquely determine the byte content
  discovery would read, so a single changed byte in any tracked file, a
  new/deleted tracked file, or a ``HEAD`` move all change the signature and miss
  the cache. Rename detection is disabled when listing the dirty set so a rename
  surfaces its vacated original as an explicit deletion -- otherwise a rename and
  a copy-then-restore could collapse to the same signature.
* The entry also pins the ``graph.json`` identity ``(size, mtime_ns)`` observed
  immediately after the refresh. If anything rewrites or reverts ``graph.json``
  out of band (a ``git checkout`` of a committed graph, an external
  ``wd discover``), the live stat stops matching and the cache misses -- it never
  asserts freshness for a graph it did not itself produce.
* Any failure to compute the signature or stat the graph yields a miss, never a
  false hit: the caller falls back to the existing always-refresh behaviour.

Hit-path cost (bd q3le): the caller may pass the ``HEAD`` sha it already resolved
this read (``compute_stale_info``'s ``current_sha``) so the signature does not
re-run ``git rev-parse HEAD``. It is provably the same value -- no git state
mutates between the two lookups -- so this changes cost, never the signature or a
hit/miss outcome. The dirty *set* is deliberately *not* reused from the staleness
probe: that probe lists renames (default detection) while the signature requires
``--no-renames`` to keep a rename-to-untracked-path distinct (above), so the two
sets are not interchangeable.

Boundedness: one entry per repo root in an LRU capped at :data:`_MAX_ROOTS`
(a long-lived MCP process serves a small, fixed set of roots). Per-file content
hashes are memoized by ``(path, mtime_ns, size)`` in a second LRU capped at
:data:`_MAX_HASHED_FILES`, so repeated cache-hit reads pay a ``stat`` -- not a
re-hash -- per dirty file while staying bounded.

Determinism: the signature is a pure function of tree content; the dirty paths
are sorted before hashing so identical trees always hash identically.
"""

from __future__ import annotations

import hashlib
import os
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Final

from weld._git import get_git_sha, working_tree_dirty_sources

__all__ = [
    "refresh_with_cache",
    "worktree_signature",
    "clear_refresh_cache",
]

# One entry per repo root; LRU-evicted past this. A single MCP process serves
# one root in practice, a handful under multi-repo use -- 64 is generous.
_MAX_ROOTS: Final[int] = 64
# Per-file content-hash memo cap. A dirty tree holds a handful of files
# mid-edit; a broad rebase/checkout can dirty many, so bound and LRU-evict.
_MAX_HASHED_FILES: Final[int] = 4096
# Stable stand-in for "this dirty path has no readable bytes" (a deleted file,
# or the vacated original of a rename): keeps deletion a distinguishable,
# deterministic component of the signature.
_ABSENT: Final[str] = "<absent>"

# ``root-abspath`` -> ``(signature, graph_size, graph_mtime_ns)``.
_CACHE: OrderedDict[str, tuple[str, int, int]] = OrderedDict()
# ``file-abspath`` -> ``(mtime_ns, size, sha256-hexdigest)``.
_HASH_MEMO: OrderedDict[str, tuple[int, int, str]] = OrderedDict()


def _root_key(root: Path) -> str:
    return os.path.abspath(os.fspath(root))


def _stream_sha256(path: Path) -> str | None:
    """Stream *path* through sha256 in 1 MiB chunks, or ``None`` on read error."""
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _file_content_hash(path: Path) -> str:
    """sha256 of *path*'s bytes, memoized by ``(path, mtime_ns, size)``.

    Returns the :data:`_ABSENT` sentinel when the file cannot be stat-ed or read
    -- the normal case for a tracked file deleted in the working tree -- so that
    deletion is a stable, distinguishable component of the signature rather than
    a hole.
    """
    try:
        stat = os.stat(path)
    except OSError:
        return _ABSENT
    key = os.path.abspath(os.fspath(path))
    cached = _HASH_MEMO.get(key)
    if cached is not None and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
        _HASH_MEMO.move_to_end(key)
        return cached[2]
    digest = _stream_sha256(path)
    if digest is None:
        return _ABSENT
    _HASH_MEMO[key] = (stat.st_mtime_ns, stat.st_size, digest)
    _HASH_MEMO.move_to_end(key)
    while len(_HASH_MEMO) > _MAX_HASHED_FILES:
        _HASH_MEMO.popitem(last=False)
    return digest


def worktree_signature(
    root: Path, tracked: list[str], *, head_sha: str | None = None
) -> str | None:
    """Return a content signature of the dirty working tree, or ``None``.

    The signature folds in the current ``HEAD`` sha and, for every dirty tracked
    path (rename detection disabled so vacated originals surface as deletions),
    the path plus a content hash of its working-tree bytes. ``None`` means "no
    trustworthy signature" (no ``HEAD``, git unavailable) and callers must treat
    it as a cache miss -- see the module docstring for the full argument.

    *head_sha* (bd q3le): when the caller already resolved ``HEAD`` this read
    (``compute_stale_info`` fetches it as ``current_sha`` just upstream), pass it
    to skip a redundant ``git rev-parse HEAD``. It is the *same* value a fresh
    lookup would return -- no git state mutates between the two -- so the
    signature is byte-identical either way. ``None`` (the default) means "not
    prefetched": shell ``get_git_sha`` exactly as before. Only the HEAD sha is
    threaded; the dirty set is still listed here with ``detect_renames=False``
    (never reusing ``compute_stale_info``'s rename-detecting set, which diverges
    on a tracked-file rename to an untracked path and would reopen the o18k
    collision).
    """
    head = head_sha if head_sha is not None else get_git_sha(root)
    if head is None:
        return None
    dirty = working_tree_dirty_sources(root, tracked, detect_renames=False)
    hasher = hashlib.sha256()
    hasher.update(head.encode("utf-8"))
    for rel in sorted(dirty):
        hasher.update(b"\0")
        hasher.update(rel.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(_file_content_hash(root / rel).encode("utf-8"))
    return hasher.hexdigest()


def _graph_stat(graph_path: Path) -> tuple[int, int] | None:
    try:
        stat = os.stat(graph_path)
    except OSError:
        return None
    return stat.st_size, stat.st_mtime_ns


def _tracked_from_meta(meta: dict | None) -> list[str]:
    if not isinstance(meta, dict):
        return []
    tracked = meta.get("discovered_from")
    return tracked if isinstance(tracked, list) else []


def refresh_with_cache(
    root: Path,
    graph_path: Path,
    meta: dict | None,
    do_refresh: Callable[[], dict | None],
    *,
    head_sha: str | None = None,
) -> dict | None:
    """Run *do_refresh* unless the cached tree/graph signature still holds.

    Returns ``None`` on a cache hit -- discovery is skipped because the graph
    already reflects this exact working-tree state. Otherwise runs *do_refresh*,
    records the post-refresh signature keyed to the fresh ``graph.json`` stat,
    and returns its result unchanged. The signature is computed once and reused
    for both the hit check and the store, so a working tree that changes *during*
    discovery can only cause a spurious future refresh, never a false hit.

    *head_sha* (bd q3le) is forwarded to :func:`worktree_signature`: the caller
    (auto-refresh) already resolved ``HEAD`` via ``compute_stale_info`` this
    read, so passing it elides a duplicate ``git rev-parse HEAD``.
    """
    tracked = _tracked_from_meta(meta)
    signature = worktree_signature(root, tracked, head_sha=head_sha)
    key = _root_key(root)
    if signature is not None:
        live = _graph_stat(graph_path)
        entry = _CACHE.get(key)
        if live is not None and entry == (signature, live[0], live[1]):
            _CACHE.move_to_end(key)
            return None
    result = do_refresh()
    if result is None or signature is None:
        return result
    fresh = _graph_stat(graph_path)
    if fresh is not None:
        _CACHE[key] = (signature, fresh[0], fresh[1])
        _CACHE.move_to_end(key)
        while len(_CACHE) > _MAX_ROOTS:
            _CACHE.popitem(last=False)
    return result


def clear_refresh_cache() -> None:
    """Drop every cached signature and file hash (test seam; long-process safe)."""
    _CACHE.clear()
    _HASH_MEMO.clear()
