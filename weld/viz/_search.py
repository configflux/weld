"""Search-suggest helpers for the viz API (bd h6z0.8).

Kept in a sibling module so ``weld/viz/api.py`` stays under the 400-line
cap. The substring-fallback helper here is also used by
``VizApi._query_slice`` when the tokenized search yields zero matches.
"""

from __future__ import annotations

from weld.viz._adapter_helpers import degree_by_node, is_entry_point, overview_key

_SUGGEST_HARD_LIMIT = 50


def suggest_limit(raw: object, *, default: int) -> int:
    """Clamp a ``/api/search-suggest`` limit into ``[1, 50]``.

    ``default`` is per-call so the empty-state path ships 5 entry
    points while the substring path ships 20.
    """
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    if value < 1:
        return default
    return min(value, _SUGGEST_HARD_LIMIT)


def substring_match_nodes(data: dict, query: str, *, limit: int) -> list[dict]:
    """Return record-shaped nodes whose id or label contains *query*.

    Case-insensitive. Same record shape as ``Graph.query`` matches so
    the caller can feed the result straight into ``normalize_records``.
    """
    needle = query.lower()
    if not needle:
        return []
    matches: list[dict] = []
    for nid, node in (data.get("nodes", {}) or {}).items():
        label = str(node.get("label") or "")
        if needle in nid.lower() or needle in label.lower():
            matches.append({"id": nid, **node})
        if len(matches) >= limit:
            break
    return matches


def substring_suggestions(nodes: dict, query: str, limit: int) -> list[dict]:
    """Return dropdown-shaped ``{id, label, type}`` suggestions.

    Ranking: label matches outrank id-only matches (a hit on the
    human-visible label is a stronger signal than a hit anywhere in
    the structured id). Within each band shorter labels rank higher,
    ties broken alphabetically by id for determinism.
    """
    needle = query.lower()
    hits: list[tuple[int, int, str, dict]] = []
    for nid, node in nodes.items():
        label = str(node.get("label") or "")
        label_hit = needle in label.lower()
        id_hit = needle in nid.lower()
        if not (label_hit or id_hit):
            continue
        rank = 0 if label_hit else 1
        sort_label = label or nid
        hits.append((rank, len(sort_label), nid, {
            "id": nid, "label": label, "type": str(node.get("type") or ""),
        }))
    hits.sort(key=lambda row: (row[0], row[1], row[2]))
    return [item for _, _, _, item in hits[:limit]]


def top_degree_suggestions(data: dict, limit: int) -> list[dict]:
    """Return the top-*limit* project entry points as suggestions.

    Powers the empty-state hint, the inspector's cold-open seed, and the
    search dropdown when ``q`` is empty. Ranking by raw degree alone
    surfaces stdlib and unresolved test-assertion hubs -- the worst
    possible orientation for a first-time view -- so this keeps only
    orientation entry points (:func:`is_entry_point`: ``project``-origin
    nodes plus agent-graph anchors such as CLI commands, agents, and MCP
    tools) and ranks survivors by the same overview key the graph view
    uses: project-surface priority (commands, packages, entrypoints
    outrank bare symbols), then descending degree, then id for
    determinism (bd 123p / ADR 0073).
    """
    nodes = data.get("nodes", {}) or {}
    entry_nodes = {
        nid: node
        for nid, node in nodes.items()
        if is_entry_point(nid, node)
    }
    degree = degree_by_node(data.get("edges", []) or [])
    ranked = sorted(
        entry_nodes,
        key=lambda nid: overview_key(nid, entry_nodes[nid], degree),
    )
    # ADR 0073: a short seed of N agents is poor orientation when the
    # project also has commands, packages, and MCP tools (agents share
    # the lowest overview_key priority, so a plain top-N is all agents).
    # Round-robin one node per type -- each type's nodes stay in
    # overview_key order, but the first pass guarantees the named entry
    # points (commands / MCP tools / packages / agents) all appear before
    # the list is padded out by the second pass.
    ranked = _diversify_by_type(ranked, entry_nodes)
    return [
        {
            "id": nid,
            "label": str(entry_nodes[nid].get("label") or ""),
            "type": str(entry_nodes[nid].get("type") or ""),
        }
        for nid in ranked[:limit]
    ]


def _diversify_by_type(ranked: list[str], nodes: dict) -> list[str]:
    """Reorder *ranked* round-robin by node type, preserving within-type order.

    Buckets keep their first-seen order (which is overview_key order, so
    the highest-priority type leads), then we emit one id per bucket per
    round. The result lists every type's top entry point before any
    type's second, giving a balanced cold-open seed.
    """
    buckets: dict[str, list[str]] = {}
    for nid in ranked:
        buckets.setdefault(str(nodes[nid].get("type") or ""), []).append(nid)
    order: list[str] = []
    bucket_lists = list(buckets.values())
    index = 0
    while any(index < len(b) for b in bucket_lists):
        for bucket in bucket_lists:
            if index < len(bucket):
                order.append(bucket[index])
        index += 1
    return order
