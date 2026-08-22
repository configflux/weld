"""The index<->graph path-form contract, pinned across a separator split.

Incremental discovery compares two path *vocabularies* that no single
component owns. The **index** vocabulary is built by weld from the filesystem
(``_source_resolve`` -> ``DiscoveryState.files`` -> the dirty/stale sets) and
is OS-native. The **graph** vocabulary is built by ~40 strategies into
``props.file`` / ``props.declared_in`` / ``props.provenance.file``, and is
mixed by construction: some strategies write ``as_posix()``, some ``str()``.

On POSIX the two spellings are byte-identical, so every comparison between
them works and the divergence is invisible. Off POSIX they diverge and the
ADR 0074 purge misfires in both tiers -- a stale node survives its own file's
edit, and a provenance-carrying edge takes the "attributable" branch, misses
the stale set, and is retained *unconditionally* even when its producing file
is stale. That second one is strictly worse than the unattributed endpoint
floor it replaced: silent staleness (bd pbi8).

HONEST LIMITATION -- READ BEFORE TRUSTING THESE TESTS. Weld has no Windows
lane (no ``windows-latest`` in ``.github/workflows/``, no OS classifiers), so
none of this runs on the platform where the defect actually bites. The
non-POSIX platform is *simulated*: ``_simulated_non_posix`` patches
``weld._rel_path._FOREIGN_SEPARATORS`` to ``("\\\\",)``, the value that module
would compute on Windows. What these tests therefore prove is the *form
contract* -- that a comparison spanning the two vocabularies is total whenever
the separators differ -- and not that weld runs on Windows. A real Windows
runner would still be needed for that, and would exercise far more than this.

The last class runs with **no** simulation and pins the property that keeps
this from being the unconditional ``str.replace("\\\\", "/")`` used by the
read-side query and lint paths: on POSIX a file legitimately named ``a\\b.py``
is a different file from ``a/b.py``, and conflating them in a purge would let
one file's edit silently drop the other's nodes.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from weld import _rel_path
from weld._discover_state_check import save_state_for_graph
from weld._graph_anchors import (
    compute_files_with_no_nodes,
    files_missing_from_graph,
    files_missing_strategy_outputs,
    graph_files_with_nodes,
)
from weld._incremental_purge import edge_provenance_file, purge_edges_by_provenance
from weld._rel_path import canonical_rel_path, canonical_rel_paths
from weld.discovery_state import DiscoveryState, purge_stale_nodes

# The two spellings of one repo-relative path. ``NATIVE`` is what
# ``str(p.relative_to(root))`` yields off POSIX (the index vocabulary and the
# ``str()`` half of the strategies); ``POSIX`` is what ``as_posix()`` yields
# (the other half, and every node id).
NATIVE = "lib\\thing.py"
POSIX = "lib/thing.py"


def _simulated_non_posix():
    """Patch the canonical form onto the separators Windows would compute.

    Not a mock of the code under test: ``_FOREIGN_SEPARATORS`` is derived from
    ``os.sep``/``os.altsep`` at import, and this substitutes the value that
    derivation produces on Windows. Everything downstream is the real code.
    """
    return mock.patch.object(_rel_path, "_FOREIGN_SEPARATORS", ("\\",))


def _node(file_prop: str) -> dict:
    return {"type": "symbol", "label": "thing", "props": {"file": file_prop}}


def _edge(prov_file: str | None) -> dict:
    props: dict = {"source_strategy": "python_callgraph"}
    if prov_file is not None:
        props["provenance"] = {"file": prov_file, "line": 7}
    return {"from": "symbol:a", "to": "symbol:b", "type": "calls", "props": props}


class CanonicalFormContractTest(unittest.TestCase):
    """The form itself: identity on POSIX, total off it."""

    def test_posix_native_input_is_returned_unchanged(self):
        for value in (POSIX, NATIVE, "a/b/c.py", ""):
            self.assertEqual(canonical_rel_path(value), value)

    def test_foreign_separator_folds_to_posix(self):
        with _simulated_non_posix():
            self.assertEqual(canonical_rel_path(NATIVE), POSIX)
            self.assertEqual(canonical_rel_path(POSIX), POSIX)

    def test_fold_is_idempotent(self):
        with _simulated_non_posix():
            once = canonical_rel_path(NATIVE)
            self.assertEqual(canonical_rel_path(once), once)

    def test_non_string_anchors_nothing(self):
        for value in (None, 3, ["lib/thing.py"], {"file": POSIX}):
            self.assertEqual(canonical_rel_path(value), "")

    def test_both_spellings_collapse_to_one_lookup_entry(self):
        with _simulated_non_posix():
            self.assertEqual(canonical_rel_paths({NATIVE, POSIX}), {POSIX})

    def test_lookup_set_is_unchanged_on_posix(self):
        self.assertEqual(canonical_rel_paths({NATIVE, POSIX}), {NATIVE, POSIX})


class PurgeStaleNodesFormSplitTest(unittest.TestCase):
    """ADR 0074 tier 1: a dirty file's node must not outlive its own edit."""

    def test_posix_spelled_node_is_purged_by_native_stale_entry(self):
        nodes = {"symbol:thing": _node(POSIX)}
        with _simulated_non_posix():
            surviving, _ = purge_stale_nodes(dict(nodes), [], {NATIVE})
        self.assertEqual(surviving, {}, "stale node survived its own file's edit")

    def test_native_spelled_node_is_purged_by_posix_stale_entry(self):
        nodes = {"symbol:thing": _node(NATIVE)}
        with _simulated_non_posix():
            surviving, _ = purge_stale_nodes(dict(nodes), [], {POSIX})
        self.assertEqual(surviving, {})

    def test_clean_file_node_survives_the_same_simulation(self):
        """Negative control: the fold must not widen the purge."""
        nodes = {"symbol:other": _node("lib/other.py")}
        with _simulated_non_posix():
            surviving, _ = purge_stale_nodes(dict(nodes), [], {NATIVE})
        self.assertEqual(set(surviving), {"symbol:other"})

    def test_node_without_a_file_prop_survives(self):
        nodes = {"package:weld": {"type": "package", "label": "weld", "props": {}}}
        with _simulated_non_posix():
            surviving, _ = purge_stale_nodes(dict(nodes), [], {NATIVE})
        self.assertEqual(set(surviving), {"package:weld"})


