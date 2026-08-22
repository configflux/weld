"""Bounded read shaping for the traversal surfaces (ADR 0082).

The sibling of :mod:`weld.read`, which bounds ``query`` / ``context`` /
``brief``. This module covers the reads that walk *relationships* --
``impact`` / ``callers`` / ``references`` / ``trace`` -- and it exists for the
same reason ADR 0082 gave for the first three: an unbounded envelope is not a
usable answer.

Those four were left out of ADR 0082's first pass, and the gap is not
theoretical. Measured on this repository's own graph, before this module:

===============================================  ==========
read                                             bytes
===============================================  ==========
``impact('weld/_federation_staleness.py')``         200,852
``impact('weld/_graph_cli.py')``                    180,930
``callers('symbol:py:pathlib:Path', depth=5)``    1,406,886
``references('append')``                            725,271
===============================================  ==========

Every one of those overflows the agent tool-result cap, so the tool returned
nothing an agent could read -- and the failure is worst exactly where the
answer matters most, on the high-fan-out nodes whose blast radius is the
reason to ask. ``trace`` measured 41,220 bytes at its widest on this graph,
under the budget today, but its ``depth`` and ``seed_limit`` are caller-chosen
and its slice grows with the graph, so it is bounded here too rather than left
to be the next report.

Both surfaces delegate here -- ``wd <cmd> --json`` and the matching MCP tool --
so their answers are identical by construction (the ADR 0083 thin-wrapper
invariant), and ``--full-size`` / ``full_size=True`` is the escape hatch on
each, exactly as on ``query`` / ``context`` / ``brief``.

Core stays untouched: :func:`weld.impact_core.impact`, ``Graph.callers``,
``Graph.references`` and :func:`weld.trace.trace` still return the full
envelope to their internal callers (ADR 0078's constraint), because shaping is
a layer *above* them, not inside them.

``impact``'s shaper moved to :mod:`weld._read_impact` at bd gfpl and is
re-exported here, so this module stays the one import site for all four. It is
the only one of the four whose droppable items are nested and whose envelope
carries a contract about what may not shrink; the other three are flat lists.
"""

from __future__ import annotations

from weld._envelope_diet import neighbor_cap_sort_key
from weld._read_budget import (
    BUDGET_EXCEEDED_KEY,
    EFFECTIVE_READ_BUDGET_BYTES,
    READ_BUDGET_MESSAGE,
    SIZE_CAPPED_KEY,
    envelope_bytes,
    exceeds_budget,
    fit_buckets,
    is_shapeable,
)
# ``impact`` lives next door in :mod:`weld._read_impact` -- it is the one
# bounded read with nested droppable buckets and a no-shrink safety contract.
# Re-exported so both surfaces keep importing every traversal shaper from here.
from weld._read_impact import shape_impact

__all__ = ["shape_callers", "shape_impact", "shape_references", "shape_trace"]

#: Trace's bucket keys (:func:`weld.trace.trace` emits all five).
_TRACE_BUCKETS: tuple[str, ...] = (
    "services", "interfaces", "contracts", "boundaries", "verifications",
)


def _by_node_quality(_bucket: str, node: dict) -> tuple:
    """Rank a node by ADR 0078's total order, ignoring which bucket it is in.

    The same selection key the fan-out cap and the ``query`` byte budget use,
    so all three bounds prune consistently and the result is byte-identical
    across runs (ADR 0012).
    """
    return neighbor_cap_sort_key(node)


#: References' retention priority: the resolution answer first, then its
#: resolved callers, and finally the textual file hits.
_REFERENCES_ORDER: dict[str, int] = {"matches": 0, "callers": 1, "files": 2}


def _references_rank(bucket: str, item: dict) -> tuple:
    """Rank a references item by bucket priority, then within the bucket.

    The bucket leads, so the resolution answer (``matches``) survives a flood
    of textual file hits rather than competing with it on node quality. File
    hits carry no node id (they are ``{path, tokens, score}`` from the file
    index), so their score and path supply the total order instead.
    """
    order = _REFERENCES_ORDER[bucket]
    if bucket == "files":
        score = item.get("score")
        return (
            order,
            -(score if isinstance(score, int) else 0),
            str(item.get("path", "")),
        )
    return (order, neighbor_cap_sort_key(item))


