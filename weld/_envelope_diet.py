"""Query/context envelope neighbor diet (bd d1oc).

The raw ``Graph.query`` / ``Graph.context`` envelope walks every 1-hop edge
and returns every adjacent node as a full dict with no origin filter and no
fan-out cap. On this repo a single ``wd query telemetry --json`` was ~100 KB
(~26 K tokens): 24/81 neighbors were stdlib symbols, 12 were
``symbol:unresolved`` sentinels, and hub nodes reach degree 1011. That spray
is pure noise to an agent -- it cannot discount a stdlib callee or an
unresolved sentinel, and a hub blows the token budget.

:func:`diet_envelope` is a pure projection over a query/context envelope
(same class of helper as :func:`weld._query_envelope.trim_envelope_to_matches`)
that, by default:

* drops neighbors whose ``props.origin`` is ``stdlib`` or ``unresolved`` (and
  any ``symbol:unresolved:`` id), plus speculative *external symbols* (external
  *package* nodes are kept -- a real third-party dependency carries signal);
* drops edges that would dangle once those neighbors are gone;
* caps the surviving neighbor fan-out at :data:`DEFAULT_NEIGHBOR_CAP` with a
  deterministic selection key so a hub cannot blow up the envelope;
* annotates the result with ``neighbors_filtered: true`` and an
  ``omitted_neighbors`` count-by-reason object -- there is *no silent
  truncation*, an agent can always see what and how much was hidden.

The escape hatch (``full=True``, wired to the CLI ``--full-neighborhood`` flag
and the MCP ``full_neighborhood`` parameter) returns the envelope unchanged so
the full, raw neighborhood is one flag away.

Determinism (ADR 0012): the surviving neighbor list preserves the caller's
input order (``compute_neighborhood`` emits id-sorted neighbors) and the cap's
selection key is a total order over node ids, so the same graph + same query
yields a byte-identical envelope.

The diet is applied at the CLI and MCP *surface* boundaries only; the core
``Graph.query`` / ``Graph.context`` and direct API callers (``brief`` /
``trace`` / ``impact``) keep receiving the full envelope, mirroring how the
speculative-match filter stays a surface concern.
"""

from __future__ import annotations

from weld.ranking import authority_score, confidence_score

#: Global cap on the number of neighbors an envelope may carry after the
#: origin filter. Chosen so a normal multi-match query (telemetry keeps 45
#: project neighbors) is never capped -- the cap is a safety valve for hub
#: adjacency, not a routine trim. Overflow is reported as ``fanout_capped``.
DEFAULT_NEIGHBOR_CAP: int = 50

#: Fixed, stable order for the ``omitted_neighbors`` reason keys so the JSON
#: envelope is byte-identical across runs (ADR 0012).
OMISSION_REASONS: tuple[str, ...] = (
    "stdlib",
    "unresolved",
    "external_symbol",
    "fanout_capped",
)

_UNRESOLVED_ID_PREFIX: str = "symbol:unresolved:"

#: Selection priority for the fan-out cap: keep project neighbors first, then
#: real external *package* deps, then everything else. Lower is kept sooner.
_ORIGIN_CAP_RANK: dict[str, int] = {"project": 0, "external": 1}


def neighbor_exclude_reason(node: dict) -> str | None:
    """Return the diet reason a neighbor *node* is excluded, else ``None``.

    * ``stdlib`` -- ``props.origin == "stdlib"`` (stdlib package/symbol noise);
    * ``unresolved`` -- ``props.origin == "unresolved"`` or the id begins with
      ``symbol:unresolved:`` (call-graph closure sentinel a callee could not be
      linked to a definition);
    * ``external_symbol`` -- a speculative external *symbol*
      (``props.origin == "external"`` and ``type == "symbol"``). External
      *package* nodes are kept: a real third-party dependency carries signal.

    Any other node (``project``, origin-less, external package) is kept.
    """
    node_id = node.get("id", "")
    if isinstance(node_id, str) and node_id.startswith(_UNRESOLVED_ID_PREFIX):
        return "unresolved"
    props = node.get("props") or {}
    origin = props.get("origin")
    if origin == "stdlib":
        return "stdlib"
    if origin == "unresolved":
        return "unresolved"
    if origin == "external" and node.get("type") == "symbol":
        return "external_symbol"
    return None


