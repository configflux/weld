"""Which files (and sources) a graph anchors, and which it was supposed to.

Pure predicates over a graph body and a :class:`DiscoveryState`, with no I/O
and no notion of *which* graph is on disk. That last part is what makes them
a separate module from :mod:`weld._discover_state_check`, which owns the
graph<->state binding (tokens, vouching, the state writes) and consumes these:
the dependency runs one way, and this half stays directly unit-testable
without a filesystem. Carved out of that module, and later grown by moving
:func:`files_missing_strategy_outputs` in from :mod:`weld.discovery_state`,
both to keep the respective modules under the 400-line cap (CLAUDE.md
"Line-Count Policy").

The distinction most of these encode is intent versus failure. A file can end
a run with no anchoring node because a strategy looked at it and declined, or
because no strategy spoke for it at all; only the first is a standing
exemption. See :mod:`weld.strategies._strategy_failure` for the channel that
reports the second (bd hch4) -- and its entry-keyed sibling, for a source
entry with no file to report at all (bd um00).
"""

from __future__ import annotations

from weld._rel_path import canonical_rel_path, canonical_rel_paths
from weld.discovery_state import DiscoveryState


def graph_files_with_nodes(graph: dict) -> set[str]:
    """Return the set of repo-relative file paths referenced by graph nodes.

    Treats both ``props.file`` and ``props.declared_in`` as file
    anchors so the audit is consistent with
    :func:`weld.discovery_state.files_missing_strategy_outputs`.

    Returned in the canonical form (:mod:`weld._rel_path`; identity on POSIX):
    these are graph-vocabulary paths, spelled by whichever strategy minted the
    node, and every consumer below weighs them against index-vocabulary paths
    weld built itself (bd pbi8).
    """
    out: set[str] = set()
    for node in graph.get("nodes", {}).values():
        props = node.get("props", {})
        anchor = canonical_rel_path(props.get("file") or props.get("declared_in"))
        if anchor:
            out.add(anchor)
    return out


def files_missing_from_graph(
    state: DiscoveryState | None,
    current_files: set[str],
    files_with_nodes: set[str],
) -> set[str]:
    """Files that the prior run *should* have anchored in graph but did not.

    Returns the set of repo-relative paths that are simultaneously:

    * present in the current discovered file set (so the strategy is
      still configured for them),
    * recorded in the previous ``DiscoveryState.files`` (so the SHA
      tracker thought the file was up to date and would not mark it
      dirty by content),
    * not represented by any node in the existing graph
      (``files_with_nodes``), and
    * not recorded in ``state.files_with_no_nodes`` (which captures
      legitimate empty-output files like blank ``__init__.py``).

    This is the file-level cross-check that closes the
    "stale-graph-plus-current-state" gap. The source-level audit in
    ``files_missing_strategy_outputs`` is satisfied as soon as any
    sibling file in the source has nodes, so a single freshly-added
    file with no graph node slips through when the graph predates
    it. Returning the file here forces the incremental orchestrator
    to mark it dirty and re-run the strategy.

    ``state.files_with_failed_strategy`` is deliberately *not* subtracted (bd
    hch4). A file no strategy spoke for is exactly a file whose strategy must
    be given another chance: keeping it here is what makes the repair survive
    the run that failed it, and it is what stops the no-change fast path from
    running while a known hole is open. It costs one re-run of that file's
    source per pass for as long as the failure lasts, which is the honest
    price of a graph that is genuinely missing it.

    Only the ``files_with_nodes`` subtraction crosses the index<->graph
    vocabulary line, so only it is canonicalized (:mod:`weld._rel_path`;
    identity on POSIX). The returned paths keep their index spelling: they
    become part of the dirty set, and the strategies match that against their
    own OS-native rel paths (bd pbi8).
    """
    if state is None:
        return set()
    candidates = current_files & set(state.files.keys())
    anchored = canonical_rel_paths(files_with_nodes)
    unanchored = {f for f in candidates if canonical_rel_path(f) not in anchored}
    return unanchored - state.files_with_no_nodes


def compute_files_with_no_nodes(
    current_files: set[str],
    files_with_nodes: set[str],
    failed_files: set[str] | None = None,
) -> set[str]:
    """Files in *current_files* a strategy looked at and left without a node.

    Saved into :class:`DiscoveryState` so subsequent incremental runs
    can tell intentional empty output (e.g. an empty ``__init__.py``
    that the python_module strategy skips) from a graph<->state
    mismatch (the "stale graph + current state" case).

    *failed_files* are the paths this run could not speak for at all
    (:func:`weld.strategies._strategy_failure.drain_strategy_failures`). They
    are withheld rather than recorded, because the recorded set is read as a
    claim about what the strategy *decided*, and a strategy that never ran --
    refused by ``--safe``, unloadable, an ``external_json`` command that
    failed -- decided nothing. Recording them was the bd hch4 defect: the run
    that failed a file wrote the exemption that stopped any later run from
    retrying it, and since the cause lay outside the file, no content change
    ever re-dirtied it.

    ``files_with_nodes`` is graph-vocabulary and the other two are index
    vocabulary, so that subtraction goes through the canonical form
    (:mod:`weld._rel_path`; identity on POSIX). Off POSIX, skipping it records
    an anchored file as producing no nodes -- a permanent exemption from the
    per-file repair, which is silent staleness rather than a slow path
    (bd pbi8). Output keeps the index spelling it is stored and matched in.
    """
    anchored = canonical_rel_paths(files_with_nodes)
    failed = failed_files or set()
    return {
        f for f in current_files
        if f not in failed and canonical_rel_path(f) not in anchored
    }


