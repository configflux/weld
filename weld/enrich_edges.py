"""Cross-repo edge enrichment orchestration.

Extends the enrichment pipeline to handle edges that cross repository
boundaries in a federated workspace. While node enrichment (in
:mod:`weld.enrich`) adds semantic descriptions to graph nodes, this
module adds descriptions to the typed edges that resolvers emit between
nodes in different child repos.

The provider protocol is the same: ``run_edge_enrichment`` adapts the
edge payload into the ``(node, neighbors, model)`` shape that every
:class:`weld.providers.EnrichmentProvider` expects, so no new provider
code is needed -- existing providers (Anthropic, OpenAI, Ollama) work
unchanged.

Public surface:

* :func:`run_edge_enrichment` -- enrich cross-repo edges in a loaded graph.
"""

from __future__ import annotations

from weld.enrich import (
    ENRICH_VERSION,
    EnrichmentProvider,
    Graph,
    _budget_reached,
    _now,
    _usage_block,
)

_UNIT_SEPARATOR = "\x1f"


def _is_cross_repo_edge(edge: dict) -> bool:
    """Return True when both endpoints carry a federated namespace prefix."""
    from_id = edge.get("from", "")
    to_id = edge.get("to", "")
    return _UNIT_SEPARATOR in from_id and _UNIT_SEPARATOR in to_id


def _valid_cached_edge_enrichment(
    edge: dict,
    *,
    provider_name: str,
    model: str,
) -> bool:
    """Return True when *edge* already carries enrichment from same provider/model."""
    props = edge.get("props") or {}
    enrichment = props.get("enrichment")
    if not isinstance(enrichment, dict):
        return False
    if not isinstance(enrichment.get("provider"), str):
        return False
    if not isinstance(enrichment.get("model"), str):
        return False
    return (
        enrichment["provider"].strip().lower() == provider_name
        and enrichment["model"].strip() == model
    )


def run_edge_enrichment(
    graph: Graph,
    *,
    provider: EnrichmentProvider,
    provider_name: str,
    model: str | None = None,
    force: bool = False,
    max_tokens: int | None = None,
    max_cost: float | None = None,
    persist: bool = True,
) -> dict:
    """Run enrichment on cross-repo edges in *graph*.

    Only edges whose ``from`` and ``to`` IDs both contain the Unit
    Separator (indicating a federated namespace) are considered. Local
    edges within a single repo are never touched.

    The provider receives an edge-shaped payload as the ``node``
    argument (with a synthetic ``id`` field) and the two endpoint nodes
    as ``neighbors``.
    """
    if max_tokens is not None and max_tokens < 0:
        raise ValueError("max_tokens must be >= 0")
    if max_cost is not None and max_cost < 0:
        raise ValueError("max_cost must be >= 0")

    resolved_model = model or provider.DEFAULT_MODEL
    data = graph.dump()
    edges = data.get("edges", [])
    nodes = data.get("nodes", {})

    cross_repo_edges = [
        (i, e) for i, e in enumerate(edges) if _is_cross_repo_edge(e)
    ]

    result: dict = {
        "enrich_version": ENRICH_VERSION,
        "provider": provider_name,
        "model": resolved_model,
        "requested_edges": len(cross_repo_edges),
        "enriched_edges": 0,
        "skipped_edges": 0,
        "errors": [],
        "partial": False,
        "usage": _usage_block(0, 0.0),
    }

    if not cross_repo_edges:
        return result

    tokens_used = 0
    cost_used = 0.0
    changed = False

    for edge_index, edge in cross_repo_edges:
        if _budget_reached(
            tokens_used=tokens_used,
            cost_used=cost_used,
            max_tokens=max_tokens,
            max_cost=max_cost,
        ):
            result["partial"] = True
            break

        if not force and _valid_cached_edge_enrichment(
            edge,
            provider_name=provider_name,
            model=resolved_model,
        ):
            result["skipped_edges"] += 1
            continue

        from_id = edge["from"]
        to_id = edge["to"]
        from_node = nodes.get(from_id)
        to_node = nodes.get(to_id)

        edge_as_node = {
            "id": f"{from_id}->{to_id}",
            "type": edge.get("type", "edge"),
            "label": f"{from_id} -> {to_id}",
            "props": dict(edge.get("props") or {}),
        }
        endpoint_nodes = []
        if from_node is not None:
            endpoint_nodes.append({"id": from_id, **from_node})
        if to_node is not None:
            endpoint_nodes.append({"id": to_id, **to_node})

        try:
            enrichment_result = provider.enrich(
                edge_as_node,
                endpoint_nodes,
                model=resolved_model,
            )
        except Exception as exc:
            result["errors"].append({
                "edge": f"{from_id}->{to_id}",
                "error": str(exc),
            })
            result["partial"] = True
            continue

        props = dict(edge.get("props") or {})
        props["description"] = enrichment_result.description.strip()
        props["enrichment"] = {
            "provider": provider_name,
            "model": resolved_model,
            "timestamp": _now(),
        }
        edge["props"] = props
        edges[edge_index] = edge

        tokens_used += max(int(enrichment_result.tokens_used or 0), 0)
        cost_used += max(float(enrichment_result.cost_usd or 0.0), 0.0)
        result["enriched_edges"] += 1
        result["usage"] = _usage_block(tokens_used, cost_used)
        changed = True

    if persist and changed:
        graph.save()

    return result
