"""File-level graph<->state cross-check for incremental discovery.

ADR 0008 keys incremental re-runs on file content hashes. That is
not enough on its own: a graph rolled back (or committed) at an
older revision can lack nodes for files the current
``discovery-state.json`` already records at their on-disk SHAs. The
source-level audit in
:func:`weld.discovery_state.files_missing_strategy_outputs` only
catches the case where every file in a source is missing -- a
single freshly-added file slips through whenever any sibling in the
same source still has nodes.

This module hosts the per-file audit and the matching state-save
helper. Carved out of :mod:`weld.discovery_state` to keep that file
under the 400-line cap (CLAUDE.md "Line-Count Policy").
"""

from __future__ import annotations

from pathlib import Path

from weld.discovery_state import DiscoveryState, save_state


def graph_files_with_nodes(graph: dict) -> set[str]:
    """Return the set of repo-relative file paths referenced by graph nodes.

    Treats both ``props.file`` and ``props.declared_in`` as file
    anchors so the audit is consistent with
    :func:`weld.discovery_state.files_missing_strategy_outputs`.
    """
    out: set[str] = set()
    for node in graph.get("nodes", {}).values():
        props = node.get("props", {})
        f = props.get("file") or props.get("declared_in")
        if f:
            out.add(f)
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
    """
    if state is None:
        return set()
    candidates = current_files & set(state.files.keys())
    return candidates - files_with_nodes - state.files_with_no_nodes


def compute_files_with_no_nodes(
    current_files: set[str],
    files_with_nodes: set[str],
) -> set[str]:
    """Files in *current_files* with no anchoring node in the graph.

    Saved into :class:`DiscoveryState` so subsequent incremental runs
    can tell intentional empty output (e.g. an empty ``__init__.py``
    that the python_module strategy skips) from a graph<->state
    mismatch (the "stale graph + current state" case).
    """
    return current_files - files_with_nodes


def save_state_for_graph(
    root: Path,
    current_hashes: dict[str, str],
    graph: dict,
) -> None:
    """Persist :class:`DiscoveryState` aligned with the just-built graph.

    Captures both the file content hashes and the set of currently
    discovered files that produced no graph nodes, so the next
    incremental run's per-file audit can distinguish intentional
    empty output from a graph<->state mismatch.
    """
    no_nodes = compute_files_with_no_nodes(
        set(current_hashes.keys()), graph_files_with_nodes(graph),
    )
    save_state(
        root,
        DiscoveryState(files=dict(current_hashes), files_with_no_nodes=no_nodes),
    )
