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

from weld._rel_path import canonical_rel_path, canonical_rel_paths


def edge_provenance_file(edge: dict) -> str:
    """Return an edge's producing-file provenance, or ``""`` if absent.

    ``python_callgraph`` sets ``props.provenance.file`` to the caller's
    ``rel_path`` for ``calls`` edges and the defining class's ``rel_path``
    for ``inherits`` edges; ``graph_closure._decorate_call_edges`` backfills
    it from the ``from`` (source) node's ``props.file``. So provenance.file
    is reliably the *originating* file -- never the endpoint -- and "is this
    provenance file stale" never misfires on a clean caller that merely
    targets a dirty file's symbol.

    Returned in the canonical form (:mod:`weld._rel_path`), because the
    strategy that stamped it and the index that builds the stale set spell a
    repo-relative path differently off POSIX. Identity on POSIX.
    """
    props = edge.get("props")
    if not isinstance(props, dict):
        return ""
    prov = props.get("provenance")
    if not isinstance(prov, dict):
        return ""
    return canonical_rel_path(prov.get("file"))


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

    *stale_files* arrives in the index spelling and the provenance in whichever
    spelling its strategy chose, so both sides are canonicalized for the
    membership test (:mod:`weld._rel_path`; identity on POSIX). Skipping that
    off POSIX puts every attributable edge on the ``not in stale_files``
    branch, retaining it unconditionally -- silent staleness rather than the
    over-purge the endpoint floor would have given (bd pbi8).
    """
    stale = canonical_rel_paths(stale_files)
    surviving: list[dict] = []
    for e in edges:
        prov_file = edge_provenance_file(e)
        if prov_file:
            if prov_file not in stale:
                surviving.append(e)
        elif e["from"] not in removed_ids and e["to"] not in removed_ids:
            surviving.append(e)
    return surviving


__all__ = ["edge_provenance_file", "purge_edges_by_provenance"]
