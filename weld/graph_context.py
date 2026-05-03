"""Fallback helpers for ``Graph.context`` and ``FederatedGraph.context``.

tracked issue: when ``context`` is called with a
free-form string that is not an exact node id, call ``query`` with limit 1
and, if a match is returned, surface the matched node's context plus an
envelope ``resolved_from`` so callers can detect fallback fired.

The helper lives in its own module so the behaviour can be shared by both
``weld.graph.Graph`` and ``weld.federation.FederatedGraph`` without
inflating either file past the 400-line cap.
"""

from __future__ import annotations

from typing import Callable


def compute_neighborhood(
    nodes: dict, edges: list, node_ids: set[str],
) -> tuple[list[dict], list[dict]]:
    """Return ``(neighbors, edges)`` for the 1-hop expansion of ``node_ids``.

    Pure: no Graph reference. Pulled out of ``Graph._neighborhood`` to
    keep ``weld/graph.py`` under its 400-line cap; ``Graph._neighborhood``
    now delegates here.
    """
    out_edges = []
    neighbor_ids: set[str] = set()
    for e in edges:
        if e["from"] in node_ids or e["to"] in node_ids:
            out_edges.append(e)
            neighbor_ids.add(e["from"])
            neighbor_ids.add(e["to"])
    neighbor_ids -= node_ids
    neighbors = []
    for nid in sorted(neighbor_ids):
        n = nodes.get(nid)
        if n:
            neighbors.append({"id": nid, **n})
    return neighbors, out_edges


def simple_exact_context(get_node, neighborhood, node_id: str) -> dict | None:
    """Build the exact-match payload for a plain ``Graph``.

    Returns ``None`` when ``node_id`` is not present so the caller can
    drop into the query-fallback path.
    """
    node = get_node(node_id)
    if node is None:
        return None
    neighbors, edges = neighborhood({node_id})
    return {"node": node, "neighbors": neighbors, "edges": edges}


def context_with_fallback(
    *,
    raw_node_id: str,
    error_node_id: str,
    fallback: bool,
    exact_fn: Callable[[], dict | None],
    query_fn: Callable[[str, int], dict],
    recurse_fn: Callable[[str], dict],
    match_tokens_fn: Callable[[list[str], str, dict], int],
) -> dict:
    """Return a context payload, resolving via ``query`` on a miss.

    ``exact_fn`` returns the exact-match context payload if ``raw_node_id``
    maps to a stored node, else ``None``. ``query_fn`` mirrors
    ``Graph.query``. ``recurse_fn`` must be the ``fallback=False`` variant
    of the caller's own ``context`` so a matched id is resolved without
    re-running the fallback.

    ``error_node_id`` is the canonical form surfaced in the ``error``
    message when the fallback fails (for ``FederatedGraph`` this is the
    canonicalized id; for ``Graph`` it is the raw id).
    """
    exact = exact_fn()
    if exact is not None:
        return exact
    if not fallback:
        return {"error": f"node not found: {error_node_id}"}
    matches = (query_fn(raw_node_id, 1).get("matches") or [])
    if not matches:
        return {"error": f"node not found: {error_node_id}"}
    top = matches[0]
    matched_id = top["id"]
    resolved = recurse_fn(matched_id)
    if "error" in resolved:
        # The matched node disappeared between query and context lookup;
        # surface the original miss rather than the inner error.
        return {"error": f"node not found: {error_node_id}"}
    resolved["resolved_from"] = {
        "query": raw_node_id,
        "matched_id": matched_id,
        "score": match_tokens_fn(raw_node_id.lower().split(), matched_id, top),
    }
    return resolved
