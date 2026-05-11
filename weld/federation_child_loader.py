"""Child-graph loader for :class:`weld.federation.FederatedGraph`.

ADR 0058 (sqlite sidecar storage) gave each child a fast lazy read path
via :class:`SqliteBackedGraph`. The federation rewires its
``_load_child`` to prefer the sidecar; this module owns the actual
loader machinery so ``weld/federation.py`` stays under the
CLAUDE.md 400-line cap.

The functions here are deliberately pure (no class state). The
:class:`FederatedGraph` passes its sentinel cache, JSON cache, and
``read_bytes`` hook by reference so the loader can populate the caches
while keeping the test seams (``read_bytes`` patching) intact.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Callable
from pathlib import Path

from weld._sqlite_reader import (
    SqliteBackedGraph,
    open_sidecar_if_fresh,
)
from weld.federation_support import (
    ChildGraphCache,
    ChildHandle,
    CorruptChild,
    LoadedChild,
    MissingChild,
    UninitializedChild,
    load_graph_bytes,
)
from weld.graph import CHILD_SCHEMA_VERSION, Graph, SchemaVersionError
from weld.workspace import ChildEntry

__all__ = [
    "child_edges_for",
    "child_local_context",
    "load_child",
    "load_child_for_query",
    "load_child_from_json",
]

#: Read-bytes callable signature. Default reads from disk; tests can
#: substitute a side-effecting callable to verify caching semantics.
ReadBytes = Callable[[Path], bytes]


def load_child(
    *,
    name: str,
    entry: ChildEntry,
    workspace_root: Path,
    sentinel_cache: dict[str, LoadedChild],
    child_cache: ChildGraphCache,
    sqlite_cache: dict[str, SqliteBackedGraph] | None = None,
    read_bytes: ReadBytes,
) -> LoadedChild:
    """Return a child handle, preferring the lazy sqlite path.

    Priority order:

    1. Sentinel cache (missing/uninit/corrupt) results are returned
       immediately if present.
    2. ``sqlite_cache`` hit: a previously opened
       :class:`SqliteBackedGraph` is reused (no reopen). The caller
       guarantees the cache lives for the duration of one
       :class:`FederatedGraph` invocation, which mirrors the
       ``_root_graph`` snapshot lifetime; a JSON write during the
       call is the existing TOCTOU window the federation already
       accepts.
    3. Sidecar fast path: if ``graph.db`` is fresh, return a
       :class:`SqliteBackedGraph`. The handle is *not* interchangeable
       with :class:`Graph` for ``query`` (no inverted index yet);
       callers needing query must request the JSON path via
       :func:`load_child_for_query`.
    4. JSON path: parse ``graph.json`` and cache the resulting
       :class:`Graph` keyed by ``(name, sha256)``.
    """
    sentinel = sentinel_cache.get(name)
    if sentinel is not None:
        return sentinel

    if sqlite_cache is not None:
        cached_sqlite = sqlite_cache.get(name)
        if cached_sqlite is not None:
            return cached_sqlite

    child_root = workspace_root / entry.path
    graph_path = child_root / ".weld" / "graph.json"
    graph_rel = _graph_rel_path(entry)

    early = _maybe_sentinel(name, entry, child_root, graph_path, graph_rel)
    if early is not None:
        sentinel_cache[name] = early
        return early

    # ADR 0058 fast path: try the sqlite sidecar before reading JSON.
    # ``open_sidecar_if_fresh`` only returns a handle when the
    # ``source_json_sha`` matches; a stale sidecar transparently falls
    # through to the JSON path.
    sqlite_handle = open_sidecar_if_fresh(graph_path)
    if sqlite_handle is not None:
        if sqlite_cache is not None:
            sqlite_cache[name] = sqlite_handle
        return sqlite_handle

    return load_child_from_json(
        name=name,
        entry=entry,
        child_root=child_root,
        graph_path=graph_path,
        graph_rel=graph_rel,
        sentinel_cache=sentinel_cache,
        child_cache=child_cache,
        read_bytes=read_bytes,
    )


def load_child_for_query(
    *,
    name: str,
    entry: ChildEntry,
    workspace_root: Path,
    sentinel_cache: dict[str, LoadedChild],
    child_cache: ChildGraphCache,
    read_bytes: ReadBytes,
) -> LoadedChild:
    """Return the JSON-backed :class:`Graph` for a child (always).

    ``Graph.query`` depends on the in-memory inverted index, BM25
    corpus, and alias index, none of which are rebuilt from sqlite
    yet. Callers therefore opt into the JSON path explicitly here.
    Sentinel results are returned as-is so callers can skip
    missing/uninit/corrupt children.
    """
    sentinel = sentinel_cache.get(name)
    if sentinel is not None:
        return sentinel

    child_root = workspace_root / entry.path
    graph_path = child_root / ".weld" / "graph.json"
    graph_rel = _graph_rel_path(entry)

    early = _maybe_sentinel(name, entry, child_root, graph_path, graph_rel)
    if early is not None:
        sentinel_cache[name] = early
        return early

    return load_child_from_json(
        name=name,
        entry=entry,
        child_root=child_root,
        graph_path=graph_path,
        graph_rel=graph_rel,
        sentinel_cache=sentinel_cache,
        child_cache=child_cache,
        read_bytes=read_bytes,
    )


def load_child_from_json(
    *,
    name: str,
    entry: ChildEntry,
    child_root: Path,
    graph_path: Path,
    graph_rel: str,
    sentinel_cache: dict[str, LoadedChild],
    child_cache: ChildGraphCache,
    read_bytes: ReadBytes,
) -> LoadedChild:
    """JSON-path load of a child with sha256-keyed cache lookup.

    Returns a :class:`Graph` on success or a :class:`CorruptChild`
    sentinel on parse failure. Used by :func:`load_child` (after the
    sqlite fast path misses) and :func:`load_child_for_query` (always).
    """
    try:
        raw = read_bytes(graph_path)
    except OSError as exc:
        loaded: LoadedChild = CorruptChild(
            name=name,
            path=entry.path,
            graph_path=graph_rel,
            remote=entry.remote,
            error=f"{type(exc).__name__}: {exc}",
        )
        sentinel_cache[name] = loaded
        return loaded

    digest = hashlib.sha256(raw).hexdigest()

    # LRU cache lookup keyed by (name, sha256). On hit the expensive
    # JSON parse + Graph construction is skipped.
    cached_graph = child_cache.get(name, digest)
    if cached_graph is not None:
        return cached_graph

    try:
        data = load_graph_bytes(
            raw,
            graph_path=graph_path,
            max_supported_schema_version=CHILD_SCHEMA_VERSION,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, SchemaVersionError, ValueError) as exc:
        loaded = CorruptChild(
            name=name,
            path=entry.path,
            graph_path=graph_rel,
            remote=entry.remote,
            error=f"{type(exc).__name__}: {exc}",
        )
        sentinel_cache[name] = loaded
        return loaded

    observed = _graph_digest(graph_path, read_bytes)
    if observed is not None and observed != digest:
        print(
            f"[weld] warning: child graph changed during load: {graph_path}",
            file=sys.stderr,
        )

    graph = Graph(child_root)
    graph._data = data
    graph._build_inverted_index()
    child_cache.put(name, digest, graph)
    return graph


def child_local_context(
    child: ChildHandle, local_id: str,
) -> tuple[list[dict], list[dict]]:
    """Return ``(neighbors, edges)`` for *local_id* inside the child.

    Works against both :class:`Graph` (JSON-backed, exposing
    ``.context``) and :class:`SqliteBackedGraph` (sidecar-backed,
    exposing ``.neighbors`` / ``.get_node`` indexed queries). For the
    sqlite path this is one indexed query per call rather than a full
    child JSON scan -- the core of the memory-peak benefit ADR 0058
    promised.
    """
    if isinstance(child, SqliteBackedGraph):
        edges = child.neighbors(local_id)
        neighbor_ids: set[str] = set()
        for edge in edges:
            if edge["from"] == local_id and edge["to"] != local_id:
                neighbor_ids.add(edge["to"])
            elif edge["to"] == local_id and edge["from"] != local_id:
                neighbor_ids.add(edge["from"])
        neighbors: list[dict] = []
        for nid in sorted(neighbor_ids):
            node = child.get_node(nid)
            if node is not None:
                neighbors.append(node)
        return neighbors, edges
    # JSON-backed Graph: delegate to its own context for parity.
    child_context = child.context(local_id, fallback=False)
    return (
        list(child_context.get("neighbors", [])),
        list(child_context.get("edges", [])),
    )


def child_edges_for(child: ChildHandle, local_id: str) -> list[dict]:
    """Return every edge touching *local_id* inside the child.

    Sqlite path uses an indexed neighbor query (cheap); JSON path
    scans the in-memory edge list (already in memory). Both return the
    same shape so callers can iterate uniformly.
    """
    if isinstance(child, SqliteBackedGraph):
        return child.neighbors(local_id)
    return [
        edge
        for edge in child.dump().get("edges", [])
        if edge["from"] == local_id or edge["to"] == local_id
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _graph_rel_path(entry: ChildEntry) -> str:
    return (Path(entry.path) / ".weld" / "graph.json").as_posix()


def _graph_digest(graph_path: Path, read_bytes: ReadBytes) -> str | None:
    try:
        return hashlib.sha256(read_bytes(graph_path)).hexdigest()
    except OSError:
        return None


def _maybe_sentinel(
    name: str,
    entry: ChildEntry,
    child_root: Path,
    graph_path: Path,
    graph_rel: str,
) -> LoadedChild | None:
    """Return a sentinel if the child is missing or uninitialised."""
    if not child_root.is_dir() or not (child_root / ".git").exists():
        return MissingChild(
            name=name,
            path=entry.path,
            graph_path=graph_rel,
            remote=entry.remote,
        )
    if not graph_path.is_file():
        return UninitializedChild(
            name=name,
            path=entry.path,
            graph_path=graph_rel,
            remote=entry.remote,
        )
    return None
