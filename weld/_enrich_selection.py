"""What needs enrichment -- the oracle both enrichment paths read.

``wd enrich`` has two paths: the provider-backed loop
(:func:`weld.enrich.run_enrichment`) and the agent-direct work plan
(:mod:`weld._enrich_agent_direct`, ADR 0098). They must agree on which
nodes need work and in what order, or an agent following the plan would
enrich a different set than the provider would -- and neither surface
could be trusted as a progress report on the other.

So the selection lives here, in one module both import, the same
single-sourcing discipline ADR 0079 applies to the enrichment
fingerprint and ADR 0077 to the edge-sort key.

Three levels, narrowing:

* :func:`selected_node_ids` -- the *candidate* set and its order: one
  node when the caller named one (resolved through the ADR 0041 alias
  index, so a legacy id still names its node), otherwise every node
  sorted by ``(type, id)``. Both paths walk this.
* :func:`scoped_node_ids` -- the candidates a type filter leaves. Kept
  distinct from the next level so the caller can tell "nothing matched
  your filter" from "everything matched is already done" -- two very
  different answers that a single empty list would conflate.
* :func:`pending_node_ids` -- the scoped nodes that still *need* work,
  i.e. those without a structurally-valid enrichment record. This is the
  provider-independent form of the provider loop's skip test, which
  additionally requires the stored provider/model/fingerprint to match
  the run it is part of. An agent has no provider identity to compare
  against, and a structurally-valid record is exactly what discovery
  preserves, so validity is the right gate for the agent-direct path.
"""

from __future__ import annotations

from weld._alias_index import resolve_id
from weld.enrichment_persistence import valid_enrichment


def selected_node_ids(graph, node_id: str | None = None) -> list[str]:
    """Return the candidate node ids for an enrichment run, in order.

    A named *node_id* narrows the run to that node and raises
    :class:`ValueError` when it is unknown -- callers surface that as a
    CLI error rather than silently enriching nothing. Otherwise every
    node is a candidate, ordered by ``(type, id)`` so runs are
    reproducible and a batched caller can resume deterministically.

    The named id is read through the ADR 0041 alias index, so an id
    pasted from an older transcript still names the node it named then.
    Resolving *here* rather than at either surface is what makes the
    behaviour uniform: this is the one door a named id passes through on
    the way to both the provider loop and the agent-direct plan, on both
    the CLI and MCP. Rewriting at a surface is how it drifted before --
    ``weld_enrich`` resolved, ``wd enrich`` did not.

    :func:`weld._alias_index.resolve_id` owns the resolution rules, so a
    canonical id outranks any alias claiming it (an alias must never
    shadow a real node -- enrichment writes to what it selects) and an
    alias whose target is gone is a miss rather than a dangling pointer.
    An unresolvable id keeps its original spelling in the error, which is
    what the caller typed and can correct.
    """
    nodes = graph.dump().get("nodes", {})
    if node_id is not None:
        # The index is built by Graph.load() alongside the query state;
        # default to empty so a caller holding a hand-built graph still
        # resolves canonical ids.
        alias_index = getattr(graph, "_alias_index", None) or {}
        canonical_id = resolve_id(node_id, nodes, alias_index)
        if canonical_id is None:
            raise ValueError(f"node not found: {node_id}")
        return [canonical_id]
    return sorted(nodes, key=lambda nid: (nodes[nid].get("type", ""), nid))


def scoped_node_ids(
    graph, *, node_id: str | None = None, node_type: str | None = None,
) -> list[str]:
    """Return the candidates a *node_type* filter leaves, in selection order.

    Everything the caller asked to consider, enriched or not. An empty
    result means the filters matched nothing -- which is why it is worth
    knowing separately from an empty :func:`pending_node_ids`.
    """
    nodes = graph.dump().get("nodes", {})
    return [
        candidate_id
        for candidate_id in selected_node_ids(graph, node_id)
        if node_type is None or (nodes.get(candidate_id) or {}).get("type") == node_type
    ]


def pending_node_ids(
    graph,
    *,
    node_id: str | None = None,
    node_type: str | None = None,
    force: bool = False,
) -> list[str]:
    """Return scoped ids that still need enrichment, in selection order.

    A node is pending when its ``props.enrichment`` is not a
    structurally-valid record -- which covers both "never enriched" and
    "holds a half-written record discovery would drop anyway". *force*
    widens the result to every scoped node, matching what ``--force``
    means on the provider path (re-do work already done).
    """
    nodes = graph.dump().get("nodes", {})
    scoped = scoped_node_ids(graph, node_id=node_id, node_type=node_type)
    if force:
        return scoped
    return [
        candidate_id
        for candidate_id in scoped
        if not valid_enrichment(
            ((nodes.get(candidate_id) or {}).get("props") or {}).get("enrichment")
        )
    ]


__all__ = ["selected_node_ids", "scoped_node_ids", "pending_node_ids"]
