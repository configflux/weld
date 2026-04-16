"""Load-time query state derived from graph nodes and edges."""

from __future__ import annotations

from dataclasses import dataclass

from weld.bm25 import BM25Corpus
from weld.embeddings import TextEmbeddingCache, enrichment_description
from weld.query_index import build_index


@dataclass(frozen=True)
class QueryState:
    inverted_index: dict[str, set[str]]
    bm25: BM25Corpus
    structural_scores: dict[str, float]
    embedding_cache: TextEmbeddingCache | None


def build_query_state(nodes: dict[str, dict], edges: list[dict]) -> QueryState:
    return QueryState(
        inverted_index=build_index(nodes),
        bm25=BM25Corpus.from_nodes(nodes),
        structural_scores=_structural_scores(nodes, edges),
        embedding_cache=TextEmbeddingCache() if _has_enrichment(nodes) else None,
    )


def _structural_scores(nodes: dict[str, dict], edges: list[dict]) -> dict[str, float]:
    indegrees = {node_id: 0 for node_id in nodes}
    for edge in edges:
        target = edge.get("to")
        if target in indegrees:
            indegrees[target] += 1
    max_indegree = max(indegrees.values(), default=0)
    if max_indegree <= 0:
        return {node_id: 0.0 for node_id in nodes}
    return {node_id: count / max_indegree for node_id, count in indegrees.items()}


def _has_enrichment(nodes: dict[str, dict]) -> bool:
    return any(enrichment_description(node) is not None for node in nodes.values())
