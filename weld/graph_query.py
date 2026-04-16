"""Query execution helper for :class:`weld.graph.Graph`."""

from __future__ import annotations

from weld.embeddings import semantic_scores
from weld.ranking import rank_query_matches
from weld.synonyms import candidate_nodes_grouped, expand_token_groups


def query_graph(graph: object, term: str, limit: int = 20) -> dict:
    """Run tokenized graph query while preserving Graph.query's envelope."""
    tokens = term.lower().split()
    if not tokens:
        return {"query": term, "matches": [], "neighbors": [], "edges": []}
    graph._ensure_query_state()
    token_groups = expand_token_groups(tokens)
    candidates = candidate_nodes_grouped(graph._inverted_index, token_groups)
    if candidates is not None and not candidates:
        return {"query": term, "matches": [], "neighbors": [], "edges": []}
    if candidates is None:
        candidate_iter = graph._data["nodes"].items()
    else:
        candidate_iter = (
            (nid, graph._data["nodes"][nid])
            for nid in candidates
            if nid in graph._data["nodes"]
        )
    matched: list[tuple[str, dict]] = []
    for node_id, node in candidate_iter:
        if graph._match_token_groups(token_groups, node_id, node):
            matched.append((node_id, node))
    ranked = rank_query_matches(
        matched,
        token_groups,
        graph._bm25,
        graph._structural_scores,
        semantic=semantic_scores(term, matched, graph._embedding_cache),
    )
    matches = [{"id": node_id, **node} for node_id, node in ranked[:limit]]
    match_ids = {match["id"] for match in matches}
    neighbors, edges = graph._neighborhood(match_ids)
    return {"query": term, "matches": matches, "neighbors": neighbors, "edges": edges}
