"""Federated query ordering helpers."""

from __future__ import annotations

from weld.ranking import (
    authority_score,
    confidence_score,
    exact_symbol_match_rank,
    resolution_penalty,
)
from weld.synonyms import expand_token_groups


def query_federated(federation, term: str, limit: int) -> dict:
    """Run a federated query with exact-symbol collection when useful."""
    if _is_exact_style_term(term):
        return _query_exact_style(federation, term, limit)
    return _query_incremental(federation, term, limit)


def _query_incremental(federation, term: str, limit: int) -> dict:
    matches: list[dict] = []
    root_matches = federation._root_graph.query(
        term, limit=limit,
    ).get("matches", [])
    for match in root_matches:
        matches.append(federation._decorate_node(match))
        if len(matches) >= limit:
            return federation._query_payload(term, matches)
    for name in sorted(federation._children):
        for match in federation._child_query_matches(name, term, limit):
            matches.append(federation._prefix_node(name, match))
            if len(matches) >= limit:
                return federation._query_payload(term, matches)
    return federation._query_payload(term, matches)


def _query_exact_style(federation, term: str, limit: int) -> dict:
    if limit <= 0:
        return federation._query_payload(term, [])
    source_limit = max(limit, 20)
    matches: list[dict] = []
    root_matches = federation._root_graph.query(
        term, limit=source_limit,
    ).get("matches", [])
    matches.extend(federation._decorate_node(match) for match in root_matches)
    for name in sorted(federation._children):
        child_matches = federation._child_query_matches(name, term, source_limit)
        matches.extend(
            federation._prefix_node(name, match)
            for match in child_matches
        )
    ranked = _rank_exact_style_matches(term, matches)
    return federation._query_payload(term, ranked[:limit])


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
