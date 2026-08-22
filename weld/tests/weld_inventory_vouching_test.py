"""An inventory may only speak for the graph it actually published (ADR 0101).

The other half of "covered". ``weld_coverage_staleness_test`` asks whether the
inventory covers every in-scope file; these ask the prior question -- whether
that inventory has any standing to describe ``.weld/graph.json`` at all.

Two ways it loses that standing, amended into ADR 0101 in turn:

* bd esww / hfm6: the run that wrote the inventory never published a graph.
  ``finalize_single_repo`` persists state on every path but writes the graph
  only when asked, so the ``--output`` elsewhere shape, the library caller,
  and any interruption between the two writes all leave an inventory covering
  files no reader can see.
* bd wq9i: the run published correctly and the *body* was replaced
  afterwards. The inventory still matches the tree, so every cheaper signal
  reads clean; only comparing the published graph's recorded identity against
  the file now on disk catches it.

Both resolve the same way -- report the doubt, buy one refresh, converge --
because the alternative is a confident wrong answer that persists for as long
as the tree stays clean.
"""

from __future__ import annotations

import json
import unittest

from weld._discover_state_check import state_vouches_for_graph
from weld._staleness_coverage import (
    coverage_stale,
    coverage_stale_detail,
    files_missing_from_inventory,
)
from weld.tests._coverage_stale_lib import CoverageFixture, commit_all


class UnpublishedInventoryTest(CoverageFixture):
    """An inventory may only vouch for a graph its own run published.

    Rationale in :func:`weld._staleness_coverage.inventory_vouches_for_graph`;
    these pin both directions of it plus the degraded inputs.
    """

    def _write_graph(self) -> None:
        empty = json.dumps({"meta": {}, "nodes": {}, "edges": []})
        (self.root / ".weld" / "graph.json").write_text(empty, encoding="utf-8")

    def test_unpublished_inventory_is_stale(self) -> None:
        # Scope is *fully* covered; the defect is that the graph this
        # inventory describes was never written where readers look.
        self._write_graph()
        self.save_state(graph_published=False)
        self.assertEqual(files_missing_from_inventory(self.root), set())
        self.assertTrue(coverage_stale(self.root))
        # The doubt has no single uncovered file to blame, so the detail
        # enumeration under-reports rather than inventing one.
        self.assertEqual(coverage_stale_detail(self.root), [])

    def test_published_inventory_is_not_stale(self) -> None:
        # The other direction of ADR 0101 section 4: over-reporting here would
        # refresh on every single read forever.
        self._write_graph()
        self.save_state(graph_published=True)
        self.assertFalse(coverage_stale(self.root))

    def test_unpublished_inventory_without_a_graph_is_silent(self) -> None:
        # Degraded input (ADR 0101 section 5): with no graph to be stale
        # against, the missing-graph guard owns the first run.
        self.save_state(graph_published=False)
        self.assertFalse(coverage_stale(self.root))

    def test_legacy_state_without_the_field_is_stale_once(self) -> None:
        # A state written before this field existed cannot name the graph it
        # published, so it buys exactly one refresh -- which re-stamps it.
        # ``graph_published`` is deliberately left behind: an older weld's
        # boolean is a compatibility mirror, and a reader that consulted it
        # would inherit exactly the claim this amendment stopped trusting.
        self._write_graph()
        self.save_state(graph_published=True)
        path = self.root / ".weld" / "discovery-state.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw.pop("published_graph", None)
        self.assertTrue(raw["graph_published"], "fixture: the mirror stays")
        path.write_text(json.dumps(raw), encoding="utf-8")
        self.assertTrue(coverage_stale(self.root))

    def test_body_replaced_under_a_vouching_inventory_is_stale(self) -> None:
        # bd wq9i: the inventory published *a* graph, and something replaced
        # the body underneath it -- ADR 0096 gate 5 lands a sibling's graph
        # beside a state file it deliberately keeps. Scope stays fully
        # covered and the hashes still describe this tree, so every other
        # signal reads clean; only the identity check sees it.
        self._write_graph()
        self.save_state(graph_published=True)
        self.assertFalse(coverage_stale(self.root), "precondition: healthy")

        (self.root / ".weld" / "graph.json").write_text(
            json.dumps({"meta": {}, "nodes": {"file:foreign": {}}, "edges": []}),
            encoding="utf-8",
        )

        self.assertEqual(files_missing_from_inventory(self.root), set())
        self.assertTrue(
            coverage_stale(self.root),
            "an inventory that never described this body must not vouch for it",
        )
        self.assertEqual(coverage_stale_detail(self.root), [])


