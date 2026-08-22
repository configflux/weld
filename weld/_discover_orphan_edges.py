"""Detect a purge-surviving edge that a dirty re-parse never re-minted.

ADR 0074 purges a stale file's nodes but retains any edge whose clean
*provenance* file points at one -- trusting that file's own dirty re-parse to
re-mint the same-id endpoint. That trust holds when the endpoint's owning
file is genuinely re-parsed and re-emits the identical id. It does not hold
when the owning file was **deleted** (nothing ever re-parses it) or was
edited in a way that **stops emitting that id** (e.g. a renamed symbol): the
retained edge survives the purge with an endpoint that never comes back, and
the orchestrator's dangling-edge sweep
(:func:`weld._discover_postprocess._clean_and_dedup_edges`) quietly drops it
-- diverging from a full discover, which always re-parses the clean file too
and re-resolves its import against whatever exists today (bd znzu, ADR 0074
fourth amendment).

This module answers one narrow question, after a purge-and-merge pass has
run the originally-dirty files: which edges still dangle, and which clean
file produced them? The caller (:mod:`weld._discover_incremental_merge`)
widens the dirty set with the answer and re-runs the merge once -- giving
that file the same re-resolution chance a full discover already gives it,
rather than teaching python_callgraph (or any other strategy) to
special-case deletion or rename.
"""

from __future__ import annotations

from weld._incremental_purge import edge_provenance_file


def orphaned_producer_files(
    nodes: dict[str, dict],
    edges: list[dict],
) -> set[str]:
    """Provenance files of *edges* whose endpoint is missing from *nodes*.

    Call after a purge-and-merge pass (:func:`weld.discovery_state.purge_stale_nodes`
    plus the dirty-source re-run) with its resulting node/edge sets. An edge
    reaches *edges* with a missing endpoint only by the provenance branch of
    :func:`weld._incremental_purge.purge_edges_by_provenance` -- the
    conservative endpoint-membership floor already drops any unattributable
    edge touching a purged node before the merge even starts. So every result
    here names a clean file whose earlier parse produced an edge that is
    about to be swept as dangling, and that has not had a chance to
    reconsider it.

    Edges with no usable provenance file are skipped: there is no file to
    re-run for them, and they remain governed by the existing
    endpoint-membership purge floor (by construction they should not appear
    here at all, but a strategy could in principle hand back an edge that was
    never subject to the purge -- e.g. one added fresh by this same merge
    pass -- so the check stays defensive rather than assuming it).
    """
    orphaned: set[str] = set()
    for edge in edges:
        if edge.get("from") in nodes and edge.get("to") in nodes:
            continue
        prov_file = edge_provenance_file(edge)
        if prov_file:
            orphaned.add(prov_file)
    return orphaned


__all__ = ["orphaned_producer_files"]
