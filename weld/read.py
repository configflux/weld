"""Product read command: the one bounded, shaped read envelope (ADR 0082).

Sits *between* core ``Graph.query`` / ``Graph.context`` / ``brief`` and the CLI
(``weld._graph_cli`` / ``weld.brief``) and MCP (``weld.mcp_server``) surfaces.
Both surfaces delegate here, so their agent-facing output is identical by
construction (the ADR 0083 thin-wrapper invariant).

Why a layer above the diet:

* ADR 0078's :func:`weld._envelope_diet.diet_envelope` filters noise neighbors
  and caps *fan-out count* at 50 -- but a count cap does not bound *bytes*: on
  this ~7k-node graph a dieted ``query`` is still 68-84 KB and ``brief`` (which
  the diet never covered) is 140-222 KB, both overflowing the agent tool cap
  (dogfood gap 6dmc).
* This module reuses that diet verbatim and adds a deterministic **byte budget**
  on top, and extends bounding to ``brief``. Core ``Graph.query`` / ``Graph.
  context`` stay untouched (ADR 0078's constraint: their internal callers --
  ``brief`` / ``trace`` / ``impact`` / ``path`` -- keep the full envelope).

The byte budget prunes in ADR 0078's existing total order
(:func:`weld._envelope_diet.neighbor_cap_sort_key`) so the two bounds are
consistent, and reports every drop -- never a silent truncation. Determinism
(ADR 0012): the budget is a pure function of content (a fixed canonical
serialization), pruning follows a total order over node ids, and matches/anchors
are never dropped, so the same graph + query yields a byte-identical envelope.
"""

from __future__ import annotations

import json

from weld._envelope_diet import diet_envelope, neighbor_cap_sort_key

#: Default byte budget for the agent-facing read envelope. 64 KiB is a
#: conservative fraction of the agent tool-result cap (~a quarter of a typical
#: 25 K-token budget) that bounds every observed 6dmc overflow while leaving a
#: normal multi-match query essentially whole. Measured against a canonical
#: ``indent=2`` serialization (the CLI's emit shape, larger than MCP's compact
#: one) so a fit here fits on both surfaces. Tests pass an explicit ``budget``.
DEFAULT_READ_BUDGET_BYTES: int = 65_536

#: Appended after ADR 0078's four ``omitted_neighbors`` reasons, in fixed order,
#: so the shaped envelope is byte-identical across runs (ADR 0012).
SIZE_CAPPED_REASON: str = "size_capped"

#: Brief buckets carrying node dicts, in the order the size budget walks them.
_BRIEF_BUCKETS: tuple[str, ...] = (
    "primary", "interfaces", "docs", "build", "boundaries",
)

_SIZE_WARNING: str = (
    "read-budget: omitted {dropped} lowest-priority node(s) to fit the bounded "
    "read envelope (size_capped); pass --full-size / full_size=True for the "
    "unbounded brief."
)


