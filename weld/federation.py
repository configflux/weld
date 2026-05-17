"""Federated workspace query/context/path wrapper.

ADR 0058 rewires ``_load_child`` to a :class:`SqliteBackedGraph` when
the child's sidecar is fresh; read paths run against it. Option B
added the lazy per-query inverted index. ADR 0063 adds an opt-in
eager aggregation (``WELD_FEDERATION_EAGER`` / ``eager_index=True``).
Stale/missing sidecars fall back via :meth:`_load_child_for_query`.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path

from weld._federation_eager_index import (
    EagerFederationIndex,
    build_eager_index_for,
    resolve_eager_flag,
)
from weld._federation_query import query_federated as _query_federated
from weld._sqlite_reader import SqliteBackedGraph
from weld.federation_child_loader import (
    child_edges_for as _child_edges_for,
    child_local_context as _child_local_context,
    load_child as _load_child_impl,
    load_child_for_query as _load_child_for_query_impl,
)
from weld.federation_support import (
    ChildGraphCache,
    DEFAULT_CACHE_MAXSIZE,
    LoadedChild,
    edge_key,
    prefix_node_id,
    render_display_id,
    sorted_edges,
    split_prefixed_id,
)
from weld.graph import Graph
from weld.graph_context import context_with_fallback as _context_with_fallback
from weld.workspace import ChildEntry, UNIT_SEPARATOR
from weld.workspace_state import load_workspace_config

class FederatedGraph:
    """Read-only graph facade for workspace roots with ``workspaces.yaml``."""

    def __init__(
        self,
        workspace_root: Path,
        *,
        cache_maxsize: int = DEFAULT_CACHE_MAXSIZE,
        eager_index: bool | None = None,
    ) -> None:
        self._root = Path(workspace_root)
        config = load_workspace_config(self._root)
        if config is None:
            raise ValueError(f"{self._root} is not a federated workspace root")
        self._children: dict[str, ChildEntry] = {
            child.name: child
            for child in sorted(config.children, key=lambda entry: entry.name)
        }
        self._root_graph = Graph(self._root)
        self._root_graph.load()
        self._root_edges: list[dict] = list(self._root_graph.dump().get("edges", []))
        self._child_cache = ChildGraphCache(maxsize=cache_maxsize)
        # Sentinel cache (missing/uninitialized/corrupt); cheap, no eviction.
        self._sentinel_cache: dict[str, LoadedChild] = {}
        # ADR 0058: per-name sqlite-handle cache; shares the
        # ``_root_graph`` TOCTOU window (one MCP/CLI invocation).
        self._sqlite_cache: dict[str, SqliteBackedGraph] = {}
        # ADR 0063: opt-in eager inverted-index aggregation. Kwarg wins;
        # otherwise consult ``WELD_FEDERATION_EAGER`` (1/true/yes/on).
        self.eager_index_active: bool = resolve_eager_flag(eager_index)
        self._eager_index: EagerFederationIndex = (
            build_eager_index_for(self) if self.eager_index_active
            else EagerFederationIndex.empty()
        )

    def close(self) -> None:
        """Close every cached sqlite child handle. Idempotent."""
        for handle in self._sqlite_cache.values():
            handle.close()
        self._sqlite_cache.clear()

    def __enter__(self) -> FederatedGraph:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def children_status(self) -> dict[str, dict[str, object]]:
        """Return the current status of every registered child repo.

        A child is "present" whenever ``_load_child`` returns any
        readable handle -- either a JSON-backed :class:`Graph` or a
        sqlite-backed :class:`SqliteBackedGraph` (ADR 0058). The
        sentinel types (``MissingChild`` / ``UninitializedChild`` /
        ``CorruptChild``) carry their own ``status`` field.
        """
        status: dict[str, dict[str, object]] = {}
        for name in sorted(self._children):
            loaded = self._load_child(name)
            if isinstance(loaded, (Graph, SqliteBackedGraph)):
                entry = self._children[name]
                status[name] = {
                    "status": "present",
                    "graph_path": self._graph_rel_path(entry),
                }
                if entry.remote is not None:
                    status[name]["remote"] = entry.remote
                continue
            payload: dict[str, object] = {
                "status": loaded.status,
                "graph_path": loaded.graph_path,
            }
            if loaded.remote is not None:
                payload["remote"] = loaded.remote
            if loaded.error is not None:
                payload["error"] = loaded.error
            status[name] = payload
        return status

    def query(self, term: str, limit: int = 20) -> dict:
        """Fan out tokenized search across root + present children.

        Sqlite-fresh children run :meth:`SqliteBackedGraph.query`
        (lazy per-query inverted index, ADR 0058 Option B); stale or
        missing sidecars fall back to the JSON-backed
        :class:`Graph` via :meth:`_load_child_for_query`.
        """
        return _query_federated(self, term, limit)

    def _child_query_matches(
        self, name: str, term: str, limit: int,
    ) -> list[dict]:
        """Return query matches for one child, sqlite path when possible."""
        child = self._load_child(name)
        if isinstance(child, SqliteBackedGraph):
            # ADR 0063: eager path when flag is on and child was covered.
            if self.eager_index_active and name in self._eager_index.eager_children:
                return self._eager_index.query_child_matches(
                    child, name, term, limit=limit,
                )
            return list(child.query(term, limit=limit).get("matches", []))
        if not isinstance(child, Graph):
            child = self._load_child_for_query(name)
            if not isinstance(child, Graph):
                return []
        return list(child.query(term, limit=limit).get("matches", []))

    def _exact_context(self, canonical_id: str) -> dict | None:
        node = self.get_node(canonical_id)
        if node is None:
            return None
        neighbors: dict[str, dict] = {}
        edges: dict[str, dict] = {}
        parts = split_prefixed_id(canonical_id)
        if parts is not None:
            child_name, local_id = parts
            child = self._load_child(child_name)
            if isinstance(child, (Graph, SqliteBackedGraph)):
                local_neighbors, local_edges = _child_local_context(child, local_id)
                for neighbor in local_neighbors:
                    prefixed = self._prefix_node(child_name, neighbor)
                    neighbors.setdefault(prefixed["id"], prefixed)
                for edge in local_edges:
                    prefixed_edge = self._prefix_edge(child_name, edge)
                    edges.setdefault(edge_key(prefixed_edge), prefixed_edge)
        for edge in self._root_edges_for(canonical_id):
            other_id = edge["to"] if edge["from"] == canonical_id else edge["from"]
            other = self.get_node(other_id)
            if other is None:
                continue
            neighbors.setdefault(other["id"], other)
            decorated = self._decorate_edge(edge)
            edges.setdefault(edge_key(decorated), decorated)
        neighbors.pop(canonical_id, None)
        return {
            "node": node,
            "neighbors": [neighbors[nid] for nid in sorted(neighbors)],
            "edges": sorted_edges(edges.values()),
        }

    def context(self, node_id: str, *, fallback: bool = True) -> dict:
        """1-hop neighborhood. Prefixed child ids short-circuit and skip fallback."""
        canonical_id = self._canonicalize_node_id(node_id)
        # Prefixed-child ids must never go through query fallback; force off.
        effective_fallback = fallback and split_prefixed_id(canonical_id) is None
        return _context_with_fallback(
            raw_node_id=node_id, error_node_id=canonical_id,
            fallback=effective_fallback,
            exact_fn=lambda: self._exact_context(canonical_id),
            query_fn=self.query,
            recurse_fn=lambda nid: self.context(nid, fallback=False),
            match_tokens_fn=Graph._match_tokens,
        )

    def path(self, from_id: str, to_id: str) -> dict:
        """Return the shortest path across child graphs and root cross edges."""
        start = self._canonicalize_node_id(from_id)
        goal = self._canonicalize_node_id(to_id)
        if self.get_node(start) is None or self.get_node(goal) is None:
            return {"path": None, "reason": "node not found"}

        queue: deque[str] = deque([start])
        visited = {start}
        prev: dict[str, tuple[str, dict]] = {}

        while queue:
            current = queue.popleft()
            if current == goal:
                break
            for neighbor_id, edge in self._adjacent(current):
                if neighbor_id in visited:
                    continue
                visited.add(neighbor_id)
                prev[neighbor_id] = (current, edge)
                queue.append(neighbor_id)
        else:
            return {"path": None, "reason": "no path found"}

        path_ids: list[str] = [goal]
        edges: list[dict] = []
        current = goal
        while current != start:
            parent, edge = prev[current]
            path_ids.append(parent)
            edges.append(edge)
            current = parent
        path_ids.reverse()
        edges.reverse()
        nodes = [self.get_node(node_id) for node_id in path_ids]
        return {
            "path": [node for node in nodes if node is not None],
            "edges": edges,
        }

    def dump(self) -> dict:
        """Return the root graph data for provenance and meta access."""
        return self._root_graph.dump()

    def get_node(self, node_id: str) -> dict | None:
        """Return a root node or prefixed child node with display metadata.

        Works against both JSON-backed and sqlite-backed child handles
        (:class:`SqliteBackedGraph`) -- both expose ``get_node`` with
        the same shape.
        """
        canonical_id = self._canonicalize_node_id(node_id)
        parts = split_prefixed_id(canonical_id)
        if parts is None:
            node = self._root_graph.get_node(canonical_id)
            if node is None:
                return None
            return self._decorate_node(node)

        child_name, local_id = parts
        child = self._load_child(child_name)
        if not isinstance(child, (Graph, SqliteBackedGraph)):
            return None
        node = child.get_node(local_id)
        if node is None:
            return None
        return self._prefix_node(child_name, node)

    def _query_payload(self, term: str, matches: list[dict]) -> dict:
        match_ids = {match["id"] for match in matches}
        neighbors: dict[str, dict] = {}
        edges: dict[str, dict] = {}
        for match in matches:
            context = self.context(match["id"])
            for neighbor in context.get("neighbors", []):
                if neighbor["id"] not in match_ids:
                    neighbors.setdefault(neighbor["id"], neighbor)
            for edge in context.get("edges", []):
                edges.setdefault(edge_key(edge), edge)
        return {
            "query": term,
            "matches": matches,
            "neighbors": [neighbors[nid] for nid in sorted(neighbors)],
            "edges": sorted_edges(edges.values()),
        }

    def _adjacent(self, node_id: str) -> list[tuple[str, dict]]:
        adjacent: dict[str, tuple[str, dict]] = {}

        for edge in self._root_edges_for(node_id):
            other_id = edge["to"] if edge["from"] == node_id else edge["from"]
            if self.get_node(other_id) is None:
                continue
            decorated = self._decorate_edge(edge)
            adjacent.setdefault(
                f"{other_id}|{edge_key(decorated)}",
                (other_id, decorated),
            )

        parts = split_prefixed_id(node_id)
        if parts is None:
            return [adjacent[key] for key in sorted(adjacent)]

        child_name, local_id = parts
        child = self._load_child(child_name)
        if not isinstance(child, (Graph, SqliteBackedGraph)):
            return [adjacent[key] for key in sorted(adjacent)]

        for edge in _child_edges_for(child, local_id):
            if edge["from"] == local_id:
                other_local = edge["to"]
            elif edge["to"] == local_id:
                other_local = edge["from"]
            else:
                continue
            other_id = prefix_node_id(child_name, other_local)
            if self.get_node(other_id) is None:
                continue
            prefixed = self._prefix_edge(child_name, edge)
            adjacent.setdefault(
                f"{other_id}|{edge_key(prefixed)}",
                (other_id, prefixed),
            )

        return [adjacent[key] for key in sorted(adjacent)]

    def _load_child(self, name: str) -> LoadedChild:
        """Return a child handle, preferring the lazy sqlite path (ADR 0058).

        Body lives in :mod:`weld.federation_child_loader`; this method
        is the federation-shaped seam tests monkey-patch.
        """
        return _load_child_impl(
            name=name,
            entry=self._children[name],
            workspace_root=self._root,
            sentinel_cache=self._sentinel_cache,
            child_cache=self._child_cache,
            sqlite_cache=self._sqlite_cache,
            read_bytes=self._read_graph_bytes,
        )

    def _load_child_for_query(self, name: str) -> LoadedChild:
        """JSON-backed :class:`Graph` fallback for :meth:`query`.

        Used when the sidecar is missing/stale so the alias index and
        OR-fallback path are available. Sqlite-backed children run
        ``SqliteBackedGraph.query`` instead (ADR 0058 Option B).
        """
        return _load_child_for_query_impl(
            name=name,
            entry=self._children[name],
            workspace_root=self._root,
            sentinel_cache=self._sentinel_cache,
            child_cache=self._child_cache,
            read_bytes=self._read_graph_bytes,
        )

    def _canonicalize_node_id(self, node_id: str) -> str:
        if UNIT_SEPARATOR in node_id or "::" not in node_id:
            return node_id
        child_name, rest = node_id.split("::", 1)
        if child_name in self._children:
            return prefix_node_id(child_name, rest)
        return node_id

    def _root_edges_for(self, node_id: str) -> list[dict]:
        return [
            edge
            for edge in self._root_edges
            if edge["from"] == node_id or edge["to"] == node_id
        ]

    def _prefix_node(self, child_name: str, node: dict) -> dict:
        prefixed = dict(node)
        prefixed["id"] = prefix_node_id(child_name, node["id"])
        return self._decorate_node(prefixed)

    def _prefix_edge(self, child_name: str, edge: dict) -> dict:
        return self._decorate_edge(
            {
                **edge,
                "from": prefix_node_id(child_name, edge["from"]),
                "to": prefix_node_id(child_name, edge["to"]),
            }
        )

    def _decorate_node(self, node: dict) -> dict:
        decorated = dict(node)
        decorated["display_id"] = render_display_id(str(decorated["id"]))
        return decorated

    def _decorate_edge(self, edge: dict) -> dict:
        decorated = dict(edge)
        decorated["from_display"] = render_display_id(str(decorated["from"]))
        decorated["to_display"] = render_display_id(str(decorated["to"]))
        return decorated

    def _graph_rel_path(self, entry: ChildEntry) -> str:
        return (Path(entry.path) / ".weld" / "graph.json").as_posix()

    def _read_graph_bytes(self, graph_path: Path) -> bytes:
        """Read JSON bytes for *graph_path* (test seam for TOCTOU/cache patches)."""
        return graph_path.read_bytes()
