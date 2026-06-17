"""Provenance-aware edge purge for incremental discovery (ADR 0074).

The incremental orchestrator purges the prior graph's stale-file *nodes*
before re-running strategies for dirty files. Purging the prior *edges* by
endpoint-node membership (drop any edge touching a purged node) over-purges:
a ``calls`` edge that **originates in a clean sibling** but **targets a
symbol defined in a dirty (re-extracted) file** is collateral-dropped, and
because parse-only-dirty re-extraction never re-parses the clean caller, the
edge is permanently lost -- breaking the incremental == full byte-identity
invariant (the cjij.2 defect, ADR 0074 amendment).

This module purges edges by **provenance** instead: an edge that records the
file its extraction produced it from (``props.provenance.file``) is purged
iff *that* file is stale, not because an endpoint was purged. Provenance is
the *originating* file by construction, so a clean caller's inbound edge into
a dirty file's symbol survives; the dirty re-parse re-mints the same-id
target node, restoring the endpoint and its ``definite`` closure. Edges
without a usable provenance file keep the conservative endpoint-membership
purge (the safety floor: strictly additive -- never retains a genuinely-stale
edge it cannot attribute).
"""

from __future__ import annotations


def edge_provenance_file(edge: dict) -> str:
    """Return an edge's producing-file provenance, or ``""`` if absent.

    ``python_callgraph`` sets ``props.provenance.file`` to the caller's
    ``rel_path`` for ``calls`` edges and the defining class's ``rel_path``
    for ``inherits`` edges; ``graph_closure._decorate_call_edges`` backfills
    it from the ``from`` (source) node's ``props.file``. So provenance.file
    is reliably the *originating* file -- never the endpoint -- and "is this
    provenance file stale" never misfires on a clean caller that merely
    targets a dirty file's symbol.
    """
    props = edge.get("props")
    if not isinstance(props, dict):
        return ""
    prov = props.get("provenance")
    if not isinstance(prov, dict):
        return ""
    f = prov.get("file")
    return f if isinstance(f, str) and f else ""


def purge_edges_by_provenance(
    edges: list[dict],
    stale_files: set[str],
    removed_ids: set[str],
) -> list[dict]:
    """Return the surviving edges under ADR 0074's two-tier rule.

    * Provenance-attributable edge: survives iff its producing file is not
      stale (independent of endpoint membership).
    * Unattributable edge: keeps the conservative endpoint-membership purge
      (drop if either endpoint was purged).

    An edge retained by provenance whose endpoint is not re-minted by the
    dirty parse (target genuinely deleted) does not dangle: the orchestrator
    post-process (:func:`weld._discover_postprocess._clean_and_dedup_edges`)
    drops every edge whose endpoint is absent from the final node set.
    """
    surviving: list[dict] = []
    for e in edges:
        prov_file = edge_provenance_file(e)
        if prov_file:
            if prov_file not in stale_files:
                surviving.append(e)
        elif e["from"] not in removed_ids and e["to"] not in removed_ids:
            surviving.append(e)
    return surviving


__all__ = ["edge_provenance_file", "purge_edges_by_provenance"]