class PurgeEdgesByProvenanceFormSplitTest(unittest.TestCase):
    """ADR 0074 tier 2: the branch that retains unconditionally on a miss."""

    def test_stale_provenance_edge_is_dropped_across_spellings(self):
        edges = [_edge(POSIX)]
        with _simulated_non_posix():
            surviving = purge_edges_by_provenance(edges, {NATIVE}, set())
        self.assertEqual(
            surviving, [],
            "edge retained though its producing file is stale -- silent staleness",
        )

    def test_clean_provenance_edge_survives(self):
        edges = [_edge("lib/other.py")]
        with _simulated_non_posix():
            surviving = purge_edges_by_provenance(edges, {NATIVE}, set())
        self.assertEqual(surviving, edges)

    def test_clean_caller_edge_into_purged_endpoint_still_survives(self):
        """The amendment's whole point, preserved under the fold."""
        edges = [_edge("lib/clean_caller.py")]
        with _simulated_non_posix():
            surviving = purge_edges_by_provenance(edges, {NATIVE}, {"symbol:b"})
        self.assertEqual(surviving, edges)

    def test_unattributed_edge_keeps_the_endpoint_membership_floor(self):
        edges = [_edge(None)]
        with _simulated_non_posix():
            self.assertEqual(purge_edges_by_provenance(edges, {NATIVE}, set()), edges)
            self.assertEqual(purge_edges_by_provenance(edges, {NATIVE}, {"symbol:b"}), [])

    def test_provenance_reader_reports_the_canonical_form(self):
        with _simulated_non_posix():
            self.assertEqual(edge_provenance_file(_edge(NATIVE)), POSIX)
        self.assertEqual(edge_provenance_file(_edge(None)), "")

    def test_both_tiers_judge_staleness_by_one_yardstick(self):
        """purge_stale_nodes must hand the edge purge the same stale set."""
        nodes = {"symbol:thing": _node(POSIX)}
        edges = [_edge(POSIX)]
        with _simulated_non_posix():
            surviving_nodes, surviving_edges = purge_stale_nodes(
                dict(nodes), list(edges), {NATIVE},
            )
        self.assertEqual(surviving_nodes, {})
        self.assertEqual(surviving_edges, [])


