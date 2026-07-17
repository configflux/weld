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
    degradation payload for non-present children). Uses the JSON
    path explicitly because ``Graph.stale`` depends on
    ``Graph._data['meta']`` which the sqlite handle does not expose
    (ADR 0058 Option A scope).
    """
    result = fg._root_graph.stale()
    children: dict[str, dict] = {}
    for name in sorted(fg._children):
        child = fg._load_child_for_query(name)
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
    is searched. Uses the JSON path because ``Graph.callers`` builds
    a full reverse-adjacency over the edge list -- not yet built from
    sqlite (ADR 0058 Option A scope).
    """
    parts = split_prefixed_id(symbol_id)
    if parts is not None:
        child_name, local_id = parts
        child = fg._load_child_for_query(child_name)
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
    callers across all of them. Uses the JSON path because
    ``Graph.references`` chains into ``Graph.callers`` and
    ``Graph._resolve_symbol_name``, both of which scan the full
    in-memory node/edge list (ADR 0058 Option A scope).
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
        child = fg._load_child_for_query(name)
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


def federated_trace(
    fg: FederatedGraph,
    *,
    term: str | None = None,
    node_id: str | None = None,
    depth: int = 2,
    seed_limit: int = 5,
) -> dict:
    """Run ``trace`` over the read-time flattened federation (ADR 0089).

    Whole-graph interaction BFS needs every child's internal edges, which
    ``FederatedGraph.dump()`` (root meta-graph only) never surfaces. We flatten
    the federation into one in-memory ``Graph`` (child ids prefixed) and hand it
    to the unchanged pure engine, so a child anchor and cross-child interaction
    edges are both in scope. Prefixed federated ids are already canonical, so no
    ADR 0041 alias rewrite is needed here.
    """
    from weld._federation_flatten import flatten_federation
    from weld.trace import trace as _trace

    return _trace(
        flatten_federation(fg),
        term=term,
        node_id=node_id,
        depth=depth,
        seed_limit=seed_limit,
    )


def federated_impact(fg: FederatedGraph, target: str, depth: int = 3) -> dict:
    """Run ``impact`` over the read-time flattened federation (ADR 0089).

    Reverse-dependency BFS ("who points at this?") must span child-internal
    edges plus the root's cross-repo edges. The flattened graph unions both, so a
    child-internal dependent and a cross-repo dependent land in the same
    reverse-adjacency map. ``build_index=False``: ``impact`` never queries.
    """
    from weld._federation_flatten import flatten_federation
    from weld.impact_core import impact as _impact

    flat = flatten_federation(fg, build_index=False)
    try:
        return _impact(flat, target=target, depth=depth)
    except ValueError as exc:
        return {"error": str(exc)}
