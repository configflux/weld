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

from weld._graph_origin import classify_node
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

#: Curated-overview tuning (ADR 0073). The cold-open architecture slice
#: shows orientation anchors plus a few principal files per package,
#: capped below the 300-node default so the first paint is never
#: truncated.
OVERVIEW_SLICE_LIMIT = 120
OVERVIEW_FILES_PER_PACKAGE = 3

#: Orientation "anchor" node types for the curated overview: every type
#: that carries a low ``overview_key`` priority (<= 5) -- the project's
#: principal surfaces (commands, agents, packages, services, routes,
#: contracts, ...). ``package`` doubles as the membership root for the
#: per-package top-file expansion. These are the types a newcomer needs
#: to see first; bare ``file`` / ``symbol`` nodes are not anchors.
_OVERVIEW_ANCHOR_TYPES = frozenset(
    t for t, p in _NODE_TYPE_PRIORITY.items() if p <= 5
)

#: Anchor types whose nodes are inherently project surfaces but are not
#: ADR-0042 origin-tagged (agent-graph nodes -- commands, agents,
#: workflows, skills, mcp-servers -- classify as ``unresolved`` because
#: they carry no ``props.origin``). The curated slice includes these
#: regardless of origin; file / package anchors still require a
#: ``project`` origin so stdlib / external packages stay out.
_OVERVIEW_ORIGIN_EXEMPT_TYPES = frozenset(
    {"agent", "subagent", "workflow", "command", "skill", "mcp-server",
     "service", "route", "rpc", "channel", "hook", "boundary",
     "entrypoint", "instruction", "prompt"}
)


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


def _is_overview_anchor(node_id: str, node: dict) -> bool:
    """Return True when *node* is an orientation anchor (ADR 0073).

    Anchor types carry an ``overview_key`` priority <= 5. Agent-graph
    anchors (commands / agents / workflows / ...) are included even
    though they classify as ``unresolved`` (no ADR-0042 origin tag);
    ``file`` / ``package`` anchors must be ``project`` origin so stdlib
    and external packages never reach the curated cold open.
    """
    node_type = node.get("type", "")
    if node_type not in _OVERVIEW_ANCHOR_TYPES:
        return False
    if node_type in _OVERVIEW_ORIGIN_EXEMPT_TYPES:
        return True
    return classify_node({"id": node_id, **node}) == "project"


def _package_top_files(
    package_id: str,
    nodes: dict,
    edges: list[dict],
    degree: dict[str, int],
    limit: int,
) -> list[str]:
    """Return the top-*limit* project file ids a package ``contains``.

    Membership is the ``contains`` edge emitted package -> file. Files
    are ranked by descending degree then id (stable), and only
    ``project``-origin files qualify so vendored / stdlib file nodes are
    never pulled in by a stdlib package.
    """
    members: list[str] = []
    for edge in edges:
        if edge.get("type") != "contains":
            continue
        child = None
        if edge.get("from") == package_id:
            child = edge.get("to")
        elif edge.get("to") == package_id:
            child = edge.get("from")
        if not isinstance(child, str) or child not in nodes:
            continue
        node = nodes[child]
        if node.get("type") != "file":
            continue
        if classify_node({"id": child, **node}) != "project":
            continue
        members.append(child)
    members = dedupe(members)
    members.sort(key=lambda fid: (-degree.get(fid, 0), fid))
    return members[:limit]


def is_entry_point(node_id: str, node: dict) -> bool:
    """Return True when *node* is an orientation entry point (ADR 0073).

    Used to rank the empty-query ``search-suggest`` set (the inspector's
    cold-open seed and the search dropdown hint). An entry point is any
    ``project``-origin node *or* an agent-graph anchor type (commands,
    agents, workflows, skills, mcp-servers, ...). The agent-graph clause
    is what surfaces CLI commands and MCP tools as entry points even
    though they classify as ``unresolved`` for lack of an origin tag;
    bare ``unresolved`` *symbols* (test-assertion hubs like ``append``)
    are not anchor types, so they stay excluded (bd 123p).
    """
    if node.get("type", "") in _OVERVIEW_ORIGIN_EXEMPT_TYPES:
        return True
    return classify_node({"id": node_id, **node}) == "project"


def has_overview_anchors(nodes: dict) -> bool:
    """Return True when *nodes* contains at least one orientation anchor.

    The adapter uses this to tell a real curated slice (which is
    origin-clean and must bypass the implicit ``unresolved`` strip) from
    the empty-anchor fallback (legacy ``overview_key`` ordering, which
    still wants the strip). See :func:`architecture_overview_ids`.
    """
    return any(_is_overview_anchor(nid, node) for nid, node in nodes.items())


def architecture_overview_ids(
    nodes: dict,
    edges: list[dict],
    *,
    limit: int = OVERVIEW_SLICE_LIMIT,
) -> list[str]:
    """Return the curated cold-open architecture slice (ADR 0073).

    The default ``wd viz`` overview is no longer "top-N nodes by
    ``overview_key`` over the whole graph" (which buried the project's
    principal surfaces under packages + files and stripped the
    agent-graph anchors as ``unresolved``). Instead it is an
    orientation-first slice: every anchor node (commands, agents,
    workflows, project packages, services, routes, ...) ordered by
    ``overview_key``, plus up to :data:`OVERVIEW_FILES_PER_PACKAGE`
    principal files per project package. The whole slice is bounded by
    *limit* (default :data:`OVERVIEW_SLICE_LIMIT`, well under the 300
    node cap) so the cold open is never truncated.

    When the graph has no anchor nodes at all (e.g. a bare symbol-only
    snapshot) the slice falls back to the legacy ``overview_key``
    ordering so the view is never empty.
    """
    degree = degree_by_node(edges)
    anchors = [nid for nid, node in nodes.items() if _is_overview_anchor(nid, node)]
    if not anchors:
        ordered = sorted(nodes, key=lambda nid: overview_key(nid, nodes[nid], degree))
        return ordered[:limit]

    anchors.sort(key=lambda nid: overview_key(nid, nodes[nid], degree))

    selected: list[str] = []
    for anchor_id in anchors:
        selected.append(anchor_id)
        if nodes[anchor_id].get("type") != "package":
            continue
        for file_id in _package_top_files(
            anchor_id, nodes, edges, degree, OVERVIEW_FILES_PER_PACKAGE,
        ):
            selected.append(file_id)
    return dedupe(selected)[:limit]


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
    "OVERVIEW_FILES_PER_PACKAGE",
    "OVERVIEW_SLICE_LIMIT",
    "architecture_overview_ids",
    "css_token",
    "dedupe",
    "degree_by_node",
    "edge_element",
    "edge_id",
    "empty_payload",
    "has_overview_anchors",
    "is_entry_point",
    "node_element",
    "overview_key",
]