def shape_callers(
    envelope: dict, *, full_size: bool = False,
    budget: int = EFFECTIVE_READ_BUDGET_BYTES,
) -> dict:
    """Return the bounded ``callers`` envelope (ADR 0082).

    A transitive caller walk over a hot symbol is the widest read weld
    offers -- ``callers('symbol:py:pathlib:Path', depth=5)`` reaches 1,072
    callers and 1.4 MB on this graph. Lowest-priority callers are dropped in
    ADR 0078's node-quality order (project code outlives stdlib and unresolved
    sentinels) and the count lands in ``size_capped``, which is always present.
    """
    return _shape_flat(
        envelope, buckets=("callers",), rank_key=_by_node_quality,
        full_size=full_size, budget=budget,
    )


def shape_references(
    envelope: dict, *, full_size: bool = False,
    budget: int = EFFECTIVE_READ_BUDGET_BYTES,
) -> dict:
    """Return the bounded ``references`` envelope (ADR 0082).

    ``references`` unions three unbounded lists -- resolved ``matches``, their
    ``callers``, and file-index ``files`` -- so a common identifier explodes it
    (``references('append')`` is 725,271 bytes here). ``matches`` is ranked
    first because it is the resolution answer the caller asked for; file hits
    are ranked by their index score and drop before a resolved caller does.

    Call this **after** the surface has attached ``files``: the field is added
    by ``wd references`` / ``weld_references`` rather than by
    ``Graph.references``, and a budget that ran before it would bound the wrong
    payload.
    """
    return _shape_flat(
        envelope, buckets=("matches", "callers", "files"),
        rank_key=_references_rank, full_size=full_size, budget=budget,
    )


def shape_trace(
    envelope: dict, *, full_size: bool = False,
    budget: int = EFFECTIVE_READ_BUDGET_BYTES,
) -> dict:
    """Return the bounded ``trace`` envelope (ADR 0082).

    Trace is the one surface here that does not overflow on this repository
    (41,220 bytes at ``depth=6, seed_limit=50``, its saturation point). It is
    bounded anyway because that number is a property of *this* graph, not of
    the contract: the slice grows with the codebase and the caller picks the
    depth. Nodes are ranked purely on quality across the five buckets, the way
    ``brief`` ranks across its own, and the drop is reported through trace's
    existing ``warnings`` list -- so a payload that never caps looks exactly as
    it does today.
    """
    if not is_shapeable(envelope, _TRACE_BUCKETS) or full_size:
        return envelope
    if envelope_bytes(envelope) <= budget:
        return envelope

    def annotate(env: dict, dropped: dict[str, int], _edges: int) -> dict:
        # Inside the fit check, not after it: the warning is part of the
        # payload, so a budget that ignored it would hand back an envelope
        # that breaches the cap by the length of its own apology.
        total = sum(dropped.values())
        if not total:
            return env
        return {**env, "warnings": [
            *(env.get("warnings") or []),
            READ_BUDGET_MESSAGE.format(dropped=total, noun="slice node(s)"),
        ]}

    shaped, _dropped, _dropped_edges = fit_buckets(
        envelope, buckets=_TRACE_BUCKETS, budget=budget,
        rank_key=_by_node_quality, annotate=annotate,
    )
    return shaped


def _shape_flat(
    envelope: dict, *, buckets: tuple[str, ...], rank_key,
    full_size: bool, budget: int,
) -> dict:
    """Bound a ``callers`` / ``references`` envelope and stamp ``size_capped``.

    Both have a flat ``{symbol, ...lists, edges}`` shape with no warnings
    channel of their own, so the report *is* the top-level ``size_capped``
    object: per-bucket drop counts plus ``edges``, always present so the key
    set stays invariant whether or not the budget fired and a consumer never
    has to probe. Only the buckets the envelope actually carries are ranked,
    so an unattached ``files`` list never shifts another bucket's priority.
    """
    if not is_shapeable(envelope, buckets):
        return envelope
    present = tuple(b for b in buckets if b in envelope)

    def annotate(env: dict, dropped: dict[str, int], dropped_edges: int) -> dict:
        return {
            **env,
            SIZE_CAPPED_KEY: {**dropped, "edges": dropped_edges},
            BUDGET_EXCEEDED_KEY: False,
        }

    if full_size:
        return annotate(dict(envelope), dict.fromkeys(present, 0), 0)
    shaped, _dropped, _dropped_edges = fit_buckets(
        envelope, buckets=present, budget=budget, rank_key=rank_key,
        annotate=annotate,
    )
    if exceeds_budget(shaped, budget):
        # Same floor as impact: these envelopes carry a symbol header the loop
        # cannot prune, so "capped" and "fits" are not the same claim.
        shaped = {**shaped, BUDGET_EXCEEDED_KEY: True}
    return shaped
