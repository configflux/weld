"""OR-fallback retrieval for the eager federation index.

Split out of :mod:`weld._federation_eager_index` so that module stays under
the line-count cap; the logic mirrors
:func:`weld._sqlite_query._or_fallback` (the lazy/sqlite per-child path) and
:func:`weld.graph_query.query_or_fallback` (the in-memory path). A free
function parameterized by the per-child accessors the eager index already
holds, so it carries no class coupling.

Why this exists: the lazy and eager federation paths must return the same
per-child match list (the federation correctness test asserts match-set
parity). The lazy path relaxes a zero-strict-AND multi-token query to the
per-group UNION; without the same relaxation here the eager path would return
an empty list where the lazy path returns ranked union results.

The ranking key is the shared
:func:`weld._coverage_admission.or_fallback_sort_key`
(``group_hits_desc, subject tie-break, BM25_desc, id``), so all three impls
order identically. The caller supplies the per-child BM25 scores (computed
from the index's in-memory dicts), so this helper stays free of any corpus
coupling.
"""

from __future__ import annotations

from typing import Callable

from weld._coverage_admission import count_groups_hit, or_fallback_sort_key


def candidates_union(
    per_token: dict[str, set[str]],
    token_groups: list[list[str]],
) -> set[str]:
    """Return the UNION of node ids across all groups for one child.

    Reads the eager index's per-child ``token -> {node_id}`` map (no sqlite
    round-trip). Used by BOTH the bounded-coverage admission tier and the
    OR-fallback relaxation, so a 3/4 node the strict-AND intersection dropped
    is reachable. Empty tokens are skipped (they match nothing).
    """
    if not per_token:
        return set()
    union: set[str] = set()
    for group in token_groups:
        for tok in group:
            if not tok:
                continue
            for indexed_token, ids in per_token.items():
                if tok in indexed_token:
                    union.update(ids)
    return union


def or_fallback_child_matches(
    *,
    union: set[str],
    get_node: Callable[[str], dict | None],
    bm25_scores_for: Callable[[list[tuple[str, dict]]], dict[str, float]],
    token_groups: list[list[str]],
    limit: int,
) -> list[dict]:
    """Return the OR-fallback match list for one child (eager path).

    Counts per-group hits over the *union* candidates with the shared
    :func:`count_groups_hit`, ranks via :func:`or_fallback_sort_key`, and
    returns the top-*limit* match dicts (``{"id": ..., **node}``). Returns an
    empty list when no union candidate hits any group.

    *bm25_scores_for* receives the matched ``[(node_id, node), ...]`` list and
    returns ``{node_id: score}`` -- the eager index supplies its in-memory
    per-child scorer, matching what the lazy path computes from sqlite.
    """
    matched: list[tuple[str, dict]] = []
    group_hits: dict[str, int] = {}
    for node_id in sorted(union):
        node = get_node(node_id)
        if node is None:
            continue
        hits = count_groups_hit(token_groups, node_id, node)
        if hits > 0:
            matched.append((node_id, node))
            group_hits[node_id] = hits
    if not matched:
        return []
    bm25_scores = bm25_scores_for(matched)

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
    return [{"id": node_id, **node} for node_id, node in ranked[:limit]]
