"""SQLite-backed graph query (ADR 0058 Option B).

Implements the federation query path that reads the inverted-index
tables added in sidecar schema version 2 instead of forcing a JSON
parse to rebuild the in-memory inverted index. The query envelope
matches :func:`weld.graph_query.query_graph` so callers can swap
result types without changing downstream code.

The flow per query is:

1. Lowercase + split the search term into tokens.
2. For each token, pull the matching ``token_index`` rows from sqlite
   (parameter-bound substring search, so the term never reaches SQL
   text). A token group is the synonym-expanded form of one user
   token (:func:`weld.synonyms.expand_token_groups`).
3. Intersect the per-group candidate sets (strict-AND), exactly like
   :func:`weld.graph_query.query_graph` does in memory.
4. Rebuild a per-candidate ``Counter`` from the touched rows and score
   each candidate with the same BM25 math as
   :class:`weld.bm25.BM25Corpus`. The corpus stats row supplies the
   ``avg_length`` and ``total_docs`` BM25 needs for IDF.
5. Apply the same ``_match_token_groups`` field-AND check the
   JSON path uses, so the envelope is semantically identical.
6. ADR 0075 parity (8rm0.4): when strict-AND succeeds on an N>=3
   query, additionally admit high-coverage (``>= max(2, N-1)``)
   non-doc nodes from the per-group UNION (so a 3/4 strategy/test
   node the intersection dropped is reachable) and demote a diffuse
   full-coverage doc below them. Mirrors impl #1
   (:func:`weld.graph_query.query_graph`) via the shared
   :mod:`weld._coverage_admission` helpers; inert for N<=2.
7. Score with the same BM25 math impl #1 uses; the diffuse-doc tag
   is a leading sort dimension ahead of the BM25 score (mirrors the
   ``_diffuse`` dimension in :func:`weld.ranking.rank_query_matches`).
8. Return the JSON-shaped envelope (``matches`` only -- neighbors
   and edges are populated by the federation wrapper via the
   already-lazy context path).

Security: every read uses parameter binding; the only literal in any
SQL string is the LIKE wildcard character, which is escaped on the
indexed token side. Term-injection probes (e.g. ``term="' OR 1=1 --"``)
are bound as a literal substring and produce zero matches.
"""

from __future__ import annotations

import sqlite3

from weld._coverage_admission import (
    _COVERAGE_MIN_GROUPS,
    count_groups_hit,
    coverage_admissions,
    hydrate_union,
    or_fallback_sort_key,
    strip_match,
    tag_match,
)
from weld._match_surface import count_group_hits
from weld._query_candidacy import relaxed_or_none
from weld._rank_strict_and import strict_and_sort_key
from weld._sqlite_index import (
    read_corpus_stats,
    read_doc_lengths,
    read_node_frequencies,
    read_token_rows_for_token,
)
from weld._sqlite_query_bm25 import _BM25_B, _BM25_K1, bm25_score
from weld.synonyms import expand_token_groups

# Re-exported for backward compatibility: ``weld._federation_eager_index``
# imports the BM25 parameters from this module. The single source of truth is
# now :mod:`weld._sqlite_query_bm25`.
__all__ = [
    "_BM25_B",
    "_BM25_K1",
    "query_sqlite_backed",
]


