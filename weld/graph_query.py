"""Query execution helper for :class:`weld.graph.Graph`."""

from __future__ import annotations

from weld.embeddings import semantic_scores
from weld.ranking import rank_query_matches
from weld.synonyms import candidate_nodes_grouped, expand_token_groups


def query_graph(graph: object, term: str, limit: int = 20) -> dict:
    """Run tokenized graph query while preserving Graph.query's envelope.

    Behavior:
    - Alias short-circuit (ADR 0041): when the entire ``term`` matches
      a legacy node ID recorded in ``props.aliases``, resolve it to the
      canonical id and return that single canonical node + its 1-hop
      neighborhood. This lets transcripts that paste a pre-rename ID
      keep working through ``wd query``.
    - Strict-AND: every token group must hit at least one field on a node.
    - OR-fallback: when strict-AND yields zero matches on a *multi-token*
      query, retry via :func:`query_or_fallback` (per-group union ranked
      by group-hit count, then BM25, then node id) and tag the envelope
      with ``degraded_match: 'or_fallback'`` so consumers know the result
      was not strict-AND. Single-token queries skip the fallback because
      OR == AND for one group.

    The fallback is silent (no warning emitted in the envelope itself --
    the ``degraded_match`` flag is the contract). ``brief()`` and
    ``trace()`` retain their own fallback paths and warning copy.
    """
    tokens = term.lower().split()
    if not tokens:
        return {"query": term, "matches": [], "neighbors": [], "edges": []}
    graph._ensure_query_state()
    alias_match = _alias_short_circuit(graph, term, limit)
    if alias_match is not None:
        return alias_match
    token_groups = expand_token_groups(tokens)
    candidates = candidate_nodes_grouped(graph._inverted_index, token_groups)
    if candidates is not None and not candidates:
        return _maybe_or_fallback(graph, term, tokens, limit)
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
    if not matched:
        return _maybe_or_fallback(graph, term, tokens, limit)
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


def _alias_short_circuit(graph: object, term: str, limit: int) -> dict | None:
    """Return a one-match envelope if ``term`` is a registered legacy alias.

    Only fires when the *exact* (case-preserving) ``term`` is a
    registered alias entry that points to a canonical node id. Plain
    canonical-id queries fall through to the normal BM25 path so that
    users who type ``wd query <canonical_id>`` keep getting the
    description / substring matches they would have gotten before
    ADR 0041 (alias short-circuit must not break the existing
    "search by id substring" UX). Returns ``None`` when ``term`` is
    not an alias, so the normal BM25 path runs.

    The envelope mirrors :func:`query_graph` so callers cannot
    distinguish an alias-short-circuit hit from a normal one-match
    result; that is intentional -- alias resolution is a guaranteed
    rewrite to the canonical node and should be invisible to callers.
    """
    nodes = graph._data["nodes"]  # type: ignore[attr-defined]
    alias_index = getattr(graph, "_alias_index", {})
    # Only fire on legacy aliases, NOT on canonical ids that happen to
    # be exact substrings of themselves -- BM25 already handles those
    # and may surface multiple useful description-substring matches.
    if term in nodes:
        return None
    canonical = alias_index.get(term)
    if canonical is None or canonical not in nodes:
        return None
    node = nodes[canonical]
    matches = [{"id": canonical, **node}][:limit]
    match_ids = {canonical}
    neighbors, edges = graph._neighborhood(match_ids)  # type: ignore[attr-defined]
    return {"query": term, "matches": matches, "neighbors": neighbors, "edges": edges}


def _maybe_or_fallback(
    graph: object, term: str, tokens: list[str], limit: int
) -> dict:
    """Return OR-fallback result for multi-token queries; else empty envelope.

    Single-token queries skip the fallback because the OR path would
    return identical results to the (already-empty) strict-AND path.
    Multi-token queries that also find nothing via OR return an honestly
    empty envelope with no ``degraded_match`` flag.
    """
    empty = {"query": term, "matches": [], "neighbors": [], "edges": []}
    if len(tokens) <= 1:
        return empty
    fallback = query_or_fallback(graph, term, limit=limit)
    if not fallback.get("matches"):
        return empty
    fallback["degraded_match"] = "or_fallback"
    return fallback


