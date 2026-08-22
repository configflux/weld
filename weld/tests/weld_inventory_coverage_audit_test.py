"""An inventory may only vouch for a graph whose coverage it describes (bd qmbp).

The third question ADR 0101 did not ask. Its two existing probes are:

* ``inventory_vouches_for_graph`` -- *which* graph did this inventory name?
  (bd esww/hfm6: it named none. bd wq9i: the body was swapped afterwards.)
* ``files_missing_from_inventory`` -- which in-scope files did it *never record*?

Neither asks whether what the inventory says about the graph is true of it. A
file recorded in ``files``, absent from ``files_with_no_nodes``, and carrying
no node in the graph is "covered" as far as both probes can see, so
``coverage_stale`` reads clean, so auto-refresh never runs, so the ADR 0008
per-file repair -- which would close the hole in a single pass -- is never
scheduled. Three tracked modules answered "no such symbol" that way while
freshness reported no staleness at all; 104 files were absent on the checkout
that reported it.

``save_state_for_graph`` cannot produce that shape: it derives
``files_with_no_nodes`` from the graph in hand, so its half holds by
construction. ``mark_state_published`` can -- it takes the inventory off disk
and the body off disk, and until now assumed they came from the same run. So
the claim is audited at the moment it is made, and refused when it does not
hold; the refusal converges, because a state that vouches for nothing forces
the next discovery full, and a full run publishes graph and inventory
together.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from weld._discover_state_check import (inventory_describes_graph,
                                        mark_state_published,
                                        state_vouches_for_graph)
from weld._staleness_coverage import coverage_stale
from weld.discovery_state import load_state
from weld.tests._coverage_stale_lib import CoverageFixture, walked_files


def _graph_body(anchored: set[str], *, declared_in: set[str] | None = None) -> str:
    """A graph anchoring exactly *anchored* via ``props.file``.

    *declared_in* anchors through the other accepted key, so the audit is
    pinned to agree with ``graph_files_with_nodes`` on both.
    """
    nodes: dict[str, dict] = {
        f"file:{rel}": {
            "id": f"file:{rel}", "type": "file", "props": {"file": rel},
        }
        for rel in sorted(anchored)
    }
    for rel in sorted(declared_in or ()):
        nodes[f"symbol:{rel}"] = {
            "id": f"symbol:{rel}", "type": "symbol",
            "props": {"declared_in": rel},
        }
    return json.dumps({"nodes": nodes, "edges": [], "meta": {}})


class InventoryCoverageAuditTest(CoverageFixture):
    """``mark_state_published`` refuses to vouch for a graph with a hole in it."""

    @property
    def graph(self) -> Path:
        return self.root / ".weld" / "graph.json"

    def write_graph(self, anchored: set[str], **kw) -> None:
        self.graph.write_text(_graph_body(anchored, **kw), encoding="utf-8")

    def stamp(self) -> None:
        """Publish through the real vouching path, from an unvouched state."""
        mark_state_published(self.root, self.graph)

    def assertStamped(self) -> None:
        state = load_state(self.root)
        self.assertIsNotNone(state.published_graph)
        self.assertTrue(state_vouches_for_graph(state, self.graph))
        self.assertFalse(coverage_stale(self.root))

    def assertWithheld(self) -> None:
        state = load_state(self.root)
        self.assertIsNone(state.published_graph)
        self.assertFalse(state_vouches_for_graph(state, self.graph))
        # The whole point of withholding: freshness must now report the hole,
        # because that report is what schedules the repair.
        self.assertTrue(coverage_stale(self.root))

    # --- the bug ---------------------------------------------------------

    def test_inventory_claiming_an_unanchored_file_is_not_vouched_for(self) -> None:
        """The bd qmbp shape: recorded as node-bearing, absent from the graph."""
        walked = walked_files(self.root)
        self.assertIn("src/b.py", walked)
        self.write_graph(walked - {"src/b.py"})
        self.save_state(graph_published=False)

        self.stamp()

        self.assertWithheld()

    def test_withholding_is_what_makes_the_hole_visible(self) -> None:
        """Without the audit this exact pair reads clean -- the regression."""
        walked = walked_files(self.root)
        self.write_graph(walked - {"src/b.py"})
        self.save_state(graph_published=False)

        # Freshness before the stamp already reports the hole only because
        # nothing has vouched yet; the failure mode is the stamp making it
        # disappear. Pin that it does not.
        self.stamp()
        self.assertTrue(coverage_stale(self.root))

    # --- the healthy paths it must not disturb ---------------------------

    def test_coherent_inventory_is_vouched_for(self) -> None:
        self.write_graph(walked_files(self.root))
        self.save_state(graph_published=False)

        self.stamp()

        self.assertStamped()

    def test_declared_in_counts_as_an_anchor(self) -> None:
        """The audit must accept both anchor keys, as the repair does."""
        walked = walked_files(self.root)
        self.write_graph(walked - {"src/b.py"}, declared_in={"src/b.py"})
        self.save_state(graph_published=False)

        self.stamp()

        self.assertStamped()

    def test_files_with_no_nodes_does_not_block_the_stamp(self) -> None:
        """A legitimately node-less file is intent, not a hole."""
        walked = walked_files(self.root)
        self.write_graph(walked - {"src/b.py"})
        self.save_state(graph_published=False, no_nodes={"src/b.py"})

        self.stamp()

        self.assertStamped()

    def test_already_vouching_state_is_left_alone(self) -> None:
        """The early return still precedes the audit; no redundant re-stamp."""
        self.write_graph(walked_files(self.root))
        self.save_state(graph_published=True)
        before = load_state(self.root).published_graph

        self.stamp()

        self.assertEqual(load_state(self.root).published_graph, before)

    def test_non_canonical_target_leaves_the_state_untouched(self) -> None:
        """``--output`` elsewhere: readers still load the older body."""
        self.write_graph(walked_files(self.root))
        self.save_state(graph_published=False)

        mark_state_published(self.root, self.root / ".weld" / "elsewhere.json")

        self.assertIsNone(load_state(self.root).published_graph)

    # --- degraded inputs fail closed -------------------------------------

    def test_corrupt_body_is_not_vouched_for(self) -> None:
        self.save_state(graph_published=False)
        self.graph.write_text("{ not json", encoding="utf-8")

        self.stamp()

        self.assertIsNone(load_state(self.root).published_graph)

    def test_non_object_body_is_not_vouched_for(self) -> None:
        self.save_state(graph_published=False)
        self.graph.write_text("[]", encoding="utf-8")

        self.stamp()

        self.assertIsNone(load_state(self.root).published_graph)


class InventoryDescribesGraphTest(CoverageFixture):
    """The predicate on its own, independent of who calls it."""

    @property
    def graph(self) -> Path:
        return self.root / ".weld" / "graph.json"

    def test_missing_state_describes_nothing(self) -> None:
        self.graph.write_text(_graph_body(set()), encoding="utf-8")
        self.assertFalse(inventory_describes_graph(None, self.graph))

    def test_absent_body_describes_nothing(self) -> None:
        self.save_state(graph_published=False)
        state = load_state(self.root)
        self.assertFalse(
            inventory_describes_graph(state, self.root / ".weld" / "gone.json"),
        )

    def test_full_coverage_holds(self) -> None:
        self.graph.write_text(
            _graph_body(walked_files(self.root)), encoding="utf-8",
        )
        self.save_state(graph_published=False)
        self.assertTrue(
            inventory_describes_graph(load_state(self.root), self.graph),
        )

    def test_a_single_unanchored_file_breaks_it(self) -> None:
        self.graph.write_text(
            _graph_body(walked_files(self.root) - {"pkg/one.py"}),
            encoding="utf-8",
        )
        self.save_state(graph_published=False)
        self.assertFalse(
            inventory_describes_graph(load_state(self.root), self.graph),
        )

    def test_a_graph_richer_than_the_inventory_still_holds(self) -> None:
        """Extra anchors are not this audit's business -- only holes are."""
        self.graph.write_text(
            _graph_body(walked_files(self.root) | {"pkg/vendor/dep.py"}),
            encoding="utf-8",
        )
        self.save_state(graph_published=False)
        self.assertTrue(
            inventory_describes_graph(load_state(self.root), self.graph),
        )


if __name__ == "__main__":
    unittest.main()
