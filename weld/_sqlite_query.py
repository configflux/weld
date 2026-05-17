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
6. Score with :func:`weld.ranking.rank_query_matches` (BM25 only --
   structural scores would require an edge fan-out we keep off the
   hot path; future enhancement).
7. Return the JSON-shaped envelope (``matches`` only -- neighbors
   and edges are populated by the federation wrapper via the
   already-lazy context path).

Security: every read uses parameter binding; the only literal in any
SQL string is the LIKE wildcard character, which is escaped on the
indexed token side. Term-injection probes (e.g. ``term="' OR 1=1 --"``)
are bound as a literal substring and produce zero matches.
"""

from __future__ import annotations

import math
import sqlite3
from collections import Counter

from weld._sqlite_index import (
    read_corpus_stats,
    read_doc_lengths,
    read_node_frequencies,
    read_token_rows_for_token,
)
from weld.ranking import (
    authority_score,
    confidence_score,
    exact_symbol_match_rank,
    resolution_penalty,
)
from weld.synonyms import expand_token_groups

__all__ = [
    "query_sqlite_backed",
]


# BM25 parameters mirror :class:`weld.bm25.BM25Corpus` so the
# lazy-from-sqlite path scores nodes consistently with the in-memory
# path. If we change one we must change the other; the test that
# compares sqlite-vs-JSON match sets is the canary.
_BM25_K1 = 1.2
_BM25_B = 0.75


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
        return _empty(term)

    # Step 2: fetch full nodes for every candidate so we can apply the
    # exact ``_match_token_groups`` check the JSON path uses. The reader
    # exposes ``get_node`` as an indexed lookup so this scales with the
    # candidate set, not the corpus.
    matched: list[tuple[str, dict]] = []
    candidate_list = sorted(candidates)
    for node_id in candidate_list:
        node = graph_view.get_node(node_id)  # type: ignore[attr-defined]
        if node is None:
            continue
        if _match_token_groups(token_groups, node_id, node):
            matched.append((node_id, node))
    if not matched:
        return _empty(term)

    # Step 3: BM25 scoring against the touched node set only.
    match_ids = [nid for nid, _ in matched]
    frequencies = read_node_frequencies(conn, match_ids)
    doc_lengths = read_doc_lengths(conn, match_ids)
    avg_length, total_docs = read_corpus_stats(conn)
    df_cache: dict[str, int] = {}

    # Precompute each node's BM25 score once so the sort key does
    # not re-run scoring on every pairwise comparison.
    bm25_scores: dict[str, float] = {}
    for node_id in match_ids:
        node_freq = frequencies.get(node_id, {})
        if not node_freq:
            bm25_scores[node_id] = 0.0
            continue
        length = doc_lengths.get(node_id, sum(node_freq.values()))
        bm25_scores[node_id] = _bm25_score(
            token_groups=token_groups,
            node_freq=node_freq,
            length=length,
            avg_length=avg_length,
            total_docs=total_docs,
            df_cache=df_cache,
            conn=conn,
        )

    def _score_key(item: tuple[str, dict]) -> tuple[int, int, float, int, int, str]:
        node_id, node = item
        node_with_id = {"id": node_id, **node}
        return (
            resolution_penalty(node_with_id),
            exact_symbol_match_rank(node_with_id, token_groups),
            -bm25_scores.get(node_id, 0.0),
            authority_score(node_with_id),
            confidence_score(node_with_id),
            node_id,
        )

    scored = sorted(matched, key=_score_key)
    matches = [{"id": nid, **node} for nid, node in scored[:limit]]
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


def _match_token_groups(
    token_groups: list[list[str]],
    nid: str,
    node: dict,
) -> int:
    """Mirror :meth:`weld.graph.Graph._match_token_groups`.

    Duplicated here (not imported) to keep the sqlite-backed query
    path independent of the JSON-Graph module; the contract is small
    and stable, and the duplication has been the same pattern the JSON
    path uses for years.
    """
    nid_l = nid.lower()
    label_l = node.get("label", "").lower()
    props = node.get("props") or {}
    file_l = (props.get("file") or "").lower()
    qualname_l = str(props.get("qualname") or "").lower()
    exports_l = [e.lower() for e in props.get("exports", []) if isinstance(e, str)]
    constants_l = [c.lower() for c in props.get("constants", []) if isinstance(c, str)]
    headings_l = [h.lower() for h in props.get("headings", []) if isinstance(h, str)]
    desc_l = (props.get("description") or "").lower()
    hits = 0
    for group in token_groups:
        if any(
            t in nid_l or t in label_l or t in file_l or t in desc_l
            or t in qualname_l
            or any(t in e for e in exports_l)
            or any(t in c for c in constants_l)
            or any(t in h for h in headings_l)
            for t in group
        ):
            hits += 1
        else:
            return 0
    return hits


def _bm25_score(
    *,
    token_groups: list[list[str]],
    node_freq: dict[str, int],
    length: int,
    avg_length: float,
    total_docs: int,
    df_cache: dict[str, int],
    conn: sqlite3.Connection,
) -> float:
    """Compute the BM25 score for one node.

    Mirrors :meth:`weld.bm25.BM25Corpus.score`. Each group contributes
    its best per-term score so synonyms do not multiply matches.
    """
    if not node_freq or not token_groups or avg_length <= 0 or total_docs <= 0:
        return 0.0
    frequencies = Counter(node_freq)
    score = 0.0
    for group in token_groups:
        score += max(
            _term_score(
                term=term,
                frequencies=frequencies,
                length=length,
                avg_length=avg_length,
                total_docs=total_docs,
                df_cache=df_cache,
                conn=conn,
            )
            for term in group
        )
    return score


def _term_score(
    *,
    term: str,
    frequencies: Counter[str],
    length: int,
    avg_length: float,
    total_docs: int,
    df_cache: dict[str, int],
    conn: sqlite3.Connection,
) -> float:
    """Per-term BM25 score with substring-aware term frequency."""
    tf = _matching_frequency(term, frequencies)
    if tf <= 0 or avg_length <= 0:
        return 0.0
    idf = _idf(term, total_docs, df_cache, conn)
    denominator = tf + _BM25_K1 * (1 - _BM25_B + _BM25_B * length / avg_length)
    return idf * (tf * (_BM25_K1 + 1)) / denominator


def _matching_frequency(term: str, frequencies: Counter[str]) -> int:
    return sum(count for token, count in frequencies.items() if term in token)


def _idf(
    term: str,
    total_docs: int,
    df_cache: dict[str, int],
    conn: sqlite3.Connection,
) -> float:
    """Inverse document frequency for *term*, with a small DF cache.

    The DF count is the number of *nodes* whose indexed tokens contain
    *term* as a substring -- the same definition the in-memory BM25
    uses. The cache is per-query, which is enough for synonym-expanded
    groups where the same term may appear in multiple groups.
    """
    cached = df_cache.get(term)
    if cached is None:
        df = _document_frequency(term, conn)
        df_cache[term] = df
    else:
        df = cached
    if df <= 0 or total_docs <= 0:
        return 0.0
    return math.log(1 + (total_docs - df + 0.5) / (df + 0.5))


def _document_frequency(term: str, conn: sqlite3.Connection) -> int:
    """Return the number of distinct nodes whose tokens contain *term*."""
    if not term:
        return 0
    safe_token = (
        term.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )
    row = conn.execute(
        "SELECT COUNT(DISTINCT node_id) FROM token_index"
        " WHERE token LIKE '%' || ? || '%' ESCAPE '\\'",
        (safe_token,),
    ).fetchone()
    return int(row[0]) if row else 0
