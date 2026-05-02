"""Shared ranking utilities for connected structure retrieval.

Ranks results by hybrid lexical, semantic, structural, and authority signals,
with confidence and optional role relevance as deterministic tie-breakers.

Authority ordering:  canonical > derived > manual > inferred
Confidence ordering: definite > inferred > speculative

The ``rank_query_matches`` path additionally applies a coarse
``resolution_penalty`` ahead of the hybrid score so that unresolved-
symbol sentinels (e.g. ``symbol:unresolved:<name>`` emitted by call-
graph closure when a callee cannot be linked to a definition) sort
below definite resolved peers even when their BM25 score on a short
label happens to be higher.

"""

from __future__ import annotations

from weld.bm25 import BM25Corpus

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

# ID prefix that marks a call-graph "unresolved symbol" sentinel node.
# These nodes are emitted by graph closure when a callee cannot be linked
# to a real definition; surfacing them ahead of definite resolved symbols
# in retrieval results is a known quality regression.
_UNRESOLVED_SYMBOL_PREFIX: str = "symbol:unresolved:"

DEFAULT_HYBRID_WEIGHTS: dict[str, float] = {
    "bm25": 0.4,
    "semantic": 0.3,
    "structural": 0.2,
    "authority": 0.1,
}

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


def resolution_penalty(node: dict) -> int:
    """Return 1 when *node* is an unresolved sentinel, else 0.

    Used as a coarse pre-score gate in ``rank_query_matches`` so that
    definite resolved symbols outrank ``symbol:unresolved:<name>``
    sentinels regardless of BM25 differences. The penalty fires when
    either of the two unresolved-sentinel signals is set:

    * ID begins with ``symbol:unresolved:`` (call-graph closure emits
      these for callees that could not be linked to a definition);
    * ``props.resolution == "unresolved"`` (explicit tag, used for
      reference-style unresolved entries).

    ``confidence: speculative`` alone is NOT enough to trigger the
    penalty: speculative-but-resolved nodes (e.g. an inferred-confidence
    callsite that did link to its target) still rank by the existing
    authority > confidence > id tiebreakers.  This keeps the bug-fix
    targeted -- it demotes only the noise class the inspector flagged
    (``symbol:unresolved:_has_enrichment`` beating
    ``symbol:py:weld.embeddings:enrichment_description``).
    """
    node_id = node.get("id", "")
    if isinstance(node_id, str) and node_id.startswith(_UNRESOLVED_SYMBOL_PREFIX):
        return 1
    props = node.get("props") or {}
    if props.get("resolution") == "unresolved":
        return 1
    return 0

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

def authority_relevance(node: dict) -> float:
    """Return authority normalized to 0..1, where canonical is strongest."""
    score = authority_score(node)
    if score >= _UNKNOWN_RANK:
        return 0.0
    known_levels = len(AUTHORITY_RANK)
    if known_levels <= 0:
        return 1.0
    return max(0.0, (known_levels - score) / known_levels)

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

def active_hybrid_weights(
    bm25_scores: dict[str, float],
    semantic: dict[str, float | None],
    structural: dict[str, float],
    weights: dict[str, float] | None = None,
) -> dict[str, float]:
    """Return active query weights normalized across available signals."""
    configured = weights or DEFAULT_HYBRID_WEIGHTS
    active = {
        "bm25": any(score > 0 for score in bm25_scores.values()),
        "semantic": any(score is not None for score in semantic.values()),
        "structural": any(score > 0 for score in structural.values()),
        "authority": True,
    }
    total = sum(configured[name] for name, enabled in active.items() if enabled)
    if total <= 0:
        return {}
    return {
        name: configured[name] / total
        for name, enabled in active.items()
        if enabled
    }

def hybrid_score(
    node: dict,
    *,
    bm25: float,
    semantic: float | None,
    structural: float,
    weights: dict[str, float],
) -> float:
    """Compose normalized ranking signals into one deterministic score."""
    score = weights.get("bm25", 0.0) * bm25
    if semantic is not None:
        score += weights.get("semantic", 0.0) * semantic
    score += weights.get("structural", 0.0) * structural
    score += weights.get("authority", 0.0) * authority_relevance(node)
    return score

def rank_query_matches(
    matches: list[tuple[str, dict]],
    token_groups: list[list[str]],
    bm25: BM25Corpus,
    structural_scores: dict[str, float],
    *,
    semantic: dict[str, float | None] | None = None,
    query_roles: frozenset[str] | None = None,
) -> list[tuple[str, dict]]:
    """Rank matched query candidates with the ADR 0010 hybrid score."""
    raw_bm25 = {node_id: bm25.score(node_id, token_groups) for node_id, _ in matches}
    normalized_bm25 = _normalize_positive(raw_bm25)
    semantic_scores = semantic or {node_id: None for node_id, _ in matches}
    structural = {
        node_id: structural_scores.get(node_id, 0.0)
        for node_id, _ in matches
    }
    weights = active_hybrid_weights(normalized_bm25, semantic_scores, structural)

    def sort_key(item: tuple[str, dict]) -> tuple[int, float, int, int, str]:
        node_id, node = item
        node_with_id = {"id": node_id, **node}
        score = hybrid_score(
            node_with_id,
            bm25=normalized_bm25.get(node_id, 0.0),
            semantic=semantic_scores.get(node_id),
            structural=structural.get(node_id, 0.0),
            weights=weights,
        )
        return (
            resolution_penalty(node_with_id),
            -score,
            role_boost(node, query_roles),
            confidence_score(node),
            node_id,
        )

    return sorted(matches, key=sort_key)

def _normalize_positive(scores: dict[str, float]) -> dict[str, float]:
    maximum = max(scores.values(), default=0.0)
    if maximum <= 0:
        return {key: 0.0 for key in scores}
    return {key: value / maximum for key, value in scores.items()}
