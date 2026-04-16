"""Pure-Python BM25 scoring for graph query documents."""

from __future__ import annotations

import math
from collections import Counter

from weld.query_index import node_tokens


class BM25Corpus:
    """Precomputed BM25 corpus over the queryable node surface."""

    def __init__(
        self,
        documents: dict[str, list[str]],
        *,
        k1: float = 1.2,
        b: float = 0.75,
    ) -> None:
        self._frequencies = {nid: Counter(tokens) for nid, tokens in documents.items()}
        self._lengths = {nid: sum(freq.values()) for nid, freq in self._frequencies.items()}
        self._doc_count = len(documents)
        total_length = sum(self._lengths.values())
        self._avg_length = total_length / self._doc_count if self._doc_count else 0.0
        self._idf_cache: dict[str, float] = {}
        self._k1 = k1
        self._b = b

    @classmethod
    def from_nodes(cls, nodes: dict[str, dict]) -> "BM25Corpus":
        return cls({nid: node_tokens(nid, node) for nid, node in nodes.items()})

    @property
    def doc_count(self) -> int:
        return self._doc_count

    def score(self, node_id: str, token_groups: list[list[str]]) -> float:
        """Return BM25 score for a node against synonym-expanded token groups."""
        frequencies = self._frequencies.get(node_id)
        if not frequencies or not token_groups:
            return 0.0
        length = self._lengths[node_id]
        score = 0.0
        for group in token_groups:
            score += max(self._term_score(term, frequencies, length) for term in group)
        return score

    def _term_score(self, term: str, frequencies: Counter[str], length: int) -> float:
        tf = _matching_frequency(term, frequencies)
        if tf <= 0 or self._avg_length <= 0:
            return 0.0
        idf = self._idf(term)
        denominator = tf + self._k1 * (1 - self._b + self._b * length / self._avg_length)
        return idf * (tf * (self._k1 + 1)) / denominator

    def _idf(self, term: str) -> float:
        cached = self._idf_cache.get(term)
        if cached is not None:
            return cached
        df = sum(
            1
            for frequencies in self._frequencies.values()
            if _matching_frequency(term, frequencies) > 0
        )
        if df <= 0 or self._doc_count <= 0:
            value = 0.0
        else:
            value = math.log(1 + (self._doc_count - df + 0.5) / (df + 0.5))
        self._idf_cache[term] = value
        return value


def _matching_frequency(term: str, frequencies: Counter[str]) -> int:
    return sum(count for token, count in frequencies.items() if term in token)
