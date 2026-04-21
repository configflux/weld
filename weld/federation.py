"""Federated workspace query/context/path wrapper."""

from __future__ import annotations

import hashlib
import json
import sys
from collections import deque
from pathlib import Path

from weld.federation_support import (
    ChildGraphCache,
    CorruptChild,
    DEFAULT_CACHE_MAXSIZE,
    LoadedChild,
    MissingChild,
    UninitializedChild,
    edge_key,
    load_graph_bytes,
    prefix_node_id,
    render_display_id,
    sorted_edges,
    split_prefixed_id,
)
from weld.graph import CHILD_SCHEMA_VERSION, Graph, SchemaVersionError
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
        # Sentinel cache for non-graph results (missing/uninitialized/corrupt).
        # These are cheap and not evicted; they do not hold parsed graph data.
        self._sentinel_cache: dict[str, LoadedChild] = {}

    def children_status(self) -> dict[str, dict[str, object]]:
        """Return the current status of every registered child repo."""
        status: dict[str, dict[str, object]] = {}
        for name in sorted(self._children):
            loaded = self._load_child(name)
            if isinstance(loaded, Graph):
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
        """Fan out tokenized search across the root graph and present children."""
        matches: list[dict] = []
        for match in self._root_graph.query(term, limit=limit).get("matches", []):
            matches.append(self._decorate_node(match))
            if len(matches) >= limit:
                return self._query_payload(term, matches)
        for name in sorted(self._children):
            child = self._load_child(name)
            if not isinstance(child, Graph):
                continue
            child_matches = child.query(term, limit=limit).get("matches", [])
            for match in child_matches:
                matches.append(self._prefix_node(name, match))
                if len(matches) >= limit:
                    return self._query_payload(term, matches)
        return self._query_payload(term, matches)

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
            if isinstance(child, Graph):
                child_context = child.context(local_id)
                for neighbor in child_context.get("neighbors", []):
                    prefixed = self._prefix_node(child_name, neighbor)
                    neighbors.setdefault(prefixed["id"], prefixed)
                for edge in child_context.get("edges", []):
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
        """Return a root node or prefixed child node with display metadata."""
        canonical_id = self._canonicalize_node_id(node_id)
        parts = split_prefixed_id(canonical_id)
        if parts is None:
            node = self._root_graph.get_node(canonical_id)
            if node is None:
                return None
            return self._decorate_node(node)

        child_name, local_id = parts
        child = self._load_child(child_name)
        if not isinstance(child, Graph):
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
        if not isinstance(child, Graph):
            return [adjacent[key] for key in sorted(adjacent)]

        for edge in child.dump().get("edges", []):
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
        # Fast path: sentinel (missing/uninit/corrupt) results are cheap.
        sentinel = self._sentinel_cache.get(name)
        if sentinel is not None:
            return sentinel

        entry = self._children[name]
        child_root = self._root / entry.path
        graph_path = child_root / ".weld" / "graph.json"
        graph_rel = self._graph_rel_path(entry)

        if not child_root.is_dir() or not (child_root / ".git").exists():
            loaded: LoadedChild = MissingChild(
                name=name,
                path=entry.path,
                graph_path=graph_rel,
                remote=entry.remote,
            )
            self._sentinel_cache[name] = loaded
            return loaded

        if not graph_path.is_file():
            loaded = UninitializedChild(
                name=name,
                path=entry.path,
                graph_path=graph_rel,
                remote=entry.remote,
            )
            self._sentinel_cache[name] = loaded
            return loaded

        # Read raw bytes and compute sha256 for cache lookup.
        try:
            raw = self._read_graph_bytes(graph_path)
        except OSError as exc:
            loaded = CorruptChild(
                name=name,
                path=entry.path,
                graph_path=graph_rel,
                remote=entry.remote,
                error=f"{type(exc).__name__}: {exc}",
            )
            self._sentinel_cache[name] = loaded
            return loaded

        digest = hashlib.sha256(raw).hexdigest()

        # LRU cache lookup keyed by (name, sha256). On hit the
        # expensive JSON parse + Graph construction is skipped.
        cached_graph = self._child_cache.get(name, digest)
        if cached_graph is not None:
            return cached_graph

        # Cache miss: parse JSON and build the Graph object.
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
            self._sentinel_cache[name] = loaded
            return loaded

        observed = self._graph_digest(graph_path)
        if observed is not None and observed != digest:
            print(
                f"[weld] warning: child graph changed during load: {graph_path}",
                file=sys.stderr,
            )

        graph = Graph(child_root)
        graph._data = data
        graph._build_inverted_index()
        self._child_cache.put(name, digest, graph)
        return graph

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
        return graph_path.read_bytes()

    def _graph_digest(self, graph_path: Path) -> str | None:
        try:
            return hashlib.sha256(self._read_graph_bytes(graph_path)).hexdigest()
        except OSError:
            return None
