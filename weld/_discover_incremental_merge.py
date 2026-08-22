"""Purge + re-run dirty sources, widening once for orphaned surviving edges.

ADR 0074 (fourth amendment, bd znzu): a clean file's edge can survive a
stale-node purge by provenance and still end up dangling, if the node it
points at was deleted outright or was edited away without being re-minted
under the same id. :func:`weld._discover_orphan_edges.orphaned_producer_files`
finds that population after a normal purge-and-run pass; this module is the
loop that acts on it -- widen ``dirty``/``stale`` with the answer and redo the
merge once more.

One retry always suffices; this is not a heuristic bound. A file only enters
the widened set because its *own* content is unchanged (a genuinely dirty
file was already in the first pass's ``dirty`` and already got its chance).
Re-parsing unchanged content reproduces byte-identical own-symbol nodes (a
file's own definitions are a pure function of its own AST, never of
``project_modules`` or any other cross-file state), so a widened file can
supply a missing endpoint but can never itself remove one -- it cannot be the
source of a *new* dangling edge. The population
:func:`~weld._discover_orphan_edges.orphaned_producer_files` finds on the
second pass is therefore always empty, and a fixed-point loop would only
spend a pass proving that.
"""

from __future__ import annotations

from pathlib import Path

from weld._discover_basis import entry_fingerprint
from weld._discover_node_merge import incremental_claim_wins
from weld._discover_orphan_edges import orphaned_producer_files
from weld._discover_strategies import IncrementalHint, run_source as _run_source
from weld.discovery_state import purge_stale_nodes


def run_incremental_merge(
    root: Path,
    sources: list[dict],
    source_file_map: list[list[str]],
    existing_graph: dict,
    dirty: set[str],
    stale: set[str],
    *,
    safe: bool,
    retry_entry_ids: frozenset[str] = frozenset(),
) -> tuple[dict[str, dict], list[dict], dict, set[str], list[str]]:
    """Purge stale nodes/edges, re-run dirty sources, and widen once.

    Returns ``(ex_nodes, ex_edges, context, dirty, ran_discovered_from)`` --
    the merged node/edge sets, the last pass's strategy ``context`` (for the
    caller's warning and failure drain), the dirty set actually used
    (possibly widened past the caller's input), and the ``discovered_from``
    every re-run source actually reported this pass (bd 8084) -- collected,
    not re-derived from *source_file_map*, so a footprint-less source (bd
    um00) or a directory-anchored provenance entry (ADR 0017's amendment)
    survives an incremental pass the same way a full run's
    ``df.extend(r.discovered_from)`` already does.

    *retry_entry_ids* (bd um00) names source entries that must run this pass
    regardless of file-level dirtiness -- the entry-keyed counterpart to
    *dirty* for entries no file-hash delta can ever mark, because they
    resolve no files at all (a command-only ``external_json`` adapter). See
    :func:`weld._discover_basis.sources_needing_retry`.
    """
    ex_nodes, ex_edges, context, ran_df = _merge_once(
        root, sources, source_file_map, existing_graph, dirty, stale,
        safe=safe, retry_entry_ids=retry_entry_ids,
    )

    orphaned = orphaned_producer_files(ex_nodes, ex_edges)
    if orphaned:
        dirty = dirty | orphaned
        stale = stale | orphaned
        # Redo from the ORIGINAL prior graph, not the first pass's partial
        # result -- one purge decision per file, never a layered one. The
        # widened pass's own ``ran_df`` supersedes the first pass's: it
        # re-derives every source against the union'd *dirty*, so it already
        # names every source either pass touched.
        ex_nodes, ex_edges, context, ran_df = _merge_once(
            root, sources, source_file_map, existing_graph, dirty, stale,
            safe=safe, retry_entry_ids=retry_entry_ids,
        )

    return ex_nodes, ex_edges, context, dirty, ran_df


def _merge_once(
    root: Path,
    sources: list[dict],
    source_file_map: list[list[str]],
    existing_graph: dict,
    dirty: set[str],
    stale: set[str],
    *,
    safe: bool,
    retry_entry_ids: frozenset[str] = frozenset(),
) -> tuple[dict[str, dict], list[dict], dict, list[str]]:
    """One purge-then-run pass for the given *dirty*/*stale* sets."""
    # Purge stale nodes; edges purge by ADR 0074 provenance (clean-caller
    # inbound edges into dirty-file symbols survive, dirty re-parse re-mints
    # the endpoint) so parse-only-dirty stays byte-identical to a full run.
    ex_nodes, ex_edges = purge_stale_nodes(
        dict(existing_graph.get("nodes", {})),
        list(existing_graph.get("edges", [])),
        stale,
    )

    # ADR 0074: hand python_callgraph (and python_module, ADR 0084) the dirty
    # scope + POST-PURGE prior node set (snapshot before this pass mutates
    # ``ex_nodes``) so they parse only dirty files and python_callgraph
    # reconstructs cross-glob ``project_modules`` from surviving prior
    # symbols instead of re-globbing every sibling.
    incremental_hint = IncrementalHint(
        dirty_files=frozenset(dirty), prior_nodes=dict(ex_nodes),
    )

    context: dict = {}
    ran_df: list[str] = []
    for i, source in enumerate(sources):
        forced = retry_entry_ids and entry_fingerprint(source) in retry_entry_ids
        if not set(source_file_map[i]).intersection(dirty) and not forced:
            continue
        r = _run_source(
            root, source, context, safe=safe, incremental_hint=incremental_hint,
            source_files=source_file_map[i],
        )
        for nid, node in r.nodes.items():
            # Dirty-scope guard + ADR 0103 veto, in one predicate: a re-run
            # source may not overwrite a clean file's node, but it may mint
            # one the graph does not hold (bd n0p2).
            if incremental_claim_wins(ex_nodes.get(nid), node, dirty):
                ex_nodes[nid] = node
        ex_edges.extend(r.edges)
        # bd 8084: collect what the source actually reported -- never
        # re-derive it from ``source_file_map``, which cannot represent a
        # footprint-less entry's paths or a directory-provenance marker.
        ran_df.extend(r.discovered_from)

    return ex_nodes, ex_edges, context, ran_df


__all__ = ["run_incremental_merge"]
