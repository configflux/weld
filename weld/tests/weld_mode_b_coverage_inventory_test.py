"""What a synthesized coverage inventory claims -- and refuses to (bd r7d7).

ADR 0101's fourth amendment lets a Mode B checkout derive the inventory git
did not carry, from the tracked graph's own anchors. The whole safety of that
lies in how *little* the record says: it must vouch for the graph beside it
(or the coverage probe reports permanent doubt), it must describe it (or
:func:`weld._discover_state_check.mark_state_published` would refuse to
re-stamp it later), and it must never be usable as a delta basis (or an
incremental pass would skip files whose content it only guessed at).

Those four properties are asserted here directly, against a real
``--track-graphs`` clone, because each is a *negative* about a file on disk
that the end-to-end suite next door can only observe through a refresh it
also triggers for other reasons. End-to-end behaviour lives in
:mod:`weld_mode_b_sidecar_synthesis_test`; the gate matrix in
:mod:`weld_worktree_seed_gates_test`.
"""

from __future__ import annotations

import json
import unittest
from unittest import mock

from weld._discover_basis import config_fingerprint, incremental_basis_valid
from weld._discover_state_check import (
    inventory_describes_graph,
    state_vouches_for_graph,
)
from weld._graph_anchors import graph_files_with_nodes
from weld._worktree_seed_inventory import UNPROVEN, synthesize_coverage_inventory
from weld.discovery_state import STATE_FILENAME, load_state
from weld.tests._mode_b_fixture import (
    LEGACY_TRACK_GRAPHS_GITIGNORE,
    ModeBFixture,
)


class SynthesizedInventoryTests(ModeBFixture):
    """A record derived from a graph may only state what the graph proves.

    Stood up in the **pre-ADR-0110** Mode B posture on purpose. Today's
    policy ships the inventory with the graph, and synthesis only ever
    writes where no inventory exists -- so on a current checkout this
    machinery is unreachable by construction. The repositories it still
    serves are the ones an earlier weld initialised, and that is the
    checkout these tests stand in.
    """

    GITIGNORE = LEGACY_TRACK_GRAPHS_GITIGNORE

    def setUp(self) -> None:
        super().setUp()
        self.clone_root = self.clone()
        self.graph_path = self.clone_root / ".weld" / "graph.json"
        self.state_path = self.clone_root / ".weld" / STATE_FILENAME

    def _synthesize(self) -> int | None:
        return synthesize_coverage_inventory(self.clone_root, self.graph_path)

    def _graph(self) -> dict:
        return json.loads(self.graph_path.read_text(encoding="utf-8"))

    def test_coverage_is_exactly_the_graphs_own_anchors(self) -> None:
        """Not the tree, not the config -- only what the graph anchors a node at.

        Anything wider would claim coverage of a file the graph may never
        have read, which is the silence this amendment exists to break.
        """
        self.assertEqual(self._synthesize(), 1)

        state = load_state(self.clone_root)
        self.assertEqual(set(state.files), graph_files_with_nodes(self._graph()))

    def test_it_claims_no_content_hashes(self) -> None:
        """The graph read *some* content; nothing here knows it was this one."""
        self._synthesize()

        state = load_state(self.clone_root)
        self.assertEqual(set(state.files.values()), {UNPROVEN})
        self.assertNotIn("sha256:", UNPROVEN, "must never collide with a real hash")

    def test_it_claims_no_declined_files(self) -> None:
        """A file left unanchored is unproven, never certified as empty.

        ``files_with_no_nodes`` is an exemption from the coverage probe. A
        synthesized record cannot tell a file a strategy read and declined
        from one it never saw, and guessing exempts the second -- which is
        the bug, respelled.
        """
        self._synthesize()

        state = load_state(self.clone_root)
        self.assertEqual(state.files_with_no_nodes, set())
        self.assertEqual(state.files_with_failed_strategy, set())

    def test_it_vouches_for_and_describes_the_tracked_graph(self) -> None:
        """Both halves of the ADR 0101 binding, and both hold by construction.

        Vouching is what stops ``coverage_stale`` reporting permanent doubt;
        describing is what lets a later ``wd discover`` re-stamp the record
        instead of refusing it.
        """
        self._synthesize()

        state = load_state(self.clone_root)
        self.assertTrue(state_vouches_for_graph(state, self.graph_path))
        self.assertTrue(inventory_describes_graph(state, self.graph_path))

    def test_it_is_never_an_incremental_basis(self) -> None:
        """No config claim, so ADR 0008's basis check refuses it outright.

        The load-bearing negative. The hashes are guesses, so a delta
        computed from them would skip files whose content the record never
        saw -- and the sentinel is the second line of defence: even reached,
        every file diffs dirty.
        """
        self._synthesize()

        state = load_state(self.clone_root)
        self.assertIsNone(state.config_fingerprint)
        self.assertFalse(
            incremental_basis_valid(
                state,
                self.graph_path,
                self._graph(),
                config_fingerprint({"sources": []}),
            )
        )

    def test_a_recorded_strategy_fingerprint_is_never_borrowed(self) -> None:
        """The local strategy code did not build this graph, and must not say so.

        ``save_state`` stamps an unset fingerprint from the strategies on
        *this* machine (bd jzxl). Reusing it here would certify a graph
        built by a weld this checkout has never run.
        """
        self._synthesize()

        self.assertEqual(load_state(self.clone_root).strategy_fingerprint, UNPROVEN)

    def test_an_existing_inventory_is_never_replaced(self) -> None:
        """A recorded inventory outranks a derived one, whatever it says."""
        self.state_path.write_text('{"version": 1, "files": {}}\n', encoding="utf-8")

        self.assertIsNone(self._synthesize())
        self.assertEqual(
            self.state_path.read_text(encoding="utf-8"), '{"version": 1, "files": {}}\n'
        )

    def test_a_corrupt_graph_gets_no_coverage_claim(self) -> None:
        """Same rule ADR 0096 gate 4 gives the basis: mask nothing from repair."""
        self.graph_path.write_text("{not json", encoding="utf-8")

        self.assertIsNone(self._synthesize())
        self.assertFalse(self.state_path.exists())

    def test_a_graph_that_anchors_nothing_gets_no_claim(self) -> None:
        """An empty inventory vouches trivially -- writing one buys nothing."""
        self.graph_path.write_text('{"nodes": {}, "edges": []}', encoding="utf-8")

        self.assertIsNone(self._synthesize())
        self.assertFalse(self.state_path.exists())

    def test_a_graph_rewritten_mid_read_is_not_vouched_for(self) -> None:
        """Anchors from one body must never be recorded under another's token.

        The mid-flight hazard ``published_graph_token`` and
        ``mark_state_published`` each guard with a paired digest. Simulated
        by rewriting the body while the anchors are being read, which is the
        only moment the two can come apart.
        """
        real_anchors = graph_files_with_nodes

        def rewrite_then_read(graph: dict) -> set[str]:
            self.graph_path.write_text('{"nodes": {}}', encoding="utf-8")
            return real_anchors(graph)

        with mock.patch(
            "weld._worktree_seed_inventory.graph_files_with_nodes",
            side_effect=rewrite_then_read,
        ):
            self.assertIsNone(self._synthesize())
        self.assertFalse(self.state_path.exists())


if __name__ == "__main__":
    unittest.main()
