"""Self-consistency projection for the ``wd query`` ``--json`` envelope.

When ``wd query`` runs without ``--include-speculative`` the CLI drops
unresolved-symbol sentinels (``origin=unresolved``) from ``matches`` (see
:func:`weld.ranking.filter_speculative_matches`). ``Graph.query`` however
computed ``neighbors``/``edges`` over the *pre-filter* match set, so the raw
envelope is a superset of the surviving result: an edge may reference a
dropped match id, and a neighbour may be the 1-hop of a dropped match only.
Text output never renders edges, so this is invisible there -- but a
``--json`` consumer doing strict graph reconstruction would see dangling edge
endpoints and orphan neighbours.

:func:`trim_envelope_to_matches` projects the envelope back to a
self-consistent shape (exactly ``compute_neighborhood`` over the surviving
match ids). It lives in its own module rather than in ``weld.ranking`` so the
ranking core stays focused on scoring and within the 400-line cap.
"""
from __future__ import annotations


def trim_envelope_to_matches(envelope: dict, surviving_ids: set[str]) -> dict:
    """Project a query *envelope* onto the *surviving* (kept) match ids.

    Pass the **pre-filter** envelope (full ``matches``) plus the set of match
    ids that survived :func:`weld.ranking.filter_speculative_matches`.
    ``Graph.query`` built ``neighbors``/``edges`` as the 1-hop neighbourhood of
    the *pre-filter* match set (``graph._neighborhood`` ->
    :func:`weld.graph_context.compute_neighborhood`), so once sentinels are
    dropped that neighbourhood is a superset of the result -- some edges
    reference a dropped match id, some neighbours are 1-hop of a dropped match
    only -- making the ``wd query --json`` envelope self-inconsistent.

    This reproduces ``compute_neighborhood(surviving_ids)`` without re-walking
    the source graph, since the envelope already holds every edge touching any
    pre-filter match: ``matches`` is filtered to ``surviving_ids`` (order
    preserved); an edge is kept iff an endpoint is a surviving match id;
    ``neighbors`` is rebuilt as the kept-edge endpoints minus ``surviving_ids``
    (sorted). Node dicts come from the *pre-filter* ``matches`` and
    ``neighbors`` so a former match demoted to a neighbour (an edge from a
    surviving match to a dropped sentinel) keeps its full node rather than
    leaving a dangling edge. Returns a new envelope dict; all other keys
    (``query``, ``degraded_match``, ``freshness``, ...) pass through unchanged.
    """
    matches = [
        m for m in (envelope.get("matches") or []) if m.get("id") in surviving_ids
    ]
    kept_edges = [
        e for e in (envelope.get("edges") or [])
        if e.get("from") in surviving_ids or e.get("to") in surviving_ids
    ]
    by_id: dict[str, dict] = {}
    for node in (envelope.get("neighbors") or []) + (envelope.get("matches") or []):
        nid = node.get("id")
        if nid is not None:
            by_id.setdefault(nid, node)
    neighbor_ids: set[str] = set()
    for e in kept_edges:
        neighbor_ids.update((e.get("from"), e.get("to")))
    neighbor_ids -= surviving_ids
    neighbor_ids.discard(None)
    neighbors = [by_id[nid] for nid in sorted(neighbor_ids) if nid in by_id]
    return {
        **envelope,
        "matches": matches,
        "neighbors": neighbors,
        "edges": kept_edges,
    }
