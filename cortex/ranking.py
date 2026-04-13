"""Shared ranking utilities for knowledge graph retrieval.

Ranks results by authority, confidence, and optionally role relevance so that
authoritative, high-confidence nodes appear first in both ``cortex query`` and
``cortex brief`` results.

Authority ordering:  canonical > derived > manual > inferred
Confidence ordering: definite > inferred > speculative

"""

from __future__ import annotations

# Authority ordering: canonical > derived > manual > inferred
# Lower numeric value = higher priority
AUTHORITY_RANK: dict[str, int] = {
    "canonical": 0,
    "derived": 1,
    "manual": 2,
    "inferred": 3,
}

# Confidence ordering: definite > inferred > speculative
CONFIDENCE_RANK: dict[str, int] = {
    "definite": 0,
    "inferred": 1,
    "speculative": 2,
}

# Sentinel value for missing/unknown metadata -- sorts after all known values
_UNKNOWN_RANK: int = 99

def authority_score(node: dict) -> int:
    """Return a numeric authority score for *node* (lower is better).

    Missing or unrecognized authority values sort after all known values.
    """
    props = node.get("props") or {}
    return AUTHORITY_RANK.get(props.get("authority", ""), _UNKNOWN_RANK)

def confidence_score(node: dict) -> int:
    """Return a numeric confidence score for *node* (lower is better).

    Missing or unrecognized confidence values sort after all known values.
    """
    props = node.get("props") or {}
    return CONFIDENCE_RANK.get(props.get("confidence", ""), _UNKNOWN_RANK)

def role_boost(node: dict, query_roles: frozenset[str] | None = None) -> int:
    """Return 0 if any of the node's roles match *query_roles*, else 1.

    When *query_roles* is ``None`` or empty, no boost is applied (returns 0
    for all nodes so it does not affect ordering).
    """
    if not query_roles:
        return 0
    props = node.get("props") or {}
    node_roles = set(props.get("roles", []))
    if node_roles & query_roles:
        return 0  # boost: sorts earlier
    return 1  # no boost: sorts later

def rank_key(
    node: dict,
    *,
    query_roles: frozenset[str] | None = None,
) -> tuple[int, int, int, str]:
    """Composite sort key: (role_boost, authority, confidence, node_id).

    Designed so that ``sorted(nodes, key=rank_key)`` puts authoritative,
    high-confidence, role-relevant nodes first with deterministic tiebreaking.
    """
    return (
        role_boost(node, query_roles),
        authority_score(node),
        confidence_score(node),
        node.get("id", ""),
    )

def query_rank_key(
    token_hits: int,
    node: dict,
    *,
    query_roles: frozenset[str] | None = None,
) -> tuple[int, int, int, int, str]:
    """Sort key for ``Graph.query()`` that layers ranking on top of token match count.

    Primary sort is by token hits (descending, so we negate).  Within the same
    hit count, authority, confidence, role boost, and node ID break ties.
    """
    return (
        -token_hits,
        role_boost(node, query_roles),
        authority_score(node),
        confidence_score(node),
        node.get("id", ""),
    )
