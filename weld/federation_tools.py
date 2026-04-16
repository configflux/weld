"""Federation-aware MCP tool helpers for stale, callers, and references.

Factored out of :mod:`weld.mcp_server` to keep the server module within
the 400-line cap.  Each function accepts a loaded ``FederatedGraph`` and
performs the child fan-out that the single-repo ``Graph`` methods do not
need.
"""

from __future__ import annotations

from weld.federation import FederatedGraph
from weld.federation_support import (
    CorruptChild,
    MissingChild,
    UninitializedChild,
    prefix_node_id,
    render_display_id,
    split_prefixed_id,
)
from weld.graph import Graph


def _prefix_node(child_name: str, node: dict) -> dict:
    """Return a copy of *node* with a federation-prefixed ``id``."""
    prefixed = dict(node)
    prefixed["id"] = prefix_node_id(child_name, node["id"])
    prefixed["display_id"] = render_display_id(str(prefixed["id"]))
    return prefixed


def _prefix_edge(child_name: str, edge: dict) -> dict:
    """Return a copy of *edge* with federation-prefixed endpoints."""
    result = {
        **edge,
        "from": prefix_node_id(child_name, edge["from"]),
        "to": prefix_node_id(child_name, edge["to"]),
    }
    result["from_display"] = render_display_id(str(result["from"]))
    result["to_display"] = render_display_id(str(result["to"]))
    return result


def federated_stale(fg: FederatedGraph) -> dict:
    """Fan out staleness check to the root graph and all children.

    Returns the root stale result augmented with a ``children`` dict
    mapping each child name to its stale result (or a graceful
    degradation payload for non-present children).
    """
    result = fg._root_graph.stale()
    children: dict[str, dict] = {}
    for name in sorted(fg._children):
        child = fg._load_child(name)
        if isinstance(child, Graph):
            children[name] = child.stale()
        elif isinstance(
            child, (MissingChild, UninitializedChild, CorruptChild)
        ):
            payload: dict[str, object] = {"status": child.status}
            if child.error is not None:
                payload["error"] = child.error
            children[name] = payload
    result["children"] = children
    return result


def federated_callers(
    fg: FederatedGraph, symbol_id: str, depth: int = 1,
) -> dict:
    """Return callers of *symbol_id* across root and child graphs.

    If *symbol_id* uses the federation prefix (``child<US>local_id``),
    the search targets that specific child.  Otherwise the root graph
    is searched.
    """
    parts = split_prefixed_id(symbol_id)
    if parts is not None:
        child_name, local_id = parts
        child = fg._load_child(child_name)
        if not isinstance(child, Graph):
            return {
                "symbol": symbol_id,
                "depth": depth,
                "callers": [],
                "edges": [],
                "error": f"child not available: {child_name}",
            }
        raw = child.callers(local_id, depth=depth)
        return {
            "symbol": symbol_id,
            "depth": raw["depth"],
            "callers": [
                _prefix_node(child_name, c)
                for c in raw.get("callers", [])
            ],
            "edges": [
                _prefix_edge(child_name, e)
                for e in raw.get("edges", [])
            ],
        }
    # Unprefixed: search the root graph.
    return fg._root_graph.callers(symbol_id, depth=depth)


def federated_references(
    fg: FederatedGraph, symbol_name: str,
) -> dict:
    """Fan out symbol references across root and all present children.

    Merges matches from every child, prefixing IDs, and aggregates
    callers across all of them.
    """
    all_matches: list[dict] = []
    all_callers: dict[str, dict] = {}
    all_edges: list[dict] = []

    # Root graph.
    root_refs = fg._root_graph.references(symbol_name)
    all_matches.extend(root_refs.get("matches", []))
    for c in root_refs.get("callers", []):
        all_callers.setdefault(c["id"], c)
    all_edges.extend(root_refs.get("edges", []))

    # Children.
    for name in sorted(fg._children):
        child = fg._load_child(name)
        if not isinstance(child, Graph):
            continue
        child_refs = child.references(symbol_name)
        for m in child_refs.get("matches", []):
            all_matches.append(_prefix_node(name, m))
        for c in child_refs.get("callers", []):
            prefixed = _prefix_node(name, c)
            all_callers.setdefault(prefixed["id"], prefixed)
        for e in child_refs.get("edges", []):
            all_edges.append(_prefix_edge(name, e))

    return {
        "symbol": symbol_name,
        "matches": all_matches,
        "callers": list(all_callers.values()),
        "edges": all_edges,
    }