class AnchorAuditFormSplitTest(unittest.TestCase):
    """The dirty-scoping audits that read graph anchors against the index."""

    def test_graph_anchors_are_reported_in_the_canonical_form(self):
        graph = {"nodes": {
            "a": _node(NATIVE),
            "b": {"type": "event", "props": {"declared_in": POSIX}},
            "c": {"type": "package", "props": {}},
            "d": {"type": "symbol", "props": {"file": 17}},
        }}
        with _simulated_non_posix():
            self.assertEqual(graph_files_with_nodes(graph), {POSIX})

    def test_anchored_source_is_not_flagged_for_a_perpetual_rerun(self):
        graph = {"nodes": {"symbol:thing": _node(POSIX)}}
        with _simulated_non_posix():
            missing = files_missing_strategy_outputs(graph, [[NATIVE]])
        self.assertEqual(missing, set())

    def test_genuinely_unanchored_source_is_returned_in_index_spelling(self):
        graph = {"nodes": {"symbol:thing": _node("lib/elsewhere.py")}}
        with _simulated_non_posix():
            missing = files_missing_strategy_outputs(graph, [[NATIVE]])
        self.assertEqual(
            missing, {NATIVE},
            "dirty paths must keep the index spelling the strategies re-match",
        )

    def test_anchored_file_is_not_queued_for_per_file_repair(self):
        state = DiscoveryState(files={NATIVE: "sha"})
        with _simulated_non_posix():
            missing = files_missing_from_graph(state, {NATIVE}, {POSIX})
        self.assertEqual(missing, set())

    def test_unanchored_file_is_queued_in_index_spelling(self):
        state = DiscoveryState(files={NATIVE: "sha"})
        with _simulated_non_posix():
            missing = files_missing_from_graph(state, {NATIVE}, {"lib/elsewhere.py"})
        self.assertEqual(missing, {NATIVE})

    def test_anchored_file_is_never_exempted_as_producing_no_nodes(self):
        """A wrong exemption here is permanent: nothing ever re-dirties it."""
        with _simulated_non_posix():
            self.assertEqual(compute_files_with_no_nodes({NATIVE}, {POSIX}), set())

    def test_declined_file_is_still_recorded_in_index_spelling(self):
        with _simulated_non_posix():
            recorded = compute_files_with_no_nodes({NATIVE}, {"lib/elsewhere.py"})
        self.assertEqual(recorded, {NATIVE})

    def test_failed_set_withholds_a_file_the_graph_anchors(self):
        with _simulated_non_posix():
            recorded = compute_files_with_no_nodes({NATIVE}, {POSIX}, {NATIVE})
        self.assertEqual(recorded, set())


class SaveStateAnchorFormTest(unittest.TestCase):
    """The recorded inventory, whose two sets gate the no-change fast path."""

    def _record(self, strategy_failed: set[str]) -> DiscoveryState:
        captured: list[DiscoveryState] = []
        graph = {"nodes": {"symbol:thing": _node(POSIX)}}
        with _simulated_non_posix(), mock.patch(
            "weld._discover_state_check.save_state",
            side_effect=lambda root, state: captured.append(state),
        ):
            save_state_for_graph(
                Path("/nonexistent"), {NATIVE: "sha"}, graph,
                graph_published=False, strategy_failed=strategy_failed,
            )
        return captured[0]

    def test_anchored_file_is_not_recorded_as_failed(self):
        self.assertEqual(self._record({NATIVE}).files_with_failed_strategy, set())

    def test_anchored_file_is_not_recorded_as_producing_no_nodes(self):
        self.assertEqual(self._record(set()).files_with_no_nodes, set())


class PosixLiteralBackslashIsNotFoldedTest(unittest.TestCase):
    """No simulation: the property an unconditional replace would break.

    ``a\\b.py`` is a legal POSIX filename and a *different* file from
    ``a/b.py``. The read-side query and lint paths fold it anyway and document
    the trade -- a misread search hit is cheap. A purge cannot take that trade:
    the conflated node is dropped and never re-minted, because the file that
    would re-mint it is not the one that changed.
    """

    def test_literal_backslash_file_is_not_purged_by_its_slash_twin(self):
        nodes = {"symbol:odd": _node("a\\b.py")}
        surviving, _ = purge_stale_nodes(dict(nodes), [], {"a/b.py"})
        self.assertEqual(set(surviving), {"symbol:odd"})

    def test_literal_backslash_edge_is_not_purged_by_its_slash_twin(self):
        edges = [_edge("a\\b.py")]
        self.assertEqual(purge_edges_by_provenance(edges, {"a/b.py"}, set()), edges)

    def test_literal_backslash_file_is_still_purged_by_itself(self):
        nodes = {"symbol:odd": _node("a\\b.py")}
        surviving, _ = purge_stale_nodes(dict(nodes), [], {"a\\b.py"})
        self.assertEqual(surviving, {})


if __name__ == "__main__":
    unittest.main()
