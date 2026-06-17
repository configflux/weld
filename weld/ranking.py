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

from weld._coverage_admission import partial_coverage_subject_miss
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

# ``props.origin`` value stamped on unresolved-symbol sentinel nodes by the
# language origin classifiers (see ``weld/strategies/_python_origin.py`` and
# peers). It is the canonical, language-agnostic marker for "this node names a
# callee/base we could not link to a definition" and is the gate the default
# ``wd query`` CLI text filter keys on (see :func:`filter_speculative_matches`).
_UNRESOLVED_ORIGIN: str = "unresolved"

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


def is_unresolved_match(match: dict) -> bool:
    """Return True when *match* is an unresolved-symbol sentinel.

    The signal is ``props.origin == "unresolved"`` -- the canonical marker
    the language origin classifiers stamp on call-graph / inheritance
    sentinels that name a callee or base which could not be linked to a
    definition. This is intentionally narrower than ``resolution_penalty``:

    * a speculative-but-*resolved* node (e.g. a stdlib builtin such as
      ``print`` or ``sum``, which carries ``origin == "stdlib"`` and
      ``confidence == "speculative"``) is NOT unresolved and is kept, so
      the documented ``wd query "print"`` behaviour is preserved;
    * ``confidence`` and the ``symbol:unresolved:`` id prefix are not
      consulted here -- ``origin`` is the single source of truth so the
      filter and the discovery-side classifier never drift.
    """
    props = match.get("props") or {}
    return props.get("origin") == _UNRESOLVED_ORIGIN


def filter_speculative_matches(matches: list[dict]) -> list[dict]:
    """Drop unresolved-symbol sentinels from a list of query *matches*.

    Used by the ``wd query`` CLI text/JSON dispatch to keep the default
    result set focused on definite + inferred + speculative-resolved
    matches. The relative order of the surviving matches is preserved, so
    the upstream ranking (``rank_query_matches``) is untouched -- this is a
    pure post-rank projection.

    The core ``Graph.query`` envelope is deliberately *not* filtered: the
    MCP surface and direct API callers still receive every node (each
    already carries ``confidence`` for client-side discounting). Only the
    CLI default applies this projection, and ``--include-speculative``
    bypasses it to restore the unfiltered view.
    """
    return [match for match in matches if not is_unresolved_match(match)]


def exact_symbol_match_rank(node: dict, token_groups: list[list[str]]) -> int:
    """Return 0 for exact symbol label/qualname hits, else 1."""
    if len(token_groups) != 1 or not token_groups[0]:
        return 1
    if node.get("type") != "symbol":
        return 1
    query = token_groups[0][0].lower()
    label = str(node.get("label") or "").lower()
    props = node.get("props") or {}
    qualname = str(props.get("qualname") or "").lower()
    if query and (query == label or query == qualname):
        return 0
    if query and _qualified_tail_matches(qualname, query):
        return 0
    return 1


def is_amalgamation_file_node(node: dict) -> bool:
    """Return True when *node* is a C++ amalgamation file node.

    Per ADR 0062 the discovery side stamps ``props.amalgamation = True``
    on file nodes whose path matches a single-include / single-header
    convention. The ranker uses this signal as a coarse tiebreak so the
    "import surface" wins against same-score modular peers on
    single-token navigation queries.

    The check is intentionally narrow:

    * the node's ``type`` MUST be ``"file"`` (we never boost a symbol
      node, even if a downstream pass mistakenly inherits the marker);
    * ``props.amalgamation`` must be truthy.
    """
    if node.get("type") != "file":
        return False
    props = node.get("props") or {}
    return bool(props.get("amalgamation"))


def amalgamation_boost(node: dict, token_groups: list[list[str]]) -> int:
    """Return 0 for amalgamation files on a single-token query, else 1.

    Per ADR 0062 the boost only fires when:

    * the query is a single-token navigation query (one token group of
      length 1 -- multi-token queries already provide enough BM25
      signal that the boost would only swap deterministic ordering);
    * the node is a C++ amalgamation file node (see
      :func:`is_amalgamation_file_node`).

    A return value of 0 sorts ahead of 1, so the boost is additive
    inside the existing tiebreak chain in :func:`rank_query_matches`.
    """
    if len(token_groups) != 1 or len(token_groups[0]) != 1:
        return 1
    if not is_amalgamation_file_node(node):
        return 1
    return 0


def _qualified_tail_matches(qualname: str, query: str) -> bool:
    return any(qualname.endswith(sep + query) for sep in (".", "::", "#"))

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

    def sort_key(
        item: tuple[str, dict]
    ) -> tuple[int, int, int, int, int, float, int, int, str]:
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
            exact_symbol_match_rank(node_with_id, token_groups),
            # ADR 0062: amalgamation boost sits AHEAD of -score so it
            # survives near-tie BM25 noise (the ``single_include`` path
            # segment slightly inflates doc length and would otherwise
            # let a modular peer win by ~0.001 BM25 points). Its blast
            # radius is narrow: it only fires on (a) ``type=file``
            # nodes, (b) ``props.amalgamation`` truthy (cpp-only via
            # discovery), and (c) single-token queries -- so non-cpp
            # behaviour is unchanged.
            amalgamation_boost(node_with_id, token_groups),
            # ADR 0075: diffuse-doc demotion. A full-coverage doc whose match
            # is purely scattered across bag fields (``_diffuse`` pre-tagged in
            # weld.graph_query, N>=3 only) sorts AFTER every non-diffuse node
            # (0 < 1) -- i.e. below the bounded-coverage code/entity nodes
            # admitted alongside it. It is re-ranked, never excluded, and the
            # relative order of all non-diffuse nodes is untouched.
            int(bool(node.get("_diffuse"))),
            # ADR 0075 subject tie-break: a bounded-coverage admission that
            # misses the query's leading (subject) token-group in every
            # identity field sorts AFTER coverage-tied peers that carry it
            # (1 > 0). Fixes the entity-shaped collision where a node tied on
            # covered-group count but missing the subject (e.g.
            # ``discovery_state`` for ``"typescript discovery strategy"``)
            # won purely on BM25 IDF rarity. Inert for N<3 and for every
            # non-admitted node; placed ahead of -score so it survives BM25
            # noise, after _diffuse so diffuse-doc demotion still wins.
            partial_coverage_subject_miss(node_with_id, token_groups),
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