def _envelope_bytes(obj: object) -> int:
    """Return the canonical serialized byte length of *obj* (ADR 0012).

    Uses ``indent=2`` / ``ensure_ascii=False`` -- the CLI's ``_out`` emit shape,
    which is larger than the MCP server's compact ``json.dumps`` -- so a payload
    that fits this budget fits under either surface's actual serialization. A
    pure function of content: no wall-clock, no randomness, stable key order.
    """
    return len(json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8"))


def _node_id(node: dict) -> object:
    return node.get("id")


def _anchor_ids(envelope: dict) -> set:
    """Ids that must never be size-pruned: query ``matches`` + a context ``node``.

    Mirrors :func:`weld._envelope_diet` anchor semantics -- matches are the
    ranked answer and the context focal node is the subject of the lookup, so
    the byte budget only ever prunes *neighbors*, never the result itself.
    """
    ids: set = set()
    for match in envelope.get("matches") or []:
        mid = match.get("id")
        if mid is not None:
            ids.add(mid)
    node = envelope.get("node")
    if isinstance(node, dict) and node.get("id") is not None:
        ids.add(node["id"])
    return ids


def _dedangle(edges: list[dict], keep_ids: set) -> list[dict]:
    """Keep only edges whose *both* endpoints are in *keep_ids* (no dangles)."""
    return [
        edge for edge in edges
        if edge.get("from") in keep_ids and edge.get("to") in keep_ids
    ]


def _size_cap_neighbors(
    dieted: dict, budget: int,
) -> tuple[list[dict], list[dict], int]:
    """Prune neighbors by :func:`neighbor_cap_sort_key` until under *budget*.

    Returns ``(neighbors, edges, dropped)``. Selection keeps the highest-priority
    survivors (project, then real external packages, then the rest) and re-emits
    them in the caller's id-sorted order; their now-dangling edges are dropped
    with them. The envelope size is monotonic non-decreasing in the number of
    neighbors kept, so a binary search finds the largest fitting prefix in
    ``O(log n)`` serializations. Anchors (matches / focal node) are never
    counted against the budget as droppable.
    """
    kept = list(dieted.get("neighbors") or [])
    edges = list(dieted.get("edges") or [])
    total = len(kept)
    if total == 0:
        return kept, edges, 0
    anchors = _anchor_ids(dieted)
    base_omitted = dieted.get("omitted_neighbors") or {}
    ranked = sorted(kept, key=neighbor_cap_sort_key)

    def _candidate(keep_count: int) -> dict:
        keep_ids = {_node_id(n) for n in ranked[:keep_count]}
        neighbors_k = [n for n in kept if _node_id(n) in keep_ids]
        edges_k = _dedangle(edges, anchors | keep_ids)
        return {
            **dieted,
            "neighbors": neighbors_k,
            "edges": edges_k,
            "omitted_neighbors": {
                **base_omitted, SIZE_CAPPED_REASON: total - keep_count,
            },
        }

    lo, hi, best = 0, total, 0
    while lo <= hi:
        mid = (lo + hi) // 2
        if _envelope_bytes(_candidate(mid)) <= budget:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    keep_ids = {_node_id(n) for n in ranked[:best]}
    neighbors_out = [n for n in kept if _node_id(n) in keep_ids]
    edges_out = _dedangle(edges, anchors | keep_ids)
    return neighbors_out, edges_out, total - best


def shape_read_envelope(
    envelope: dict, *, full: bool = False, full_size: bool = False,
    budget: int = DEFAULT_READ_BUDGET_BYTES,
) -> dict:
    """Return the bounded, shaped copy of a ``query`` / ``context`` *envelope*.

    Composition:

    1. **Diet** (ADR 0078, reused verbatim): drop stdlib/unresolved/speculative-
       external neighbors, de-dangle their edges, cap fan-out at 50, annotate
       ``neighbors_filtered`` + ``omitted_neighbors``. ``full=True`` skips it and
       returns the raw envelope byte-identically (the ``--full-neighborhood`` /
       ``full_neighborhood`` escape hatch); an error payload / any payload with
       no ``neighbors`` key also passes straight through.
    2. **Byte budget** (ADR 0082, this module): append a ``size_capped`` reason
       to ``omitted_neighbors`` (fixed order) and, unless *full_size*, prune the
       lowest-priority neighbors until the serialized envelope fits *budget*.

    ``full`` returns the raw envelope; ``full_size`` keeps the diet but skips the
    byte budget (``size_capped`` is present and 0). Never silent-truncates:
    every dropped neighbor is counted in ``omitted_neighbors``.
    """
    dieted = diet_envelope(envelope, full=full)
    if full or "neighbors" not in envelope:
        return dieted
    omitted = dict(dieted.get("omitted_neighbors") or {})
    if full_size:
        omitted[SIZE_CAPPED_REASON] = 0
        return {**dieted, "omitted_neighbors": omitted}
    neighbors, edges, dropped = _size_cap_neighbors(dieted, budget)
    omitted[SIZE_CAPPED_REASON] = dropped
    return {
        **dieted,
        "neighbors": neighbors,
        "edges": edges,
        "omitted_neighbors": omitted,
    }


def _filter_speculative_matches(envelope: dict) -> dict:
    """Drop unresolved-sentinel matches and re-derive a consistent envelope.

    Relocated verbatim from ``weld._query_surface`` so the speculative-match
    filter lives beside the shaping it composes with -- the one query read
    command (ADR 0083). Returns *envelope* unchanged when it carries no
    ``matches`` (a context envelope or error payload), so :func:`read_query` is
    a safe no-op there. Imports are local: :mod:`weld.ranking` and
    :mod:`weld._query_envelope` are heavier than this hot path needs at import.
    """
    from weld._query_envelope import trim_envelope_to_matches
    from weld.ranking import filter_speculative_matches

    matches = envelope.get("matches")
    if not matches:
        return envelope
    surviving_ids = {
        m["id"] for m in filter_speculative_matches(matches) if "id" in m
    }
    return trim_envelope_to_matches(envelope, surviving_ids)


def read_query(
    envelope: dict, *, include_speculative: bool = False, full: bool = False,
    full_size: bool = False, budget: int = DEFAULT_READ_BUDGET_BYTES,
) -> dict:
    """Return the shaped ``query`` read answer (ADR 0083 thin-wrapper invariant).

    The one query read command both surfaces call, so ``wd query --json`` and
    ``weld_query`` return byte-identical answers by construction. Composition:

    1. **Speculative-match filter** (ADR 0078). Unless *include_speculative*,
       drop ``origin=unresolved`` sentinels from ``matches`` and re-derive a
       self-consistent ``neighbors`` / ``edges`` for the survivors (via
       :func:`weld._query_envelope.trim_envelope_to_matches`). This is the
       behaviour the CLI has always had; routing it *here* -- rather than at
       each surface -- is what closes the ADR 0083 asymmetry, so ``weld_query``
       's ``matches`` now equal ``wd query``'s.
    2. **Bounded read shaping** -- :func:`shape_read_envelope` (the ADR 0078
       neighbor diet + the ADR 0082 byte budget), with *full* / *full_size*
       passed through unchanged.

    ``context`` has no ``matches`` and calls :func:`shape_read_envelope`
    directly; only ``query`` needs the speculative step.
    """
    if not include_speculative:
        envelope = _filter_speculative_matches(envelope)
    return shape_read_envelope(
        envelope, full=full, full_size=full_size, budget=budget,
    )


def _brief_node_ids(brief_env: dict) -> set:
    """Return the id set of every node emitted across the brief's buckets."""
    ids: set = set()
    for bucket in _BRIEF_BUCKETS:
        for node in brief_env.get(bucket) or []:
            nid = node.get("id")
            if nid is not None:
                ids.add(nid)
    return ids


def _size_cap_brief(brief_env: dict, budget: int) -> tuple[dict, int]:
    """Drop the lowest-priority bucket nodes until the brief fits *budget*.

    Ranks every bucket node by :func:`neighbor_cap_sort_key` and keeps the
    highest-priority prefix that fits, preserving each bucket's own order and
    re-de-dangling ``edges`` to the surviving node set. Monotonic in the number
    kept, so a binary search finds the largest fitting set. Returns
    ``(brief, dropped_count)``.
    """
    all_nodes = [
        node for bucket in _BRIEF_BUCKETS for node in (brief_env.get(bucket) or [])
    ]
    total = len(all_nodes)
    if total == 0:
        return brief_env, 0
    ranked_ids = [_node_id(n) for n in sorted(all_nodes, key=neighbor_cap_sort_key)]

    def _candidate(keep_count: int) -> dict:
        keep = set(ranked_ids[:keep_count])
        env = dict(brief_env)
        for bucket in _BRIEF_BUCKETS:
            env[bucket] = [
                n for n in (brief_env.get(bucket) or []) if _node_id(n) in keep
            ]
        env["edges"] = _dedangle(brief_env.get("edges") or [], keep)
        return env

    lo, hi, best = 0, total, 0
    while lo <= hi:
        mid = (lo + hi) // 2
        if _envelope_bytes(_candidate(mid)) <= budget:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return _candidate(best), total - best


def shape_brief(
    brief_env: dict, *, full: bool = False, full_size: bool = False,
    budget: int = DEFAULT_READ_BUDGET_BYTES,
) -> dict:
    """Return the bounded, shaped copy of a ``brief`` envelope (ADR 0082).

    ``brief`` was previously un-dieted and is the largest 6dmc overflow: it
    emitted every edge from an over-fetched internal query, hundreds of which
    dangled onto nodes no bucket contained. Shaping:

    1. **Edge de-dangle** (the diet, extended to brief): keep only edges whose
       both endpoints are emitted bucket nodes. This alone bounds brief from
       140-222 KB to 17-27 KB on this graph and is a strict self-consistency
       gain (those edges pointed at nodes the brief never included).
    2. **Byte budget**: unless *full_size*, if the brief still exceeds *budget*,
       drop the lowest-priority bucket nodes and record the count in
       ``warnings`` (brief has no ``neighbors`` list; ``warnings`` is its
       existing visible-omission channel -- never a silent truncation).

    ``full=True`` returns the raw brief unchanged (escape hatch). The brief key
    set is unchanged.
    """
    if full or not isinstance(brief_env, dict):
        return brief_env
    ids = _brief_node_ids(brief_env)
    shaped = {**brief_env, "edges": _dedangle(brief_env.get("edges") or [], ids)}
    if full_size or _envelope_bytes(shaped) <= budget:
        return shaped
    shaped, dropped = _size_cap_brief(shaped, budget)
    if dropped:
        warnings = list(shaped.get("warnings") or [])
        warnings.append(_SIZE_WARNING.format(dropped=dropped))
        shaped = {**shaped, "warnings": warnings}
    return shaped
