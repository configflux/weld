"""Federated query ordering helpers."""

from __future__ import annotations

from weld.ranking import (
    authority_score,
    confidence_score,
    exact_symbol_match_rank,
    resolution_penalty,
)
from weld.synonyms import expand_token_groups

# Per-source candidate floor. Every source (root + each child) contributes up
# to this many candidates before the global re-rank, so a large early-alphabet
# child cannot starve later children of the user's result budget. Mirrors the
# exact-style collection width.
_SOURCE_LIMIT_FLOOR = 20


def query_federated(federation, term: str, limit: int) -> dict:
    """Run a federated query, collecting per-source then ranking globally."""
    if _is_exact_style_term(term):
        return _query_exact_style(federation, term, limit)
    return _query_incremental(federation, term, limit)


def _query_incremental(federation, term: str, limit: int) -> dict:
    """Fan out a multi-token query fairly across every source.

    Historically this filled ``limit`` from children in lexicographic name
    order and early-returned, so an early-alphabet child could consume the
    whole budget and later children never contributed. We now collect up to
    ``source_limit`` candidates from root and every child (mirroring the
    exact-style path) and merge them with a fair, deterministic global rank
    (:func:`_rank_interleaved_matches`) before truncating to ``limit``.
    """
    if limit <= 0:
        return federation._query_payload(term, [])
    source_limit = max(limit, _SOURCE_LIMIT_FLOOR)
    sources = _collect_sources(federation, term, source_limit)
    ranked = _rank_interleaved_matches(sources)
    return federation._query_payload(term, ranked[:limit])


def _query_exact_style(federation, term: str, limit: int) -> dict:
    if limit <= 0:
        return federation._query_payload(term, [])
    source_limit = max(limit, _SOURCE_LIMIT_FLOOR)
    sources = _collect_sources(federation, term, source_limit)
    matches = [match for source in sources for match in source]
    ranked = _rank_exact_style_matches(term, matches)
    return federation._query_payload(term, ranked[:limit])


def _collect_sources(
    federation, term: str, source_limit: int,
) -> list[list[dict]]:
    """Collect up to *source_limit* matches from root + every child.

    Returns one decorated match list per source: root first, then children in
    sorted name order. Each inner list preserves that source's own relevance
    ranking, which callers use either flattened (exact-style) or as the
    per-source position for the fair interleave (incremental).
    """
    root_matches = federation._root_graph.query(
        term, limit=source_limit,
    ).get("matches", [])
    sources: list[list[dict]] = [
        [federation._decorate_node(match) for match in root_matches]
    ]
    for name in sorted(federation._children):
        child_matches = federation._child_query_matches(name, term, source_limit)
        sources.append(
            [federation._prefix_node(name, match) for match in child_matches]
        )
    return sources


def _rank_interleaved_matches(sources: list[list[dict]]) -> list[dict]:
    """Merge per-source match lists into one fair, deterministic order.

    Each match sorts by its position WITHIN its source, so the top hit of
    every source is considered before any source's second hit -- a round-robin
    merge that prevents lexicographic child starvation. The unresolved-sentinel
    penalty keeps noise nodes last, and authority/confidence/id break ties for
    a stable global ordering that is independent of child iteration order.

    (Unlike the exact-style rank, this path cannot lean on
    ``exact_symbol_match_rank`` -- it is inert for multi-token queries -- so
    per-source position is what distributes the budget across children.)
    """

    def _key(item: tuple[int, dict]) -> tuple[int, int, int, int, str]:
        position, match = item
        return (
            resolution_penalty(match),
            position,
            authority_score(match),
            confidence_score(match),
            str(match.get("id", "")),
        )

    tagged = [
        (position, match)
        for source in sources
        for position, match in enumerate(source)
    ]
    return [match for _position, match in sorted(tagged, key=_key)]


def _rank_exact_style_matches(term: str, matches: list[dict]) -> list[dict]:
    token_groups = expand_token_groups(term.lower().split())

    def _key(item: tuple[int, dict]) -> tuple[int, int, int, int, int, str]:
        index, match = item
        return (
            resolution_penalty(match),
            exact_symbol_match_rank(match, token_groups),
            index,
            authority_score(match),
            confidence_score(match),
            str(match.get("id", "")),
        )

    return [match for _index, match in sorted(enumerate(matches), key=_key)]


def _is_exact_style_term(term: str) -> bool:
    tokens = term.strip().split()
    if len(tokens) != 1:
        return False
    token = tokens[0]
    return bool(token) and any(ch.isalnum() or ch == "_" for ch in token)
