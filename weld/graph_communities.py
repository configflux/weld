"""Deterministic community detection over a Weld graph payload.

Per ADR 0039, community computation runs against a projected subgraph
that excludes unresolved-symbol nodes (call-graph artefacts that act as
universal hubs). Unresolved nodes still appear in ``assignments`` as
singleton communities so the assignment surface stays complete. The
payload exposes a top-level ``hubs`` field listing the highest-degree
nodes in the projection so the documented "report hubs" contract is
honoured.
"""

from __future__ import annotations

from typing import Any, Mapping

from weld.graph_communities_helpers import (
    dominant,
    file_for,
    inc,
    is_unresolved_symbol,
    language_for,
    sorted_counts,
    title_for,
)

_MAX_ITERATIONS = 25
_DEFAULT_EDGE_WEIGHT = 1.0
_EDGE_WEIGHTS = {
    "contains": 4.0,
    "calls": 3.0,
    "invokes": 3.0,
    "implements": 3.0,
    "exposes": 3.0,
    "accepts": 2.5,
    "responds_with": 2.5,
    "depends_on": 2.0,
    "builds": 2.0,
    "configures": 2.0,
    "consumes": 2.0,
    "produces": 2.0,
    "orchestrates": 2.0,
    "tests": 1.5,
    "validates": 1.5,
    "verifies": 1.5,
    "documents": 1.0,
    "references_file": 1.0,
    "relates_to": 0.5,
}