def files_missing_strategy_outputs(
    existing_graph: dict,
    source_file_map: list[list[str]],
    exempt_files: set[str] | None = None,
) -> set[str]:
    """Audit *existing_graph* for sources whose files have zero nodes.

    Returns the set of repo-relative file paths for which the strategy
    must re-run to repair a graph that was written without the nodes the
    source was supposed to produce. Each source entry in
    ``source_file_map`` contributes either *all* of its files (when no
    node in the graph has ``props.file`` inside that set) or none.

    Background: the incremental discovery path (ADR 0008) keys re-runs
    on file content hashes. If the previous run committed
    ``discovery-state.json`` with the current files but committed a
    ``graph.json`` that lacks those files' nodes -- e.g. because of a
    crash, partial write, or a sequence where the state-write path ran
    but the symbol-emitting strategy did not -- the dirty set is empty
    and the bug perpetuates: every subsequent incremental run skips the
    strategy and the symbols never reappear short of deleting state.

    The audit closes that gap. Treating "no nodes for any of a source's
    files" as a re-run trigger is conservative: it never produces a
    false positive when the strategy genuinely emits at least one node
    for at least one file in the set, and it costs at most one pass
    over the graph's nodes.

    A source entry that resolves *no* files at all (a command-only
    ``external_json`` adapter, or any other strategy configured with no
    ``glob``/``path``/``files`` key) contributes nothing here either way --
    there is no file to name. :func:`weld._discover_basis.sources_needing_retry`
    is the entry-keyed counterpart for that class of source (bd um00).

    *exempt_files* (bd 85tb.2) is the prior run's ``files_with_no_nodes``
    -- files the last run already proved produce no ``props.file`` /
    ``props.declared_in``-anchored node (e.g. an issue-store source whose
    strategy emits abstract ``concept`` nodes that anchor to no file).
    Without this exemption such a source is flagged on *every* incremental
    run, which on a repo that has one perpetually drops every refresh onto
    the slow with-changes path instead of the no-change fast path. A file
    in *exempt_files* whose content actually changed is still picked up by
    the content-hash ``dirty`` set, so exempting it here cannot mask a real
    edit -- it only stops the perpetual no-op re-run.
    """
    exempt = exempt_files or set()
    nodes = existing_graph.get("nodes", {})
    # Different strategies record the source file under different prop
    # keys (``file`` for most, ``declared_in`` for the events family).
    # Treat a file as "has nodes" if any node references it under
    # either key so the audit does not force a perpetual re-run for
    # those strategies.
    #
    # Anchors are compared in the canonical form (:mod:`weld._rel_path`;
    # identity on POSIX) because the strategy spelled them and the source map
    # did not. The *returned* paths stay in the source map's own spelling --
    # they become part of the dirty set, which the strategies re-match against
    # their own OS-native rel paths (bd pbi8).
    files_with_nodes: set[str] = set()
    for node in nodes.values():
        props = node.get("props", {})
        anchor = canonical_rel_path(props.get("file") or props.get("declared_in"))
        if anchor:
            files_with_nodes.add(anchor)

    missing: set[str] = set()
    for files in source_file_map:
        if not files:
            continue
        file_set = set(files)
        if not any(canonical_rel_path(f) in files_with_nodes for f in files):
            # A source whose every file is already known to legitimately
            # produce no file-anchored node must not perpetually re-trigger.
            if file_set <= exempt:
                continue
            missing |= file_set
    return missing


def files_with_no_nodes_and_failed(
    current_files: set[str],
    graph: dict,
    strategy_failed: set[str] | None,
) -> tuple[set[str], set[str]]:
    """Split *current_files* into legitimate no-node files and real failures.

    Factored out of :func:`weld._discover_state_check.save_state_for_graph`
    to keep that module under the 400-line cap (CLAUDE.md "Line-Count
    Policy"). *strategy_failed* is bounded to *current_files* and to files
    the graph does not anchor -- an incremental pass reports a whole source
    when its strategy will not load, and a clean sibling whose nodes
    survived the purge is not a hole.

    Returns ``(no_nodes, failed)``. The caller still folds in files that
    vanished between the inventory walk and the strategy's own listing
    (:func:`weld._discover_vanished.vanished_since_inventory`, bd rt65),
    which needs a filesystem check this module deliberately does not make.

    The ``anchored`` subtraction crosses the index<->graph vocabulary line,
    so it goes through the canonical form (:mod:`weld._rel_path`; identity on
    POSIX) -- off POSIX it otherwise reads every strategy-spelled anchor as
    absent and records a clean file as failed, which is the one thing that
    keeps the no-change fast path from ever running (bd pbi8).
    """
    anchored = graph_files_with_nodes(graph)
    failed = {
        f for f in (strategy_failed or set())
        if f in current_files and canonical_rel_path(f) not in anchored
    }
    no_nodes = compute_files_with_no_nodes(current_files, anchored, failed)
    return no_nodes, failed


__all__ = [
    "compute_files_with_no_nodes",
    "files_missing_from_graph",
    "files_missing_strategy_outputs",
    "files_with_no_nodes_and_failed",
    "graph_files_with_nodes",
]
