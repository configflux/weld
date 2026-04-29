"""Graph-to-browser normalization helpers for ``wd viz``."""

from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter, deque
from typing import Iterable

from weld.viz import VIZ_API_VERSION

DEFAULT_MAX_NODES = 300
DEFAULT_MAX_EDGES = 1500
HARD_MAX_NODES = 2000
HARD_MAX_EDGES = 6000

_NODE_TYPE_PRIORITY = {
    "repo": 0, "platform": 0,
    "service": 1, "agent": 1, "subagent": 1, "workflow": 1,
    "package": 2, "ros_package": 2, "skill": 2, "mcp-server": 2,
    "boundary": 3, "entrypoint": 3, "instruction": 3, "prompt": 3,
    "route": 4, "rpc": 4, "channel": 4, "command": 4, "hook": 4,
    "entity": 5, "contract": 5, "enum": 5, "permission": 5, "scope": 5,
    "file": 8, "config": 8, "tool": 8,
    "symbol": 12,
}


def clamp_limit(raw: str | int | None, default: int, hard: int) -> int:
    """Return a positive integer limit clamped to *hard*."""
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    if value < 1:
        return default
    return min(value, hard)


def graph_counts(data: dict) -> dict:
    """Return summary counts for a graph-shaped dict."""
    nodes = data.get("nodes", {}) or {}
    edges = data.get("edges", []) or []
    return {
        "total_nodes": len(nodes),
        "total_edges": len(edges),
        "nodes_by_type": dict(Counter(n.get("type", "") for n in nodes.values())),
        "edges_by_type": dict(Counter(e.get("type", "") for e in edges)),
    }


def prefix_graph_data(data: dict, child_name: str) -> dict:
    """Return child graph data with globally prefixed node and edge IDs."""
    from weld.federation_support import prefix_node_id, render_display_id

    prefixed_nodes: dict[str, dict] = {}
    for node_id, node in (data.get("nodes", {}) or {}).items():
        prefixed_id = prefix_node_id(child_name, node_id)
        body = copy.deepcopy(node)
        body["display_id"] = render_display_id(prefixed_id)
        prefixed_nodes[prefixed_id] = body

    prefixed_edges = []
    for edge in data.get("edges", []) or []:
        out = copy.deepcopy(edge)
        out["from"] = prefix_node_id(child_name, edge["from"])
        out["to"] = prefix_node_id(child_name, edge["to"])
        out["from_display"] = render_display_id(out["from"])
        out["to_display"] = render_display_id(out["to"])
        prefixed_edges.append(out)

    out = copy.deepcopy(data)
    out["nodes"] = prefixed_nodes
    out["edges"] = prefixed_edges
    return out


def merge_graph_data(graphs: Iterable[dict]) -> dict:
    """Merge graph-shaped dicts without changing node or edge IDs."""
    out = {"meta": {}, "nodes": {}, "edges": []}
    seen_edges: set[str] = set()
    for graph in graphs:
        out["nodes"].update(copy.deepcopy(graph.get("nodes", {}) or {}))
        for edge in graph.get("edges", []) or []:
            key = _edge_id(edge)
            if key in seen_edges:
                continue
            seen_edges.add(key)
            out["edges"].append(copy.deepcopy(edge))
    return out


def neighborhood_from_data(data: dict, node_id: str, depth: int) -> dict:
    """Return a graph slice around *node_id* using undirected BFS."""
    nodes = data.get("nodes", {}) or {}
    edges = data.get("edges", []) or []
    if node_id not in nodes:
        return {"error": f"node not found: {node_id}"}

    adjacency: dict[str, list[str]] = {}
    for edge in edges:
        adjacency.setdefault(edge["from"], []).append(edge["to"])
        adjacency.setdefault(edge["to"], []).append(edge["from"])

    visited = {node_id}
    queue: deque[tuple[str, int]] = deque([(node_id, max(depth, 0))])
    while queue:
        current, remaining = queue.popleft()
        if remaining <= 0:
            continue
        for neighbor in sorted(adjacency.get(current, [])):
            if neighbor in visited:
                continue
            visited.add(neighbor)
            queue.append((neighbor, remaining - 1))

    return {
        "node": {"id": node_id, **nodes[node_id]},
        "neighbors": [{"id": nid, **nodes[nid]} for nid in sorted(visited - {node_id})],
        "edges": [e for e in edges if e["from"] in visited and e["to"] in visited],
    }