def build_graph_communities(
    data: Mapping[str, Any],
    *,
    top: int = 12,
    stale: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a deterministic community summary for ``.weld/graph.json`` data."""
    limit = max(1, int(top))
    nodes: Mapping[str, dict[str, Any]] = data.get("nodes") or {}
    raw_edges = list(data.get("edges") or [])
    valid_edges, dangling_edges = _split_edges(nodes, raw_edges)
    projected_nodes, projected_edges = _project(nodes, valid_edges)
    adjacency = _weighted_adjacency(projected_nodes, projected_edges)
    labels, iterations = _propagate_labels(projected_nodes, adjacency)
    all_communities, assignments = _summarize_communities(
        nodes, projected_nodes, projected_edges, labels, adjacency, limit=limit,
    )
    hubs = _global_hubs(projected_nodes, adjacency, assignments, limit=limit)
    summary = {
        "total_nodes": len(nodes),
        "total_edges": len(raw_edges),
        "valid_edges": len(valid_edges),
        "projected_nodes": len(projected_nodes),
        "projected_edges": len(projected_edges),
        "dangling_edges": dangling_edges,
        "total_communities": len(all_communities),
        "reported_communities": min(limit, len(all_communities)),
        "top": limit,
    }
    return {
        "meta": {
            "algorithm": "deterministic-weighted-label-propagation",
            "schema_version": 2,
            "max_iterations": _MAX_ITERATIONS,
            "iterations": iterations,
            "projection": "exclude-unresolved-symbols",
            "source_graph_git_sha": (data.get("meta") or {}).get("git_sha"),
            "source_graph_updated_at": (data.get("meta") or {}).get("updated_at"),
        },
        "summary": summary,
        "health": _health_checks(
            nodes, all_communities, assignments, adjacency, dangling_edges, stale,
        ),
        "communities": all_communities[:limit],
        "assignments": assignments,
        "hubs": hubs,
    }


def _split_edges(
    nodes: Mapping[str, dict[str, Any]],
    raw_edges: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    valid: list[dict[str, Any]] = []
    dangling = 0
    for edge in raw_edges:
        if edge.get("from") in nodes and edge.get("to") in nodes:
            valid.append(edge)
        else:
            dangling += 1
    valid.sort(key=lambda e: (e.get("from", ""), e.get("to", ""), e.get("type", "")))
    return valid, dangling


def _project(
    nodes: Mapping[str, dict[str, Any]],
    edges: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Drop unresolved-symbol nodes and any edge incident to them.

    Per ADR 0039 this is the smallest defensible fix that prevents the
    universal-hub collapse on real call graphs.
    """
    projected_nodes: dict[str, dict[str, Any]] = {
        nid: node for nid, node in nodes.items() if not is_unresolved_symbol(nid, node)
    }
    projected_edges: list[dict[str, Any]] = [
        edge for edge in edges
        if edge["from"] in projected_nodes and edge["to"] in projected_nodes
    ]
    return projected_nodes, projected_edges


def _weighted_adjacency(
    nodes: Mapping[str, dict[str, Any]],
    edges: list[dict[str, Any]],
) -> dict[str, dict[str, float]]:
    adjacency = {node_id: {} for node_id in nodes}
    for edge in edges:
        src = edge["from"]
        dst = edge["to"]
        if src == dst:
            continue
        weight = _edge_weight(edge)
        adjacency[src][dst] = adjacency[src].get(dst, 0.0) + weight
        adjacency[dst][src] = adjacency[dst].get(src, 0.0) + weight
    return adjacency


def _propagate_labels(
    nodes: Mapping[str, dict[str, Any]],
    adjacency: Mapping[str, Mapping[str, float]],
) -> tuple[dict[str, str], int]:
    labels = {node_id: node_id for node_id in nodes}
    iterations = 0
    for iterations in range(1, _MAX_ITERATIONS + 1):
        changed = False
        for node_id in sorted(nodes):
            scores: dict[str, float] = {}
            for neighbor, weight in sorted(adjacency.get(node_id, {}).items()):
                label = labels[neighbor]
                scores[label] = scores.get(label, 0.0) + weight
            if not scores:
                continue
            best = _choose_label(scores, current=labels[node_id])
            if best != labels[node_id]:
                labels[node_id] = best
                changed = True
        if not changed:
            break
    return labels, iterations if nodes else 0


def _choose_label(scores: Mapping[str, float], *, current: str) -> str:
    best_score = max(scores.values())
    winners = [label for label, score in scores.items() if score == best_score]
    if current in winners:
        return current
    return sorted(winners)[0]


def _summarize_communities(
    nodes: Mapping[str, dict[str, Any]],
    projected_nodes: Mapping[str, dict[str, Any]],
    edges: list[dict[str, Any]],
    labels: Mapping[str, str],
    adjacency: Mapping[str, Mapping[str, float]],
    *,
    limit: int,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    groups: dict[str, list[str]] = {}
    for node_id, label in labels.items():
        groups.setdefault(label, []).append(node_id)
    # Excluded nodes (unresolved symbols) still need a community id so the
    # assignments surface stays complete. Each becomes its own singleton.
    for node_id in nodes:
        if node_id not in labels:
            groups.setdefault(node_id, []).append(node_id)
    ordered_groups = sorted((sorted(ids) for ids in groups.values()), key=lambda ids: (-len(ids), ids[0]))
    node_to_community: dict[str, str] = {}
    for index, node_ids in enumerate(ordered_groups, start=1):
        cid = f"c{index:03d}"
        for node_id in node_ids:
            node_to_community[node_id] = cid
    edge_stats = _edge_stats(edges, node_to_community)
    communities = [
        _community_summary(
            cid=f"c{index:03d}",
            node_ids=node_ids,
            nodes=nodes,
            adjacency=adjacency,
            edge_stats=edge_stats,
            limit=limit,
        )
        for index, node_ids in enumerate(ordered_groups, start=1)
    ]
    return communities, {node_id: node_to_community[node_id] for node_id in sorted(nodes)}


def _edge_stats(
    edges: list[dict[str, Any]],
    node_to_community: Mapping[str, str],
) -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {
        cid: {"internal": 0, "boundary": 0, "edge_types": {}, "links": []}
        for cid in set(node_to_community.values())
    }
    for edge in edges:
        src = edge["from"]
        dst = edge["to"]
        src_c = node_to_community[src]
        dst_c = node_to_community[dst]
        if src_c == dst_c:
            stats[src_c]["internal"] += 1
            inc(stats[src_c]["edge_types"], edge.get("type", "unknown"))
        else:
            for cid, other in ((src_c, dst_c), (dst_c, src_c)):
                stats[cid]["boundary"] += 1
                inc(stats[cid]["edge_types"], edge.get("type", "unknown"))
                stats[cid]["links"].append({
                    "from": src,
                    "to": dst,
                    "type": edge.get("type", "unknown"),
                    "weight": _edge_weight(edge),
                    "other_community": other,
                })
    return stats


def _community_summary(
    *,
    cid: str,
    node_ids: list[str],
    nodes: Mapping[str, dict[str, Any]],
    adjacency: Mapping[str, Mapping[str, float]],
    edge_stats: Mapping[str, Mapping[str, Any]],
    limit: int,
) -> dict[str, Any]:
    node_set = set(node_ids)
    types: dict[str, int] = {}
    languages: dict[str, int] = {}
    files: dict[str, int] = {}
    for node_id in node_ids:
        node = nodes[node_id]
        inc(types, node.get("type", "unknown"))
        inc(languages, language_for(node_id, node))
        file_name = file_for(node_id, node)
        if file_name:
            inc(files, file_name)
    hubs = _hub_nodes(node_ids, nodes, adjacency, node_set, limit=limit)
    stats = edge_stats.get(cid, {})
    links = sorted(
        stats.get("links", []),
        key=lambda link: (-link["weight"], link["other_community"], link["from"], link["to"], link["type"]),
    )[:limit]
    return {
        "id": cid,
        "title": title_for(types, languages, hubs),
        "size": len(node_ids),
        "dominant_language": dominant(languages),
        "dominant_type": dominant(types),
        "languages": sorted_counts(languages),
        "node_types": sorted_counts(types),
        "key_files": [
            {"file": name, "nodes": count}
            for name, count in sorted_counts(files).items()
        ][:limit],
        "internal_edges": int(stats.get("internal", 0)),
        "boundary_edges": int(stats.get("boundary", 0)),
        "edge_types": sorted_counts(stats.get("edge_types", {})),
        "hub_nodes": hubs,
        "boundary_links": links,
    }


def _hub_nodes(
    node_ids: list[str],
    nodes: Mapping[str, dict[str, Any]],
    adjacency: Mapping[str, Mapping[str, float]],
    node_set: set[str],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    entries = []
    for node_id in node_ids:
        if node_id not in adjacency:
            continue
        internal = sum(1 for neighbor in adjacency.get(node_id, {}) if neighbor in node_set)
        boundary = sum(1 for neighbor in adjacency.get(node_id, {}) if neighbor not in node_set)
        node = nodes[node_id]
        entries.append((
            is_unresolved_symbol(node_id, node),
            -internal,
            -(internal + boundary),
            node_id,
            {
                "id": node_id,
                "label": node.get("label") or node_id,
                "type": node.get("type", "unknown"),
                "language": language_for(node_id, node),
                "file": file_for(node_id, node),
                "degree": internal + boundary,
                "internal_degree": internal,
                "boundary_degree": boundary,
            },
        ))
    return [entry for *_rank, entry in sorted(entries)[:limit]]


def _global_hubs(
    projected_nodes: Mapping[str, dict[str, Any]],
    adjacency: Mapping[str, Mapping[str, float]],
    assignments: Mapping[str, str],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """Top-N hubs across the projected subgraph (degree-centrality, ADR 0039)."""
    entries = []
    for node_id, node in projected_nodes.items():
        degree = len(adjacency.get(node_id, {}))
        entries.append((-degree, node_id, {
            "id": node_id,
            "label": node.get("label") or node_id,
            "type": node.get("type", "unknown"),
            "language": language_for(node_id, node),
            "file": file_for(node_id, node),
            "degree": degree,
            "community": assignments.get(node_id, ""),
        }))
    entries.sort()
    return [entry for *_rank, entry in entries[:limit]]


def _health_checks(
    nodes: Mapping[str, dict[str, Any]],
    communities: list[dict[str, Any]],
    assignments: Mapping[str, str],
    adjacency: Mapping[str, Mapping[str, float]],
    dangling_edges: int,
    stale: Mapping[str, Any] | None,
) -> dict[str, Any]:
    total = len(nodes)
    isolated = [node_id for node_id in sorted(nodes) if not adjacency.get(node_id)]
    oversized_at = max(100, int(total * 0.25)) if total else 100
    described = [
        node_id for node_id, node in nodes.items()
        if isinstance((node.get("props") or {}).get("description"), str)
        and (node.get("props") or {}).get("description").strip()
    ]
    return {
        "stale_graph": {
            "stale": bool(stale and stale.get("stale")),
            "details": dict(stale or {}),
        },
        "isolated_nodes": {"count": len(isolated), "sample": isolated[:12]},
        "dangling_edges": {"count": dangling_edges},
        "oversized_communities": [
            {"id": c["id"], "size": c["size"], "threshold": oversized_at}
            for c in communities if c["size"] > oversized_at
        ],
        "description_coverage": {
            "coverage_pct": round(len(described) / total * 100, 2) if total else 0.0,
            "threshold_pct": 50.0,
            "low": (len(described) / total * 100) < 50.0 if total else False,
        },
        "high_boundary_communities": [
            {"id": c["id"], "boundary_edges": c["boundary_edges"], "internal_edges": c["internal_edges"]}
            for c in communities
            if c["boundary_edges"] > c["internal_edges"] and c["size"] > 1
        ],
        "assignment_count": len(assignments),
    }


def _edge_weight(edge: Mapping[str, Any]) -> float:
    return _EDGE_WEIGHTS.get(str(edge.get("type") or ""), _DEFAULT_EDGE_WEIGHT)
