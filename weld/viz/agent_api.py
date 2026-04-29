"""Read-only API backing Agent Graph visualization."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from weld.agent_graph_storage import load_agent_graph
from weld.viz import VIZ_API_VERSION
from weld.viz.adapter import (
    DEFAULT_MAX_EDGES,
    DEFAULT_MAX_NODES,
    graph_counts,
    neighborhood_from_data,
    normalize_context_result,
    normalize_graph_data,
    normalize_path_result,
    normalize_records,
    path_from_data,
)

_QUERY_PROP_KEYS = ("file", "name", "description", "platform", "platform_name")


class AgentVizApi:
    """Small read-only API facade for persisted Agent Graph visualization."""

    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root).resolve()

    def ensure_available(self) -> None:
        """Raise a clear error when the Agent Graph has not been discovered."""
        load_agent_graph(self.root)

    def summary(self) -> dict:
        """Return Agent Graph metadata, counts, and available scopes."""
        data = self._load_data()
        return {
            "viz_api_version": VIZ_API_VERSION,
            "graph_kind": "agent",
            "title": "Weld Agent Graph",
            "root": ".",
            "graph_path": ".weld/agent-graph.json",
            "graph_exists": (self.root / ".weld" / "agent-graph.json").is_file(),
            "meta": data.get("meta", {}),
            "stale": None,
            "counts": graph_counts(data),
            "scopes": ["root"],
        }

    def slice(self, params: dict[str, Any]) -> dict:
        """Return an overview, query, or node-centered Agent Graph slice."""
        scope = str(params.get("scope") or "root")
        if scope != "root":
            return _error_payload("Agent Graph visualization supports only root scope")
        max_nodes = params.get("max_nodes", DEFAULT_MAX_NODES)
        max_edges = params.get("max_edges", DEFAULT_MAX_EDGES)
        node_types = _csv_set(params.get("node_types"))
        edge_types = _csv_set(params.get("edge_types"))
        query = _clean(params.get("q"))
        node_id = _clean(params.get("node_id"))
        depth = _int(params.get("depth"), 1)

        if node_id:
            return self.context({
                "node_id": node_id,
                "depth": depth,
                "max_nodes": max_nodes,
                "max_edges": max_edges,
            })
        if query:
            return self._query_slice(
                query,
                node_types=node_types,
                edge_types=edge_types,
                max_nodes=max_nodes,
                max_edges=max_edges,
            )
        return normalize_graph_data(
            self._load_data(),
            node_types=node_types,
            edge_types=edge_types,
            max_nodes=max_nodes,
            max_edges=max_edges,
        )

    def context(self, params: dict[str, Any]) -> dict:
        """Return a normalized Agent Graph node neighborhood."""
        node_id = _required(params, "node_id")
        depth = _int(params.get("depth"), 1)
        return normalize_context_result(
            neighborhood_from_data(self._load_data(), node_id, depth),
            max_nodes=params.get("max_nodes", DEFAULT_MAX_NODES),
            max_edges=params.get("max_edges", DEFAULT_MAX_EDGES),
        )

    def path(self, params: dict[str, Any]) -> dict:
        """Return a normalized shortest path over the Agent Graph."""
        return normalize_path_result(
            path_from_data(
                self._load_data(),
                _required(params, "from_id"),
                _required(params, "to_id"),
            ),
            max_nodes=params.get("max_nodes", DEFAULT_MAX_NODES),
            max_edges=params.get("max_edges", DEFAULT_MAX_EDGES),
        )

    def trace(self, params: dict[str, Any]) -> dict:
        """Agent Graph visualization does not expose protocol trace slices."""
        raise ValueError("trace is not supported for Agent Graph visualization")

    def _query_slice(
        self,
        query: str,
        *,
        node_types: set[str] | None,
        edge_types: set[str] | None,
        max_nodes: int,
        max_edges: int,
    ) -> dict:
        data = self._load_data()
        nodes = data.get("nodes", {}) or {}
        tokens = [part.casefold() for part in query.split() if part.strip()]
        matches = [
            {"id": node_id, **node}
            for node_id, node in sorted(nodes.items())
            if _node_matches(tokens, node_id, node)
        ]
        match_ids = {node["id"] for node in matches}
        neighbor_ids: set[str] = set()
        related_edges = []
        for edge in data.get("edges", []) or []:
            if edge_types is not None and edge.get("type") not in edge_types:
                continue
            from_id = edge.get("from")
            to_id = edge.get("to")
            if from_id not in match_ids and to_id not in match_ids:
                continue
            related_edges.append(edge)
            if isinstance(from_id, str) and from_id not in match_ids:
                neighbor_ids.add(from_id)
            if isinstance(to_id, str) and to_id not in match_ids:
                neighbor_ids.add(to_id)
        neighbors = [
            {"id": node_id, **nodes[node_id]}
            for node_id in sorted(neighbor_ids)
            if node_id in nodes
        ]
        return normalize_records(
            matches + neighbors,
            related_edges,
            focus_ids=[node["id"] for node in matches],
            node_types=node_types,
            edge_types=edge_types,
            max_nodes=max_nodes,
            max_edges=max_edges,
        )

    def _load_data(self) -> dict:
        return load_agent_graph(self.root)


def _node_matches(tokens: list[str], node_id: str, node: dict) -> bool:
    if not tokens:
        return True
    props = node.get("props") if isinstance(node.get("props"), dict) else {}
    fields = [node_id, node.get("label", ""), node.get("type", "")]
    fields.extend(str(props.get(key) or "") for key in _QUERY_PROP_KEYS)
    haystack = "\n".join(str(field) for field in fields).casefold()
    return all(token in haystack for token in tokens)


def _csv_set(raw: object) -> set[str] | None:
    if raw in (None, ""):
        return None
    values = [part.strip() for part in str(raw).split(",")]
    return {value for value in values if value} or None


def _clean(raw: object) -> str:
    return str(raw).strip() if raw not in (None, "") else ""


def _int(raw: object, default: int) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _required(params: dict[str, Any], key: str) -> str:
    value = _clean(params.get(key))
    if not value:
        raise ValueError(f"{key} is required")
    return value


def _error_payload(message: str) -> dict:
    payload = normalize_graph_data({"nodes": {}, "edges": []})
    payload["warnings"] = [message]
    return payload
