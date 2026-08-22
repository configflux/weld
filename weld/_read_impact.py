"""Bounded read shaping for ``impact`` (ADR 0082, amended by bd gfpl).

Split out of :mod:`weld.read_traversal` when the bd gfpl fix pushed that module
past the 400-line cap. The seam is a real one rather than a convenience:
``impact`` is the only bounded read whose droppable items are *nested* (the
seven ``affected_surfaces`` buckets) and the only one carrying a safety
contract about what may **not** shrink -- the risk verdict and the per-bucket
counts. ``callers`` / ``references`` / ``trace`` are all flat top-level lists
with no such contract, and they stay next door.
"""

from __future__ import annotations

from weld._envelope_diet import neighbor_cap_sort_key
from weld._read_budget import (
    BUDGET_EXCEEDED_KEY,
    EFFECTIVE_READ_BUDGET_BYTES,
    OVER_BUDGET_MESSAGE,
    READ_BUDGET_MESSAGE,
    SIZE_CAPPED_KEY,
    exceeds_budget,
    fit_buckets,
    is_shapeable,
)

#: Impact's droppable top-level node lists, nearest-first. Order is documentary
#: only -- :func:`_impact_rank` prunes by hop distance, which already sorts
#: direct before transitive.
IMPACT_BUCKETS: tuple[str, ...] = ("direct_dependents", "transitive_dependents")

#: Synthetic bucket prefix used to hoist ``affected_surfaces``' per-bucket lists
#: to the top level for the duration of one :func:`fit_buckets` call, so the
#: dependents and the surface members are ranked against each other in **one**
#: pass. See :func:`shape_impact` for why that matters.
_SURFACE_PREFIX: str = "affected_surfaces."

#: Full per-bucket surface counts, always emitted, always measured over the
#: *unpruned* blast radius (bd gfpl).
SURFACE_COUNTS_KEY: str = "affected_surface_counts"


def _impact_rank(bucket: str, node: dict) -> tuple:
    """Rank an impact dependent or surface member by hop, then kind, then quality.

    Proximity is the blast-radius priority: a direct dependent is the thing
    that breaks first, and a 3-hop dependent is the thing an agent drops first.
    Hop is already an integer on every dependent and on every surface entry.

    At equal hop a *dependent* outlives a *surface member*, because the surface
    member is the cheaper loss: its bucket count survives pruning in
    ``affected_surface_counts`` no matter what, so dropping it costs the reader
    a name and not the fact.

    The node-quality tie-break ends in the node id, and ``fit_buckets`` breaks
    a residual tie by (bucket, position) through a stable sort, so this stays a
    total order (ADR 0012).
    """
    is_surface = 1 if bucket.startswith(_SURFACE_PREFIX) else 0
    if not isinstance(node, dict):
        # A surface bucket may hold a bare label rather than a node dict. Such
        # an entry is a handful of bytes and names a published contract, so it
        # ranks at hop 0 -- cheapest to keep, most expensive to lose -- and
        # tie-breaks on its own text to stay a total order.
        return (0, is_surface, (2, 0, 0, str(node)))
    return (node.get("hop", 0), is_surface, neighbor_cap_sort_key(node))


