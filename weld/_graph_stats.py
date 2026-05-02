"""Summary-statistics helpers for :class:`weld.graph.Graph`.

Extracted from ``weld/graph.py`` so the core file stays under the 400-line
cap (see AGENTS.md / CLAUDE.md line-count policy). The logic here is
intentionally small and pure: it takes the raw ``_data`` payload and
returns a plain dict, so both :meth:`weld.graph.Graph.stats` and the
``wd stats`` CLI path in :mod:`weld._graph_cli` can share it without
carrying a :class:`~weld.graph.Graph` dependency into test fixtures.

PM audit (tracked issue) requires ``wd stats`` to cover:

- counts by node/edge type (``nodes_by_type`` / ``edges_by_type``)
- description coverage (missing-docs signal)
- top-N most-connected nodes (``top_authority_nodes``)
- graph staleness (surfaced by the CLI via :meth:`Graph.stale`)
- workspace child breakdown for polyrepo roots (surfaced by the CLI via
  :mod:`weld.workspace_state`)

This module owns the first three bullets; the CLI layer composes the
other two on top.
"""

from __future__ import annotations

from typing import Any, Mapping

_TOP_AUTHORITY_LIMIT = 5


def compute_stats(
    data: Mapping[str, Any],
    *,
    top: int | None = None,
) -> dict:
    """Return the core stats payload for a raw graph ``data`` mapping.

    ``top`` controls how many entries are included in
    ``top_authority_nodes``. ``None`` (the default) keeps the historical
    cap of five so existing consumers and fixtures stay green. Callers
    that want a wider window (e.g. ``wd stats --top 20`` on a large
    graph) pass an explicit positive integer; the resolved cap is also
    surfaced in the returned ``top`` field so JSON consumers can label
    "Top N" output without rejoining against argv.

    The payload is additive-only: new fields are appended; existing keys
    (``total_nodes``, ``total_edges``, ``nodes_by_type``, ``edges_by_type``,
    ``nodes_with_description``, ``description_coverage_pct``,
    ``description_coverage_by_type``, ``top_authority_nodes``) remain in
    place for backward compatibility with consumers and fixtures that
    pin them.
    """
    limit = _TOP_AUTHORITY_LIMIT if top is None else int(top)
    nodes = data.get("nodes") or {}
    edges = data.get("edges") or []
    nc: dict[str, int] = {}
    dc: dict[str, int] = {}  # described count per type
    for n in nodes.values():
        t = n["type"]
        nc[t] = nc.get(t, 0) + 1
        desc = (n.get("props") or {}).get("description")
        if desc and isinstance(desc, str) and desc.strip():
            dc[t] = dc.get(t, 0) + 1
    ec: dict[str, int] = {}
    for e in edges:
        ec[e["type"]] = ec.get(e["type"], 0) + 1
    total = len(nodes)
    desc_total = sum(dc.values())
    cov_by_type = {
        t: {
            "total": nc[t],
            "with_description": dc.get(t, 0),
            "coverage_pct": round(dc.get(t, 0) / nc[t] * 100, 2),
        }
        for t in nc
    }
    return {
        "total_nodes": total,
        "total_edges": len(edges),
        "nodes_by_type": nc,
        "edges_by_type": ec,
        "nodes_with_description": desc_total,
        "description_coverage_pct":
            round(desc_total / total * 100, 2) if total else 0.0,
        "description_coverage_by_type": cov_by_type,
        "top_authority_nodes": top_authority_nodes(
            nodes, edges, limit=limit,
        ),
        "top": limit,
    }


def top_authority_nodes(
    nodes: Mapping[str, dict],
    edges: list[dict] | tuple[dict, ...],
    *,
    limit: int,
) -> list[dict]:
    """Return the top-``limit`` nodes ranked by total degree.

    "Authority" here is the simple, explainable signal: number of edges
    incident to a node (in_degree + out_degree). Ties are broken by node
    id ascending so the output is deterministic across runs -- important
    for demo/confidence commands that reviewers compare manually.

    Edges referencing unknown nodes (hand-edited payloads, partial
    imports) are counted toward the *known* endpoint only; the missing
    endpoint is skipped, so ``wd stats`` stays robust on imperfect graphs
    without fabricating phantom entries.
    """
    if not nodes:
        return []
    in_deg: dict[str, int] = {}
    out_deg: dict[str, int] = {}
    for edge in edges:
        src = edge.get("from")
        dst = edge.get("to")
        if src in nodes:
            out_deg[src] = out_deg.get(src, 0) + 1
        if dst in nodes:
            in_deg[dst] = in_deg.get(dst, 0) + 1
    ranked = sorted(
        (
            (node_id, node) for node_id, node in nodes.items()
            if not _is_unresolved_symbol(node_id, node)
        ),
        key=lambda item: (
            -(in_deg.get(item[0], 0) + out_deg.get(item[0], 0)),
            item[0],
        ),
    )
    entries: list[dict] = []
    for node_id, node in ranked[:limit]:
        entries.append({
            "id": node_id,
            "label": node.get("label", node_id),
            "type": node.get("type", ""),
            "in_degree": in_deg.get(node_id, 0),
            "out_degree": out_deg.get(node_id, 0),
            "degree": in_deg.get(node_id, 0) + out_deg.get(node_id, 0),
        })
    return entries


def _is_unresolved_symbol(node_id: str, node: dict) -> bool:
    return (
        node_id.startswith("symbol:unresolved:")
        or (
            node.get("type") == "symbol"
            and (node.get("props") or {}).get("resolved") is False
        )
    )
