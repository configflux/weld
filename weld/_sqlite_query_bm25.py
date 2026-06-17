"""BM25 scoring helpers for the sqlite-backed query path (ADR 0058 Option B).

Split out of :mod:`weld._sqlite_query` so that module stays under the
line-count cap once the ADR 0075 coverage-admission helpers (8rm0.4) landed
there; the scoring logic is unchanged. The BM25 parameters live here as the
single source of truth and are re-exported from :mod:`weld._sqlite_query` for
backward compatibility (``weld._federation_eager_index`` imports them from
there).

Security: :func:`_document_frequency` is the only function that touches SQL
text; the term is bound as a parameter and its LIKE wildcards are escaped, so
a query term never reaches the SQL string.
"""

from __future__ import annotations

import math
import sqlite3
from collections import Counter

# BM25 parameters mirror :class:`weld.bm25.BM25Corpus` so the
# lazy-from-sqlite path scores nodes consistently with the in-memory
# path. If we change one we must change the other; the test that
# compares sqlite-vs-JSON match sets is the canary.
_BM25_K1 = 1.2
_BM25_B = 0.75


def bm25_score(
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