def shape_impact(
    envelope: dict, *, full_size: bool = False,
    budget: int = EFFECTIVE_READ_BUDGET_BYTES,
) -> dict:
    """Return the bounded ``impact`` envelope (ADR 0082, amended by bd gfpl).

    The **verdict** is never recomputed from survivors: ``risk_level`` and the
    per-bucket surface counts in ``affected_surface_counts`` stay exactly as
    :func:`weld.impact_core.impact` measured them over the *full* blast radius.
    Letting a payload that was merely too big come back reporting a smaller
    radius and a lower risk turns a size problem into a safety problem, and
    that remains forbidden.

    What changed at bd gfpl is *which lists* the budget may prune. ADR 0082
    read "summary fields are never pruned" as covering the whole
    ``affected_surfaces`` object, but that object is not a summary -- it is
    seven unbounded node lists. On this graph ``impact('weld/graph.py')``
    measured 129,104 B against a 65,536 B budget *after* the budget had already
    dropped all 790 dependents and 1,783 edges, because 104,056 B of it was
    ``affected_surfaces`` (588 ``tests`` entries) that the loop was not allowed
    to touch. The budget pruned the answer to nothing and still missed by 2x.

    So surface members are pruned too, in the *same* pass as the dependents --
    hoisted to synthetic top-level buckets for one :func:`fit_buckets` call and
    re-nested by the annotator. One pass is the whole point: ranked separately,
    the loop would empty the dependents list to reclaim 25 KB while leaving
    104 KB of test-file entries standing. The safety property survives intact
    because only the *members* go; the counts do not.

    Reporting, always present so a consumer never probes:

    * ``affected_surface_counts`` -- full per-bucket radius, unpruned;
    * ``warnings.size_capped`` -- dropped dependents, dropped edges, and a
      nested ``affected_surfaces`` map of dropped members per bucket;
    * ``warnings.budget_exceeded`` -- ``true`` when everything droppable was
      dropped and the payload is *still* over budget. That floor is real (a
      target header and the counts have no lower bound this loop controls) and
      it used to be silent.

    ``full_size=True`` skips the budget, reports zeros, and never flags.
    """
    if not is_shapeable(envelope, IMPACT_BUCKETS):
        return envelope
    raw = envelope.get("affected_surfaces")
    surfaces = raw if isinstance(raw, dict) else {}
    lists = {n: v for n, v in surfaces.items() if isinstance(v, list)}
    counts = {n: len(v) for n, v in lists.items()}
    buckets = (*IMPACT_BUCKETS, *(_SURFACE_PREFIX + n for n in lists))
    annotate = _impact_annotator(surfaces, counts)
    hoisted = {**envelope, **{_SURFACE_PREFIX + n: v for n, v in lists.items()}}
    if full_size:
        return annotate(hoisted, dict.fromkeys(buckets, 0), 0)
    shaped, _dropped, _dropped_edges = fit_buckets(
        hoisted, buckets=buckets, budget=budget,
        rank_key=_impact_rank, annotate=annotate,
    )
    if exceeds_budget(shaped, budget):
        shaped = _mark_over_budget(shaped)
    return shaped


def _impact_annotator(surfaces: dict, counts: dict[str, int]):
    """Return the annotator that re-nests hoisted surfaces and stamps the report.

    Runs *inside* ``fit_buckets``' fit check, so the payload it measures is the
    re-nested one a caller actually receives -- never the hoisted intermediate,
    which would double-count every surface member -- and the drop counts are
    part of the measured bytes.
    """
    def annotate(shaped: dict, dropped: dict[str, int], dropped_edges: int) -> dict:
        env = dict(shaped)
        if surfaces:
            # Assigned back onto the existing key, so ``affected_surfaces``
            # keeps its original position and bucket order (ADR 0012).
            env["affected_surfaces"] = {
                name: env.pop(_SURFACE_PREFIX + name, original)
                for name, original in surfaces.items()
            }
            env[SURFACE_COUNTS_KEY] = counts
        return _annotate_impact(env, dropped, dropped_edges)
    return annotate


def _annotate_impact(
    envelope: dict, dropped: dict[str, int], dropped_edges: int,
) -> dict:
    """Stamp ``warnings.size_capped`` + a human message onto an impact payload."""
    deps = {k: v for k, v in dropped.items() if not k.startswith(_SURFACE_PREFIX)}
    surf = {
        k[len(_SURFACE_PREFIX):]: v
        for k, v in dropped.items() if k.startswith(_SURFACE_PREFIX)
    }
    report: dict = {**deps, "edges": dropped_edges}
    if surf:
        report["affected_surfaces"] = surf
    warnings = dict(envelope.get("warnings") or {})
    warnings[SIZE_CAPPED_KEY] = report
    # A sibling of the report, never a member of it: ``size_capped`` is a map of
    # counts, and burying a boolean among them makes a consumer that totals its
    # values silently wrong by one. The existing parity test did exactly that.
    warnings[BUDGET_EXCEEDED_KEY] = False
    total = sum(deps.values()) + sum(surf.values())
    if total:
        warnings["messages"] = [
            *(warnings.get("messages") or []),
            READ_BUDGET_MESSAGE.format(
                dropped=total, noun="blast-radius entr(ies)",
            ),
        ]
    return {**envelope, "warnings": warnings}


def _mark_over_budget(envelope: dict) -> dict:
    """Flip ``warnings.budget_exceeded`` and say so in ``warnings.messages``.

    Applied *after* the fit search rather than inside it: the flag is a fact
    about the chosen answer, and the payload it lands on is already over budget,
    so the byte it adds cannot push a fitting envelope over.
    """
    warnings = dict(envelope.get("warnings") or {})
    warnings[BUDGET_EXCEEDED_KEY] = True
    warnings["messages"] = [*(warnings.get("messages") or []), OVER_BUDGET_MESSAGE]
    return {**envelope, "warnings": warnings}