def path_from_data(data: dict, from_id: str, to_id: str) -> dict:
    """Return a shortest undirected path from graph-shaped data."""
    nodes = data.get("nodes", {}) or {}
    edges = data.get("edges", []) or []
    if from_id not in nodes or to_id not in nodes:
        return {"path": None, "reason": "node not found"}

    adjacency: dict[str, list[tuple[str, dict]]] = {}
    for edge in edges:
        adjacency.setdefault(edge["from"], []).append((edge["to"], edge))
        adjacency.setdefault(edge["to"], []).append((edge["from"], edge))

    queue: deque[str] = deque([from_id])
    previous: dict[str, tuple[str, dict]] = {}
    visited = {from_id}
    while queue:
        current = queue.popleft()
        if current == to_id:
            break
        for neighbor, edge in sorted(adjacency.get(current, []), key=lambda item: item[0]):
            if neighbor in visited:
                continue
            visited.add(neighbor)
            previous[neighbor] = (current, edge)
            queue.append(neighbor)
    else:
        return {"path": None, "reason": "no path found"}

    path_ids = [to_id]
    path_edges = []
    current = to_id
    while current != from_id:
        parent, edge = previous[current]
        path_ids.append(parent)
        path_edges.append(edge)
        current = parent
    path_ids.reverse()
    path_edges.reverse()
    return {
        "path": [{"id": nid, **nodes[nid]} for nid in path_ids],
        "edges": path_edges,
    }


def normalize_context_result(
    result: dict,
    *,
    max_nodes: int = DEFAULT_MAX_NODES,
    max_edges: int = DEFAULT_MAX_EDGES,
) -> dict:
    """Normalize a ``Graph.context``-shaped result for the browser."""
    if "error" in result:
        return _empty_payload([str(result["error"])])
    anchor = result.get("node")
    records = []
    if anchor:
        records.append(anchor)
    records.extend(result.get("neighbors", []) or [])
    return normalize_records(
        records,
        result.get("edges", []) or [],
        focus_ids=[anchor["id"]] if anchor else [],
        max_nodes=max_nodes,
        max_edges=max_edges,
    )


def normalize_path_result(
    result: dict,
    *,
    max_nodes: int = DEFAULT_MAX_NODES,
    max_edges: int = DEFAULT_MAX_EDGES,
) -> dict:
    """Normalize a ``Graph.path``-shaped result for the browser."""
    if result.get("path") is None:
        return _empty_payload([str(result.get("reason", "no path found"))])
    records = [node for node in result.get("path", []) if node]
    focus_ids = [node["id"] for node in records]
    payload = normalize_records(
        records,
        result.get("edges", []) or [],
        focus_ids=focus_ids,
        max_nodes=max_nodes,
        max_edges=max_edges,
    )
    payload["path"] = focus_ids
    return payload


def normalize_records(
    node_records: list[dict],
    edges: list[dict],
    *,
    focus_ids: list[str] | None = None,
    node_types: set[str] | None = None,
    edge_types: set[str] | None = None,
    max_nodes: int = DEFAULT_MAX_NODES,
    max_edges: int = DEFAULT_MAX_EDGES,
) -> dict:
    """Normalize node records plus edges into a Cytoscape-friendly payload."""
    nodes = {}
    order = []
    for record in node_records:
        node_id = record.get("id")
        if not isinstance(node_id, str):
            continue
        body = {k: copy.deepcopy(v) for k, v in record.items() if k != "id"}
        nodes[node_id] = body
        order.append(node_id)
    data = {"nodes": nodes, "edges": edges}
    return normalize_graph_data(
        data,
        requested_node_ids=order,
        focus_ids=focus_ids or [],
        node_types=node_types,
        edge_types=edge_types,
        max_nodes=max_nodes,
        max_edges=max_edges,
    )


