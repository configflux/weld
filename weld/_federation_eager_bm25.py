"""Per-node BM25 scoring for the eager federation index (ADR 0063).

Split out of :mod:`weld._federation_eager_index` so that module stays under
the line-count cap once the ADR 0075 coverage-admission helpers (8rm0.4)
landed there; the scoring math is unchanged. These are free functions
parameterized by the aggregate corpus stats the index already holds
(``avg_length``, ``total_docs``, and the postings lists for DF counting), so
they carry no class coupling.

The BM25 parameters are imported from :mod:`weld._sqlite_query` (which
re-exports them from :mod:`weld._sqlite_query_bm25`) so the eager, lazy, and
in-memory paths score consistently -- the same single source of truth the
match-set parity tests rely on. Sharing K1/B was never sufficient on its own:
until bd ph1g this module's document frequency counted *vocabulary entries*
where the other two impls count *nodes*, so the three could and did disagree on
which result led a query (bd ki4u). :func:`_idf` records that.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Callable

from weld._sqlite_query import _BM25_B, _BM25_K1

#: The aggregated inverted index: token -> [(child_name, node_id, freq), ...].
#: Taken whole rather than as its keys because document frequency counts the
#: distinct nodes in the postings, not the vocabulary entries (bd ki4u).
Postings = dict[str, list[tuple[str, str, int]]]


def score_one(
    *,
    node_freq: dict[str, int],
    length: int,
    token_groups: list[list[str]],
    df_cache: dict[str, int],
    avg_length: float,
    total_docs: int,
    postings: Postings,
) -> float:
    """BM25 score for one node; each group contributes its best per-term score."""
    if not node_freq:
        return 0.0
    frequencies = Counter(node_freq)
    score = 0.0
    for group in token_groups:
        score += max(
            _term_score(
                term=term,
                frequencies=frequencies,
                length=length,
                df_cache=df_cache,
                avg_length=avg_length,
                total_docs=total_docs,
                postings=postings,
            )
            for term in group
        )
    return score


def _term_score(
    *,
    term: str,
    frequencies: Counter[str],
    length: int,
    df_cache: dict[str, int],
    avg_length: float,
    total_docs: int,
    postings: Postings,
) -> float:
    tf = sum(c for tok, c in frequencies.items() if term in tok)
    if tf <= 0 or avg_length <= 0:
        return 0.0
    idf = _idf(term, df_cache, total_docs, postings)
    denom = tf + _BM25_K1 * (1 - _BM25_B + _BM25_B * length / avg_length)
    return idf * (tf * (_BM25_K1 + 1)) / denom


def _idf(
    term: str,
    df_cache: dict[str, int],
    total_docs: int,
    postings: Postings,
) -> float:
    """Inverse document frequency, where a *document* is a node (bd ki4u).

    This counted vocabulary entries until bd ph1g: ``sum(1 for indexed_token in
    inverted if term in indexed_token)`` is the number of distinct *tokens*
    containing the term, while impl #1 and impl #2 both count the number of
    distinct *nodes* whose tokens contain it
    (``_sqlite_query_bm25._document_frequency``: ``COUNT(DISTINCT node_id)``).

    The two diverge whenever one node contributes several matching tokens --
    which for a substring term is the normal case, since a node is indexed under
    its whole id, its label, its path, and each of their separator-split parts.
    That made ``total_docs`` and ``df`` incommensurable in the same formula, and
    the effect was not a uniform scale factor: a term spread over many tokens on
    few nodes was scored as if it were common. Measured on the eval corpus, it
    put a different node at rank 1 on federation than on the other two backends
    for the same query and the same graph.

    Counting nodes here makes the three impls agree by definition rather than by
    coincidence. The postings lists already carry the node identity, so this
    costs one set build per uncached term and no new read.
    """
    cached = df_cache.get(term)
    if cached is None:
        df = len({
            (child_name, node_id)
            for indexed_token, hits in postings.items()
            if term in indexed_token
            for child_name, node_id, _freq in hits
        })
        df_cache[term] = df
    else:
        df = cached
    if df <= 0 or total_docs <= 0:
        return 0.0
    return math.log(1 + (total_docs - df + 0.5) / (df + 0.5))


def scores_for_matched(
    *,
    matched: list[tuple[str, dict]],
    node_freqs: dict[str, dict[str, int]],
    doc_length_for: Callable[[str], int | None],
    token_groups: list[list[str]],
    avg_length: float,
    total_docs: int,
    postings: Postings,
) -> dict[str, float]:
    """Per-node BM25 scores for a ``matched`` list from the index's dicts.

    Pulled out of :class:`weld._federation_eager_index.EagerFederationIndex`
    so that module stays under the line-count cap; the math is unchanged. A
    node with no indexed tokens scores ``0.0``. *doc_length_for* returns the
    stored doc length for a node id (or ``None`` to fall back to the summed
    frequency), so the caller keeps ownership of the ``(child, node)`` keying.
    """
    if avg_length <= 0 or total_docs <= 0:
        return {nid: 0.0 for nid, _ in matched}
    df_cache: dict[str, int] = {}
    scores: dict[str, float] = {}
    for node_id, _node in matched:
        node_freq = node_freqs.get(node_id, {})
        if not node_freq:
            scores[node_id] = 0.0
            continue
        length = doc_length_for(node_id)
        if length is None:
            length = sum(node_freq.values())
        scores[node_id] = score_one(
            node_freq=node_freq,
            length=length,
            token_groups=token_groups,
            df_cache=df_cache,
            avg_length=avg_length,
            total_docs=total_docs,
            postings=postings,
        )
    return scores