def _candidate_nodes_or(
    index: dict[str, set[str]],
    token_groups: list[list[str]],
) -> set[str] | None:
    """Return the UNION of candidate node IDs across all token groups.

    Mirrors :func:`weld.synonyms.candidate_nodes_grouped` but uses union
    instead of intersection across groups so callers can build an OR
    fallback path. Returns ``None`` when the index is empty (caller should
    fall back to a full scan).
    """
    if not index:
        return None
    union: set[str] = set()
    for group in token_groups:
        for tok in group:
            for indexed_token, node_ids in index.items():
                if tok in indexed_token:
                    union |= node_ids
    return union


def _count_groups_hit(token_groups: list[list[str]], nid: str, node: dict) -> int:
    """Return how many of ``token_groups`` are hit by ``(nid, node)``.

    Unlike :meth:`Graph._match_token_groups`, this does NOT short-circuit on
    a missing group -- it counts partial hits so OR-fallback callers can
    rank by ``num_groups_hit_desc``.
    """
    nid_l = nid.lower()
    label_l = node.get("label", "").lower()
    props = node.get("props") or {}
    file_l = (props.get("file") or "").lower()
    exports_l = [e.lower() for e in props.get("exports", []) if isinstance(e, str)]
    desc_l = (props.get("description") or "").lower()
    hits = 0
    for group in token_groups:
        if any(
            t in nid_l
            or t in label_l
            or t in file_l
            or t in desc_l
            or any(t in e for e in exports_l)
            for t in group
        ):
            hits += 1
    return hits


def query_or_fallback(graph: object, term: str, limit: int = 20) -> dict:
    """Soft retrieval path used by :func:`query_graph` when strict-AND zeroes.

    Unions per-group candidates (instead of intersecting them) and ranks the
    survivors by ``(num_groups_hit_desc, BM25_desc, node_id_asc)`` so nodes
    matching more concepts surface first. Returns the same envelope shape as
    :func:`query_graph` so callers can swap the result in-place.

    Notes
    -----
    - :func:`query_graph` invokes this path automatically on multi-token
      queries when strict-AND yields zero matches and tags the result with
      ``degraded_match: 'or_fallback'`` so consumers can detect the
      relaxation. Direct callers (e.g., custom tools) may invoke
      :func:`query_or_fallback` themselves if they want OR semantics
      unconditionally; in that case they are responsible for adding their
      own ``degraded_match`` marker.
    """
    tokens = term.lower().split()
    if not tokens:
        return {"query": term, "matches": [], "neighbors": [], "edges": []}
    graph._ensure_query_state()
    token_groups = expand_token_groups(tokens)
    candidates = _candidate_nodes_or(graph._inverted_index, token_groups)
    if candidates is None:
        candidate_iter = graph._data["nodes"].items()
    else:
        candidate_iter = (
            (nid, graph._data["nodes"][nid])
            for nid in candidates
            if nid in graph._data["nodes"]
        )
    matched: list[tuple[str, dict]] = []
    group_hits: dict[str, int] = {}
    for node_id, node in candidate_iter:
        hits = _count_groups_hit(token_groups, node_id, node)
        if hits > 0:
            matched.append((node_id, node))
            group_hits[node_id] = hits
    if not matched:
        return {"query": term, "matches": [], "neighbors": [], "edges": []}
    bm25 = graph._bm25
    def _key(item: tuple[str, dict]) -> tuple[int, float, str]:
        node_id, _ = item
        bm25_score = bm25.score(node_id, token_groups) if bm25 else 0.0
        return (-group_hits[node_id], -bm25_score, node_id)
    ranked = sorted(matched, key=_key)
    matches = [{"id": node_id, **node} for node_id, node in ranked[:limit]]
    match_ids = {match["id"] for match in matches}
    neighbors, edges = graph._neighborhood(match_ids)
    return {
        "query": term,
        "matches": matches,
        "neighbors": neighbors,
        "edges": edges,
    }
