"""Disposable text-vector helpers for optional enrichment similarity."""

from __future__ import annotations

import math
import re
from collections import Counter

_TOKEN_RE = re.compile(r"[a-z0-9_]+")


class TextEmbeddingCache:
    """In-memory cache of deterministic bag-of-words text vectors."""

    def __init__(self) -> None:
        self._cache: dict[str, Counter[str]] = {}

    def vector(self, text: str) -> Counter[str]:
        cached = self._cache.get(text)
        if cached is None:
            cached = Counter(_TOKEN_RE.findall(text.lower()))
            self._cache[text] = cached
        return cached


def enrichment_description(node: dict) -> str | None:
    props = node.get("props") or {}
    enrichment = props.get("enrichment")
    if not isinstance(enrichment, dict):
        return None
    description = enrichment.get("description")
    if not isinstance(description, str) or not description.strip():
        return None
    return description.strip()


def semantic_scores(
    query: str,
    matches: list[tuple[str, dict]],
    cache: TextEmbeddingCache | None,
) -> dict[str, float | None]:
    if cache is None:
        return {node_id: None for node_id, _ in matches}
    query_vector = cache.vector(query)
    return {
        node_id: _cosine(query_vector, cache.vector(description))
        if (description := enrichment_description(node)) is not None else None
        for node_id, node in matches
    }


def _cosine(left: Counter[str], right: Counter[str]) -> float:
    if not left or not right:
        return 0.0
    dot = sum(count * right.get(token, 0) for token, count in left.items())
    if dot <= 0:
        return 0.0
    left_norm = math.sqrt(sum(count * count for count in left.values()))
    right_norm = math.sqrt(sum(count * count for count in right.values()))
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    return dot / (left_norm * right_norm)