def query_sqlite_backed(
    conn: sqlite3.Connection,
    graph_view: object,
    term: str,
    *,
    limit: int = 20,
) -> dict:
    """Run a token-group query against the sidecar's inverted index.

    *graph_view* is the :class:`weld._sqlite_reader.SqliteBackedGraph`
    instance the federation wrapper holds; we accept it as a duck-typed
    handle so we can fetch full node payloads via ``get_node`` without
    importing the reader class (circular import). Returns the same
    envelope shape as :func:`weld.graph_query.query_graph`:

    .. code-block:: python

       {"query": term, "matches": [...], "neighbors": [], "edges": []}

    ``neighbors`` and ``edges`` are intentionally empty here; the
    federation wrapper populates them via the already-lazy context
    path on a per-match basis.
    """
    tokens = term.lower().split()
    if not tokens:
        return _empty(term)

    token_groups = expand_token_groups(tokens)

    # Step 1: gather candidate sets per group via per-token sqlite reads.
    candidates = _candidates_strict_and(conn, token_groups)
    if not candidates:
        # Strict-AND found no node covering every group. Relax to the per-group
        # UNION (OR-fallback) on multi-token queries -- parity with the JSON
        # path (:func:`weld.graph_query.query_graph`), which a fresh sidecar
        # otherwise diverges from (it would return empty where the JSON surface
        # returns ranked union results).
        return _maybe_or_fallback(conn, graph_view, term, tokens, limit)

    # Step 2: fetch full nodes for every candidate so we can apply the
    # exact ``_match_token_groups`` check the JSON path uses. The reader
    # exposes ``get_node`` as an indexed lookup so this scales with the
    # candidate set, not the corpus.
    matched: list[tuple[str, dict]] = []
    matched_ids: set[str] = set()
    candidate_list = sorted(candidates)
    for node_id in candidate_list:
        node = graph_view.get_node(node_id)  # type: ignore[attr-defined]
        if node is None:
            continue
        if _match_token_groups(token_groups, node_id, node):
            # Tag a diffuse full-coverage doc for demotion (N>=3 only; the
            # tag is inert below it). tag_match copies the node, so the
            # backing payload is untouched.
            matched.append((node_id, tag_match(node_id, node, token_groups)))
            matched_ids.add(node_id)
    if not matched:
        # Candidates existed in the index but none passed the field-AND check
        # (substring index over-matches the per-field check). Same OR-fallback
        # relaxation as the zero-candidate branch above.
        return _maybe_or_fallback(conn, graph_view, term, tokens, limit)
    # ADR 0113 candidacy parity: a strict-AND result made entirely of
    # demoted material (a bd issue title, or a test) is not evidence the
    # query was answered, so it must not suppress the fallback.
    relaxed = relaxed_or_none(
        matched,
        token_groups,
        lambda: _maybe_or_fallback(conn, graph_view, term, tokens, limit),
    )
    if relaxed is not None:
        return relaxed

    # ADR 0075 part 1 (8rm0.4 parity): admit high-coverage non-doc nodes the
    # strict-AND intersection dropped. Gated to N>=3 so 1-2 token queries skip
    # the per-group union scan entirely (the mechanism is inert for N<=2).
    if len(token_groups) >= _COVERAGE_MIN_GROUPS:
        union = _candidates_union(conn, token_groups)
        matched.extend(
            coverage_admissions(
                hydrate_union(graph_view.get_node, union, matched_ids),
                union,
                token_groups,
                matched_ids,
            )
        )

    # Step 3: BM25 scoring against the touched node set only.
    match_ids = [nid for nid, _ in matched]
    bm25_scores = _bm25_scores_for(conn, match_ids, token_groups)

    def _score_key(item: tuple[str, dict]) -> tuple:
        node_id, node = item
        return strict_and_sort_key(
            node_id, node, token_groups, bm25_scores.get(node_id, 0.0)
        )

    scored = sorted(matched, key=_score_key)
    # strip_match drops the internal ``_diffuse`` tag but keeps the
    # ``partial_coverage`` consumer signal (mirrors impl #1's envelope).
    matches = [strip_match(node, nid) for nid, node in scored[:limit]]
    return {
        "query": term,
        "matches": matches,
        "neighbors": [],
        "edges": [],
    }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _empty(term: str) -> dict:
    return {"query": term, "matches": [], "neighbors": [], "edges": []}


def _bm25_scores_for(
    conn: sqlite3.Connection,
    match_ids: list[str],
    token_groups: list[list[str]],
) -> dict[str, float]:
    """Precompute each node's BM25 score once against the touched node set.

    Shared by the strict-AND path and the OR-fallback path so they score
    identically. Returns ``{node_id: score}``; a node with no indexed tokens
    scores ``0.0``. Scoring once (rather than inside a sort key) avoids
    re-running BM25 on every pairwise comparison.
    """
    frequencies = read_node_frequencies(conn, match_ids)
    doc_lengths = read_doc_lengths(conn, match_ids)
    avg_length, total_docs = read_corpus_stats(conn)
    df_cache: dict[str, int] = {}
    scores: dict[str, float] = {}
    for node_id in match_ids:
        node_freq = frequencies.get(node_id, {})
        if not node_freq:
            scores[node_id] = 0.0
            continue
        length = doc_lengths.get(node_id, sum(node_freq.values()))
        scores[node_id] = bm25_score(
            token_groups=token_groups,
            node_freq=node_freq,
            length=length,
            avg_length=avg_length,
            total_docs=total_docs,
            df_cache=df_cache,
            conn=conn,
        )
    return scores