def neighbor_cap_sort_key(node: dict) -> tuple[int, int, int, str]:
    """Deterministic selection key for the fan-out cap (lower is kept).

    A total order over node ids: project neighbors first, then real external
    *package* deps, then everything else, tie-broken by authority, confidence,
    and finally id. Public so the ADR 0082 byte budget in :mod:`weld.read`
    prunes size-capped survivors in the *same* order the count cap uses, keeping
    the two bounds consistent and the output byte-identical (ADR 0012).
    """
    props = node.get("props") or {}
    origin_rank = _ORIGIN_CAP_RANK.get(props.get("origin"), 2)
    return (
        origin_rank,
        authority_score(node),
        confidence_score(node),
        str(node.get("id", "")),
    )


def _apply_cap(kept: list[dict], cap: int | None) -> tuple[list[dict], int]:
    """Bound *kept* to *cap* neighbors, preserving the caller's id-sorted order.

    Selection of which neighbors to keep is by :func:`neighbor_cap_sort_key` (a
    total order), but the returned list is filtered from the original *kept* so
    the id-sorted output contract of ``compute_neighborhood`` is preserved
    whether or not the cap fires. Returns ``(neighbors, dropped_count)``.
    """
    if cap is None or len(kept) <= cap:
        return kept, 0
    selected_ids = {
        n.get("id") for n in sorted(kept, key=neighbor_cap_sort_key)[:cap]
    }
    restored = [n for n in kept if n.get("id") in selected_ids]
    return restored, len(kept) - cap


def _anchor_ids(envelope: dict) -> set[str]:
    """Return the always-kept ids: query ``matches`` plus a context ``node``."""
    ids: set[str] = set()
    for match in envelope.get("matches") or []:
        mid = match.get("id")
        if mid is not None:
            ids.add(mid)
    node = envelope.get("node")
    if isinstance(node, dict):
        nid = node.get("id")
        if nid is not None:
            ids.add(nid)
    return ids


def diet_envelope(
    envelope: dict, *, full: bool = False, cap: int | None = DEFAULT_NEIGHBOR_CAP,
) -> dict:
    """Return a noise-filtered, fan-out-capped, annotated copy of *envelope*.

    Operates on the ``neighbors``/``edges`` of a query or context envelope
    (``matches`` for query, ``node`` for context are the always-kept anchors).
    When *full* is true, or the payload carries no ``neighbors`` key (e.g. a
    ``{"error": ...}`` context miss), the envelope is returned unchanged so the
    escape hatch and error paths stay byte-identical to the raw result.

    Otherwise returns a new envelope dict with:

    * ``neighbors`` -- the origin-filtered, cap-bounded survivors (input order
      preserved);
    * ``edges`` -- only edges whose *both* endpoints are an anchor or a
      surviving neighbor (no dangling endpoint on a dropped node);
    * ``neighbors_filtered: true`` -- a boolean marker an agent can key on;
    * ``omitted_neighbors`` -- ``{stdlib, unresolved, external_symbol,
      fanout_capped}`` counts (all four keys always present, fixed order).

    All other envelope keys pass through unchanged.
    """
    if full or "neighbors" not in envelope:
        return envelope
    neighbors = envelope.get("neighbors") or []
    edges = envelope.get("edges") or []
    omitted = {reason: 0 for reason in OMISSION_REASONS}
    kept: list[dict] = []
    for neighbor in neighbors:
        reason = neighbor_exclude_reason(neighbor)
        if reason is not None:
            omitted[reason] += 1
        else:
            kept.append(neighbor)
    kept, omitted["fanout_capped"] = _apply_cap(kept, cap)
    keep_ids = _anchor_ids(envelope)
    keep_ids.update(n.get("id") for n in kept if n.get("id") is not None)
    kept_edges = [
        edge for edge in edges
        if edge.get("from") in keep_ids and edge.get("to") in keep_ids
    ]
    return {
        **envelope,
        "neighbors": kept,
        "edges": kept_edges,
        "neighbors_filtered": True,
        "omitted_neighbors": omitted,
    }
