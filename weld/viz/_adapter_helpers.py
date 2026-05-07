"""Pure helpers backing :mod:`weld.viz.adapter`.

Cytoscape element shaping, edge ID hashing, and the stable overview-priority
ordering live here so :mod:`weld.viz.adapter` can stay focused on the
graph-shaped public API. These helpers are deliberately private to the
``weld.viz`` package -- callers should always go through ``adapter``.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from typing import Iterable

from weld.viz import VIZ_API_VERSION

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


def node_element(node_id: str, node: dict, degree: int) -> dict:
    """Return a Cytoscape-shaped node element for *node_id*."""
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
        "classes": f"type-{css_token(node.get('type') or 'unknown')}",
    }


def edge_element(edge: dict) -> dict:
    """Return a Cytoscape-shaped edge element for *edge*."""
    edge_type = edge.get("type") or "relates_to"
    return {
        "data": {
            "id": edge_id(edge),
            "source": edge["from"],
            "target": edge["to"],
            "type": edge_type,
            "label": edge_type,
            "props": copy.deepcopy(edge.get("props", {}) or {}),
            "from_display": edge.get("from_display") or edge["from"],
            "to_display": edge.get("to_display") or edge["to"],
        },
        "classes": f"type-{css_token(edge_type)}",
    }


def degree_by_node(edges: list[dict]) -> dict[str, int]:
    """Return undirected degree per node id."""
    degree: Counter[str] = Counter()
    for edge in edges:
        degree[edge["from"]] += 1
        degree[edge["to"]] += 1
    return dict(degree)


def overview_key(node_id: str, node: dict, degree: dict[str, int]) -> tuple:
    """Stable sort key for the overview slice (priority, -degree, id)."""
    priority = _NODE_TYPE_PRIORITY.get(node.get("type", ""), 10)
    return (priority, -degree.get(node_id, 0), node_id)


def edge_id(edge: dict) -> str:
    """Deterministic hash-based id for *edge*."""
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


def dedupe(values: Iterable[str]) -> list[str]:
    """Return *values* with order preserved and duplicates dropped."""
    out = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def css_token(value: str) -> str:
    """Lowercase *value* with non-alphanumeric chars replaced by ``-``."""
    return "".join(ch if ch.isalnum() else "-" for ch in value.lower())


def empty_payload(warnings: list[str]) -> dict:
    """Return the canonical empty Cytoscape payload."""
    return {
        "viz_api_version": VIZ_API_VERSION,
        "elements": {"nodes": [], "edges": []},
        "stats": {"total_nodes": 0, "total_edges": 0, "visible_nodes": 0, "visible_edges": 0},
        "truncated": {"nodes": False, "edges": False},
        "focus_ids": [],
        "warnings": warnings,
    }


__all__ = [
    "css_token",
    "dedupe",
    "degree_by_node",
    "edge_element",
    "edge_id",
    "empty_payload",
    "node_element",
    "overview_key",
]
