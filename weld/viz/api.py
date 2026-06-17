"""Read-only local API backing the Weld graph visualizer."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from weld.federation_support import prefix_node_id
from weld.federation import FederatedGraph
from weld.graph import Graph
from weld.trace import trace
from weld.viz import VIZ_API_VERSION
from weld.viz._api_params import (
    child_name as _child_name,
    clean as _clean,
    csv_set as _csv_set,
    error_payload as _error_payload,
    is_child_scope as _is_child_scope,
    required as _required,
    to_int as _int,
)
from weld.viz._diff import load_diff_payload
from weld.viz._git_remote import git_remote_info
from weld.viz._search import (
    substring_match_nodes,
    substring_suggestions,
    suggest_limit,
    top_degree_suggestions,
)
from weld.viz.adapter import (
    DEFAULT_MAX_EDGES,
    DEFAULT_MAX_NODES,
    graph_counts,
    merge_graph_data,
    neighborhood_from_data,
    normalize_context_result,
    normalize_graph_data,
    normalize_path_result,
    normalize_records,
    path_from_data,
    prefix_graph_data,
)
from weld.workspace_state import load_workspace_config


class VizApi:
    """Small read-only API facade for graph visualization."""

    def __init__(self, root: Path | str = ".") -> None:
        self.root = Path(root).resolve()

    def summary(self) -> dict:
        """Return graph metadata, counts, stale status, and available scopes.

        Paths in the payload (``root``, ``graph_path``) are intentionally
        emitted as posix-relative strings rather than absolute filesystem
        paths. Echoing the absolute server-side path in an HTTP response
        leaks environment information (user home, repo checkout location)
        to any client that can reach the viz server, which is undesirable
        even for a local-dev tool.
        """
        graph = self._load_root_graph()
        data = graph.dump()
        config = load_workspace_config(self.root)
        # bd h6z0.13: discover git origin URL + HEAD sha once per summary
        # so the inspector's "open in editor" links can render a remote
        # URL without re-shelling out per node click. Both fields fall
        # back to ``None`` when the repo / remote / git binary is absent.
        # ``abs_root`` is the absolute filesystem path used to build the
        # local ``vscode://file/<abs-path>`` URI; the policy on ``root``
        # / ``graph_path`` (always posix-relative) is unchanged.
        git_info = git_remote_info(self.root)
        payload = {
            "viz_api_version": VIZ_API_VERSION,
            "graph_kind": "code",
            "title": "Weld Graph",
            "root": ".",
            "abs_root": str(self.root),
            "graph_path": ".weld/graph.json",
            "graph_exists": (self.root / ".weld" / "graph.json").is_file(),
            "meta": data.get("meta", {}),
            "stale": graph.stale(),
            "counts": graph_counts(data),
            "scopes": ["root"],
            "remote_url": git_info["remote_url"],
            "head_sha": git_info["head_sha"],
        }
        if config is not None:
            federated = FederatedGraph(self.root)
            children_status = federated.children_status()
            payload["children_status"] = children_status
            payload["scopes"] = ["root", "all"] + [
                f"child:{name}" for name in sorted(children_status)
            ]
        return payload

    def diff(self, _params: dict[str, Any] | None = None) -> dict:
        """Return the structured diff between the current/previous snapshots."""
        return load_diff_payload(self.root)

    def slice(self, params: dict[str, Any]) -> dict:
        """Return an overview, query, or node-centered graph slice."""
        max_nodes = params.get("max_nodes", DEFAULT_MAX_NODES)
        max_edges = params.get("max_edges", DEFAULT_MAX_EDGES)
        node_types = _csv_set(params.get("node_types"))
        edge_types = _csv_set(params.get("edge_types"))
        hide_origins = _csv_set(params.get("hide_origins"))
        scope = str(params.get("scope") or "root")
        query = _clean(params.get("q"))
        node_id = _clean(params.get("node_id"))
        depth = _int(params.get("depth"), 1)

        if node_id:
            return self.context({
                "node_id": node_id,
                "depth": depth,
                "scope": scope,
                "max_nodes": max_nodes,
                "max_edges": max_edges,
            })
        if query:
            return self._query_slice(
                query, scope, node_types=node_types, edge_types=edge_types,
                hide_origins=hide_origins,
                max_nodes=max_nodes, max_edges=max_edges)

        data = self._data_for_scope(scope)
        if "error" in data:
            return _error_payload(str(data["error"]))
        return normalize_graph_data(
            data,
            node_types=node_types,
            edge_types=edge_types,
            hide_origins=hide_origins,
            max_nodes=max_nodes,
            max_edges=max_edges,
        )

    def context(self, params: dict[str, Any]) -> dict:
        """Return a normalized node neighborhood."""
        node_id = _required(params, "node_id")
        scope = str(params.get("scope") or "root")
        depth = _int(params.get("depth"), 1)
        max_nodes = params.get("max_nodes", DEFAULT_MAX_NODES)
        max_edges = params.get("max_edges", DEFAULT_MAX_EDGES)

        if _is_child_scope(scope):
            data = self._data_for_scope(scope)
            if "error" in data:
                return _error_payload(str(data["error"]))
            result = neighborhood_from_data(data, node_id, depth)
            return normalize_context_result(result, max_nodes=max_nodes, max_edges=max_edges)

        graph = self._graph_for_scope("all" if self._is_federated() else "root")
        if depth <= 1:
            return normalize_context_result(
                graph.context(node_id), max_nodes=max_nodes, max_edges=max_edges)
        data = self._data_for_scope("all" if scope == "all" else "root")
        if "error" in data:
            return _error_payload(str(data["error"]))
        return normalize_context_result(
            neighborhood_from_data(data, node_id, depth),
            max_nodes=max_nodes,
            max_edges=max_edges,
        )

    def path(self, params: dict[str, Any]) -> dict:
        """Return a normalized shortest path result."""
        from_id = _required(params, "from_id")
        to_id = _required(params, "to_id")
        scope = str(params.get("scope") or "root")
        max_nodes = params.get("max_nodes", DEFAULT_MAX_NODES)
        max_edges = params.get("max_edges", DEFAULT_MAX_EDGES)
        if _is_child_scope(scope):
            data = self._data_for_scope(scope)
            if "error" in data:
                return _error_payload(str(data["error"]))
            return normalize_path_result(
                path_from_data(data, from_id, to_id),
                max_nodes=max_nodes,
                max_edges=max_edges,
            )
        graph = self._graph_for_scope(scope)
        return normalize_path_result(
            graph.path(from_id, to_id),
            max_nodes=max_nodes,
            max_edges=max_edges,
        )

    def trace(self, params: dict[str, Any]) -> dict:
        """Return a trace result plus a normalized graph slice."""
        scope = str(params.get("scope") or "root")
        graph = self._graph_for_scope(scope)
        term = _clean(params.get("term"))
        node_id = _clean(params.get("node_id"))
        depth = _int(params.get("depth"), 2)
        if not term and not node_id:
            raise ValueError("term or node_id is required")
        result = trace(graph, term=term or None, node_id=node_id or None, depth=depth)
        records = []
        for bucket in ("services", "interfaces", "contracts", "boundaries", "verifications"):
            records.extend(result.get(bucket, []) or [])
        graph_slice = normalize_records(records, result.get("edges", []) or [])
        return {"viz_api_version": VIZ_API_VERSION, "trace": result, "graph": graph_slice}

    def _query_slice(
        self,
        query: str,
        scope: str,
        *,
        node_types: set[str] | None,
        edge_types: set[str] | None,
        hide_origins: set[str] | None = None,
        max_nodes: int,
        max_edges: int,
    ) -> dict:
        if _is_child_scope(scope):
            return self._child_query_slice(
                query, _child_name(scope), node_types=node_types,
                edge_types=edge_types, hide_origins=hide_origins,
                max_nodes=max_nodes, max_edges=max_edges)
        graph = self._graph_for_scope(scope)
        result = graph.query(query, limit=max_nodes)
        matches = list(result.get("matches", []) or [])
        warnings: list[str] = []
        if not matches:
            # bd h6z0.8: tokenized query missed -- fall back to a
            # case-insensitive substring scan over node id + label so
            # an obvious-looking query like "vizapi" or "visualize"
            # still surfaces nodes the tokenizer ranks at zero.
            fallback = substring_match_nodes(
                self._data_for_scope(scope), query, limit=max_nodes,
            )
            if fallback:
                matches = fallback
                warnings.append(
                    "substring fallback: no tokenized hits for "
                    f"{query!r}; showing case-insensitive id/label "
                    "matches instead."
                )
        records = list(matches)
        records.extend(result.get("neighbors", []) or [])
        focus_ids = [node["id"] for node in matches]
        payload = normalize_records(
            records,
            result.get("edges", []) or [],
            focus_ids=focus_ids,
            node_types=node_types,
            edge_types=edge_types,
            hide_origins=hide_origins,
            max_nodes=max_nodes,
            max_edges=max_edges,
        )
        if warnings:
            existing = list(payload.get("warnings", []) or [])
            payload["warnings"] = warnings + existing
        return payload

    def search_suggest(self, params: dict[str, Any]) -> dict:
        """Return autocomplete suggestions for the search input (bd h6z0.8).

        Empty ``q`` -> top-N by degree (empty-state hint). Non-empty ``q``
        -> case-insensitive substring scan over id + label, label hits
        ranked above id-only hits then by ascending label length.
        """
        query = _clean(params.get("q"))
        limit = suggest_limit(params.get("limit"), default=20 if query else 5)
        data = self._data_for_scope("root")
        if not query:
            suggestions = top_degree_suggestions(data, limit)
        else:
            suggestions = substring_suggestions(data.get("nodes", {}) or {}, query, limit)
        return {"viz_api_version": VIZ_API_VERSION, "suggestions": suggestions}

    def _child_query_slice(
        self,
        query: str,
        child_name: str,
        *,
        node_types: set[str] | None,
        edge_types: set[str] | None,
        hide_origins: set[str] | None = None,
        max_nodes: int,
        max_edges: int,
    ) -> dict:
        graph = self._load_child_graph(child_name)
        if graph is None:
            return _error_payload(f"child graph not found: {child_name}")
        result = graph.query(query, limit=max_nodes)
        data = prefix_graph_data(
            {
                "nodes": {
                    node["id"]: {k: v for k, v in node.items() if k != "id"}
                    for node in (result.get("matches", []) or []) + (result.get("neighbors", []) or [])
                },
                "edges": result.get("edges", []) or [],
            },
            child_name,
        )
        focus_ids = [
            prefix_node_id(child_name, node["id"])
            for node in result.get("matches", []) or []
        ]
        return normalize_graph_data(
            data,
            requested_node_ids=list(data["nodes"]),
            focus_ids=focus_ids,
            node_types=node_types,
            edge_types=edge_types,
            hide_origins=hide_origins,
            max_nodes=max_nodes,
            max_edges=max_edges,
        )

    def _graph_for_scope(self, scope: str) -> Graph | FederatedGraph:
        if scope == "all" and self._is_federated():
            return FederatedGraph(self.root)
        if _is_child_scope(scope):
            return self._load_child_graph(_child_name(scope))
        return self._load_root_graph()

    def _data_for_scope(self, scope: str) -> dict:
        if scope == "all" and self._is_federated():
            return self._all_data()
        if _is_child_scope(scope):
            child_name = _child_name(scope)
            graph = self._load_child_graph(child_name)
            if graph is None:
                return {"error": f"child graph not found: {child_name}"}
            return prefix_graph_data(graph.dump(), child_name)
        return self._root_scope_data()

    def _root_scope_data(self) -> dict:
        graph = self._load_root_graph()
        data = copy.deepcopy(graph.dump())
        if not self._is_federated():
            return data
        nodes = data.setdefault("nodes", {})
        fed = FederatedGraph(self.root)
        for edge in data.get("edges", []) or []:
            for key in ("from", "to"):
                node_id = edge.get(key)
                if not isinstance(node_id, str) or node_id in nodes:
                    continue
                node = fed.get_node(node_id)
                if node is not None:
                    nodes[node_id] = {k: v for k, v in node.items() if k != "id"}
        return data

    def _all_data(self) -> dict:
        graphs = [self._root_scope_data()]
        config = load_workspace_config(self.root)
        if config is None:
            return graphs[0]
        for child in sorted(config.children, key=lambda c: c.name):
            graph = self._load_child_graph(child.name)
            if graph is not None:
                graphs.append(prefix_graph_data(graph.dump(), child.name))
        return merge_graph_data(graphs)

    def _load_root_graph(self) -> Graph:
        graph = Graph(self.root)
        graph.load()
        return graph

    def _load_child_graph(self, child_name: str) -> Graph | None:
        config = load_workspace_config(self.root)
        if config is None:
            return None
        child = next((entry for entry in config.children if entry.name == child_name), None)
        if child is None:
            return None
        child_root = self.root / child.path
        if not (child_root / ".weld" / "graph.json").is_file():
            return None
        graph = Graph(child_root)
        graph.load()
        return graph

    def _is_federated(self) -> bool:
        return load_workspace_config(self.root) is not None