def _maybe_or_fallback(
    conn: sqlite3.Connection,
    graph_view: object,
    term: str,
    tokens: list[str],
    limit: int,
) -> dict:
    """Return the OR-fallback envelope for multi-token queries; else empty.

    Parity with :func:`weld.graph_query._maybe_or_fallback`: single-token
    queries skip the fallback (the per-group UNION would return the same nodes
    the already-empty strict-AND path produced, so OR == AND for one group).
    Multi-token queries that also find nothing via OR return an honestly empty
    envelope with NO ``degraded_match`` flag; a non-empty OR result is tagged
    ``degraded_match='or_fallback'`` so consumers can detect the relaxation.
    """
    if len(tokens) <= 1:
        return _empty(term)
    fallback = _or_fallback(conn, graph_view, term, limit)
    if not fallback.get("matches"):
        return _empty(term)
    fallback["degraded_match"] = "or_fallback"
    return fallback


def _or_fallback(
    conn: sqlite3.Connection,
    graph_view: object,
    term: str,
    limit: int,
) -> dict:
    """Soft retrieval path mirroring :func:`weld.graph_query.query_or_fallback`.

    Unions per-group candidates (instead of intersecting them) and ranks the
    survivors with the shared :func:`weld._coverage_admission.or_fallback_sort_key`
    (``group_hits_desc, subject tie-break, BM25_desc, id``), so this sqlite impl
    and the JSON impl produce the same ranked order for the same zero-strict-AND
    multi-token query. The per-group UNION read reuses the same parameter-bound
    :func:`read_token_rows_for_token` as the strict-AND path, so the query term
    never reaches SQL text. ``neighbors``/``edges`` stay empty (populated lazily
    by the federation wrapper), matching the strict-AND envelope.
    """
    token_groups = expand_token_groups(term.lower().split())
    union = _candidates_union(conn, token_groups)
    matched: list[tuple[str, dict]] = []
    group_hits: dict[str, int] = {}
    for node_id in sorted(union):
        node = graph_view.get_node(node_id)  # type: ignore[attr-defined]
        if node is None:
            continue
        hits = count_groups_hit(token_groups, node_id, node)
        if hits > 0:
            matched.append((node_id, node))
            group_hits[node_id] = hits
    if not matched:
        return _empty(term)
    bm25_scores = _bm25_scores_for(
        conn, [nid for nid, _ in matched], token_groups
    )

    def _key(item: tuple[str, dict]) -> tuple[int, int, int, float, str]:
        node_id, node = item
        return or_fallback_sort_key(
            node_id,
            node,
            group_hits[node_id],
            token_groups,
            bm25_scores.get(node_id, 0.0),
        )

    ranked = sorted(matched, key=_key)
    matches = [{"id": node_id, **node} for node_id, node in ranked[:limit]]
    return {"query": term, "matches": matches, "neighbors": [], "edges": []}


def _candidates_strict_and(
    conn: sqlite3.Connection,
    token_groups: list[list[str]],
) -> set[str]:
    """Per-group union; AND across groups. Mirrors ``candidate_nodes_grouped``."""
    result: set[str] | None = None
    for group in token_groups:
        group_hits: set[str] = set()
        for tok in group:
            rows = read_token_rows_for_token(conn, tok)
            for _indexed_token, node_id, _freq in rows:
                group_hits.add(node_id)
        if result is None:
            result = group_hits
        else:
            result &= group_hits
        if not result:
            return set()
    return result if result is not None else set()


def _candidates_union(
    conn: sqlite3.Connection,
    token_groups: list[list[str]],
) -> set[str]:
    """UNION of candidate node ids across all groups (ADR 0075 admission).

    Mirrors :func:`weld.graph_query._candidate_nodes_or` but reads the
    sidecar's ``token_index`` via the same parameter-bound
    :func:`read_token_rows_for_token` the strict-AND path uses -- the query
    term never reaches SQL text. Used only on N>=3 queries to reach the
    high-coverage non-doc nodes the strict-AND *intersection* dropped.
    """
    union: set[str] = set()
    for group in token_groups:
        for tok in group:
            for _indexed_token, node_id, _freq in read_token_rows_for_token(
                conn, tok
            ):
                union.add(node_id)
    return union


def _match_token_groups(
    token_groups: list[list[str]],
    nid: str,
    node: dict,
) -> int:
    """Mirror :meth:`weld.graph.Graph._match_token_groups`.

    Kept as a named local function because this module's read pipeline and its
    tests both address it by this name, but the field set now comes from
    :mod:`weld._match_surface` rather than being restated. It was restated for
    years on the argument that the contract was "small and stable"; it was
    neither -- ``props.headings`` and ``props.keywords`` each reached some
    copies and not others, and the sqlite path silently disagreed with the JSON
    path about which nodes match (bd ph1g).
    """
    return count_group_hits(token_groups, nid, node, short_circuit=True)