def normalize_graph_data(
    data: dict,
    *,
    requested_node_ids: list[str] | None = None,
    focus_ids: list[str] | None = None,
    node_types: set[str] | None = None,
    edge_types: set[str] | None = None,
    max_nodes: int = DEFAULT_MAX_NODES,
    max_edges: int = DEFAULT_MAX_EDGES,
) -> dict:
    """Return graph data as a small, deterministic browser payload."""
    nodes = data.get("nodes", {}) or {}
    edges = [
        e for e in (data.get("edges", []) or [])
        if edge_types is None or e.get("type") in edge_types
    ]
    degree = _degree_by_node(edges)

    if requested_node_ids is None:
        ordered_ids = sorted(nodes, key=lambda nid: _overview_key(nid, nodes[nid], degree))
    else:
        ordered_ids = _dedupe(requested_node_ids)

    if node_types is not None:
        ordered_ids = [
            nid for nid in ordered_ids
            if nid in nodes and nodes[nid].get("type") in node_types
        ]
    else:
        ordered_ids = [nid for nid in ordered_ids if nid in nodes]

    focus = [nid for nid in _dedupe(focus_ids or []) if nid in nodes]
    selected_ids = _dedupe(focus + ordered_ids)[:max_nodes]
    selected = set(selected_ids)
    eligible_edges = [e for e in edges if e["from"] in selected and e["to"] in selected]
    selected_edges = eligible_edges[:max_edges]

    elements = {
        "nodes": [_node_element(nid, nodes[nid], degree.get(nid, 0)) for nid in selected_ids],
        "edges": [_edge_element(edge) for edge in selected_edges],
    }
    return {
        "viz_api_version": VIZ_API_VERSION,
        "elements": elements,
        "stats": {
            **graph_counts(data),
            "visible_nodes": len(elements["nodes"]),
            "visible_edges": len(elements["edges"]),
        },
        "truncated": {
            "nodes": len(ordered_ids) > len(selected_ids),
            "edges": len(eligible_edges) > len(selected_edges),
            "node_limit": max_nodes,
            "edge_limit": max_edges,
        },
        "focus_ids": focus,
        "warnings": [],
    }


def _node_element(node_id: str, node: dict, degree: int) -> dict:
    props = copy.deepcopy(node.get("props", {}) or {})
    display_id = node.get("display_id") or node_id
    return {
        "data": {
            "id": node_id,
            "display_id": display_id,
            "label": node.get("label") or display_id,
            "type": node.get("type") or "unknown",
            "props": props,
            "file": props.get("file"),
            "degree": degree,
        },
        "classes": f"type-{_css_token(node.get('type') or 'unknown')}",
    }


def _edge_element(edge: dict) -> dict:
    edge_type = edge.get("type") or "relates_to"
    return {
        "data": {
            "id": _edge_id(edge),
            "source": edge["from"],
            "target": edge["to"],
            "type": edge_type,
            "label": edge_type,
            "props": copy.deepcopy(edge.get("props", {}) or {}),
            "from_display": edge.get("from_display") or edge["from"],
            "to_display": edge.get("to_display") or edge["to"],
        },
        "classes": f"type-{_css_token(edge_type)}",
    }


def _degree_by_node(edges: list[dict]) -> dict[str, int]:
    degree: Counter[str] = Counter()
    for edge in edges:
        degree[edge["from"]] += 1
        degree[edge["to"]] += 1
    return dict(degree)


def _overview_key(node_id: str, node: dict, degree: dict[str, int]) -> tuple:
    priority = _NODE_TYPE_PRIORITY.get(node.get("type", ""), 10)
    return (priority, -degree.get(node_id, 0), node_id)


def _edge_id(edge: dict) -> str:
    raw = json.dumps(
        {
            "from": edge.get("from"),
            "to": edge.get("to"),
            "type": edge.get("type"),
            "props": edge.get("props") or {},
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "edge:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _dedupe(values: Iterable[str]) -> list[str]:
    out = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _css_token(value: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in value.lower())


def _empty_payload(warnings: list[str]) -> dict:
    return {
        "viz_api_version": VIZ_API_VERSION,
        "elements": {"nodes": [], "edges": []},
        "stats": {"total_nodes": 0, "total_edges": 0, "visible_nodes": 0, "visible_edges": 0},
        "truncated": {"nodes": False, "edges": False},
        "focus_ids": [],
        "warnings": warnings,
    }