class DiscoveryPublishesItsInventoryTest(CoverageFixture):
    """The real pipeline, not a hand-made state (bd esww / hfm6).

    The field failure end to end: a discovery run whose graph never reaches
    ``.weld/graph.json`` leaves a state recording a file that has zero nodes
    anywhere a reader will look.
    """

    def _state(self):
        from weld.discovery_state import load_state
        state = load_state(self.root)
        assert state is not None
        return state

    def _anchored(self) -> set[str]:
        from weld._graph_anchors import graph_files_with_nodes
        raw = (self.root / ".weld" / "graph.json").read_text(encoding="utf-8")
        return graph_files_with_nodes(json.loads(raw))

    def _discover(self, *, write_graph: bool):
        from weld.discover import _discover_single_repo
        return _discover_single_repo(
            self.root, incremental=None, with_sqlite=False,
            write_graph=write_graph,
        )

    def test_publishing_run_marks_the_inventory_published(self) -> None:
        self._discover(write_graph=True)
        self.assertTrue(
            state_vouches_for_graph(
                self._state(), self.root / ".weld" / "graph.json",
            ),
            "a run that published its graph must name it in the inventory",
        )
        self.assertFalse(coverage_stale(self.root))

    def test_a_second_run_leaves_the_inventory_vouching(self) -> None:
        # The no-change refresh reuses the token rather than re-hashing a
        # multi-megabyte graph (bd 85tb.2 kept this path cheap on purpose).
        # Reuse is only sound while the body is untouched, so the pin has to
        # still match afterwards -- that is what makes reuse observable.
        self._discover(write_graph=True)
        first = self._state().published_graph
        self._discover(write_graph=True)
        self.assertEqual(
            self._state().published_graph, first,
            "a no-change refresh must carry the same graph identity forward",
        )
        self.assertFalse(coverage_stale(self.root))

    def _diverge(self) -> None:
        """Ship a module through a run that never publishes its graph.

        The ``--output`` elsewhere / library-caller shape, and what any
        interruption before the graph write leaves behind.
        """
        self._discover(write_graph=True)
        (self.root / "src" / "late.py").write_text("late = 1\n", encoding="utf-8")
        commit_all(self.root, "add late module")
        self._discover(write_graph=False)

    def test_run_that_withholds_its_graph_is_reported_stale(self) -> None:
        self._diverge()
        self.assertIn(
            "src/late.py", self._state().files,
            "precondition: the diverged run recorded the new file",
        )
        self.assertNotIn(
            "src/late.py", self._anchored(),
            "precondition: no reader can see the new file",
        )
        # Every cheaper signal is clean: the file cannot be in
        # meta.discovered_from, and the inventory calls it covered.
        self.assertEqual(files_missing_from_inventory(self.root), set())
        self.assertTrue(
            coverage_stale(self.root),
            "an inventory whose graph was never published must not read as covered",
        )

    def test_one_refresh_closes_the_hole(self) -> None:
        self._diverge()
        self._discover(write_graph=True)  # what auto-refresh runs
        self.assertIn("src/late.py", self._anchored())
        self.assertFalse(
            coverage_stale(self.root), "the repair must also clear the signal",
        )


if __name__ == "__main__":
    unittest.main()
