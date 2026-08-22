"""A strategy rewrite invalidates the incremental basis (ADR 0008 §7, bd jzxl).

The inventory keys the dirty set on source *content*, so a strategy that
starts emitting a new prop over an unchanged tree re-emits nothing: only nodes
whose own file also changed get the new shape, and every other node keeps
props the previous strategy version wrote. Observed as 2 of 25 build-targets
carrying ``props.keywords`` after ``bazel.py`` gained it, with the other 23 at
``confidence: definite`` and ``wd stale`` reading clean.

Nothing downstream recovers from that. The ADR 0008 per-file repair looks for
files with *zero* nodes; these have nodes, just the wrong generation's. So it
is confidently wrong rather than absent, which is the worse of the two -- a
missing node makes an agent go and look, a stale one does not.

The config fingerprint (bd 4fpj) is the direct prior art and the direct
neighbour: it covers which strategies ``discover.yaml`` points at a file, and
this covers what those strategies do.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld._discover_basis import (
    incremental_basis_valid,
    state_vouches_for_strategies,
    strategy_fingerprint,
)
from weld.discovery_state import DiscoveryState, load_state, save_state


class StrategyFingerprintTest(unittest.TestCase):
    """What the digest covers, and what it must not react to."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.local = self.root / ".weld" / "strategies"
        self.local.mkdir(parents=True)

    def test_a_root_without_local_strategies_still_fingerprints(self) -> None:
        with tempfile.TemporaryDirectory() as bare:
            self.assertTrue(
                strategy_fingerprint(Path(bare)).startswith("sha256:"),
            )

    def test_it_is_stable_across_calls(self) -> None:
        """Determinism first: an unstable digest means a full run every time."""
        self.assertEqual(
            strategy_fingerprint(self.root), strategy_fingerprint(self.root),
        )

    def test_a_project_local_strategy_edit_changes_it(self) -> None:
        target = self.local / "custom.py"
        target.write_text("def extract(root, source, context):\n    pass\n")
        before = strategy_fingerprint(self.root)
        target.write_text(
            "def extract(root, source, context):\n    return None  # new shape\n",
        )
        self.assertNotEqual(before, strategy_fingerprint(self.root))

    def test_adding_a_project_local_strategy_changes_it(self) -> None:
        before = strategy_fingerprint(self.root)
        (self.local / "added.py").write_text("X = 1\n")
        self.assertNotEqual(before, strategy_fingerprint(self.root))

    def test_an_unrelated_file_does_not_change_it(self) -> None:
        """Only strategy code counts; a source edit is the inventory's job."""
        before = strategy_fingerprint(self.root)
        (self.root / "app.py").write_text("X = 1\n")
        (self.local / "notes.md").write_text("not a strategy\n")
        self.assertEqual(before, strategy_fingerprint(self.root))


class BasisInvalidationTest(unittest.TestCase):
    """The digest has to actually gate the incremental run."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / ".weld").mkdir()
        self.graph_path = self.root / ".weld" / "graph.json"
        self.graph_path.write_text('{"meta": {}, "nodes": {}, "edges": []}')

    def _state(self, strategy_fp: str | None) -> DiscoveryState:
        return DiscoveryState(
            files={"a.py": "sha256:x"},
            config_fingerprint="sha256:config",
            strategy_fingerprint=strategy_fp,
        )

    def test_matching_code_vouches(self) -> None:
        fp = strategy_fingerprint(self.root)
        self.assertTrue(state_vouches_for_strategies(self._state(fp), fp))

    def test_changed_code_does_not_vouch(self) -> None:
        self.assertFalse(
            state_vouches_for_strategies(
                self._state("sha256:previous-generation"),
                strategy_fingerprint(self.root),
            ),
        )

    def test_a_state_predating_the_field_does_not_vouch(self) -> None:
        """Fail closed on an upgrade, exactly as the config half does."""
        self.assertFalse(
            state_vouches_for_strategies(
                self._state(None), strategy_fingerprint(self.root),
            ),
        )

    def test_changed_strategy_code_refuses_the_incremental_basis(self) -> None:
        """The whole point: a strategy rewrite must force a full run."""
        self.assertFalse(
            incremental_basis_valid(
                self._state("sha256:previous-generation"),
                self.graph_path,
                {"nodes": {}, "edges": []},
                "sha256:config",
                strategy_fingerprint(self.root),
            ),
        )

    def test_an_omitted_fingerprint_does_not_refuse_the_basis(self) -> None:
        """A caller that does not compute one is not evidence of a change.

        Treating the default as a mismatch would cost every such caller a
        full run on every pass, forever.
        """
        state = self._state(None)
        state.published_graph = None
        self.assertIsNone(state.strategy_fingerprint)
        # Reaches the graph-vouching check rather than short-circuiting here;
        # that it gets that far is the assertion.
        incremental_basis_valid(
            state, self.graph_path, {"nodes": {}, "edges": []},
            "sha256:config",
        )


class StampingTest(unittest.TestCase):
    """``save_state`` records the code the run actually used."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_a_fresh_state_is_stamped_with_live_code(self) -> None:
        save_state(self.root, DiscoveryState(files={"a.py": "sha256:x"}))
        state = load_state(self.root)
        assert state is not None
        self.assertEqual(
            state.strategy_fingerprint, strategy_fingerprint(self.root),
        )

    def test_a_round_tripped_state_keeps_its_own_stamp(self) -> None:
        """Re-saving must not vouch for code that never built the graph.

        ``mark_state_published`` loads a state and writes it straight back.
        Re-stamping there would silently re-point an old inventory at
        today's strategies -- the defect this field exists to catch,
        introduced by the field itself.
        """
        save_state(
            self.root,
            DiscoveryState(
                files={"a.py": "sha256:x"},
                strategy_fingerprint="sha256:the-run-that-built-it",
            ),
        )
        reloaded = load_state(self.root)
        assert reloaded is not None
        save_state(self.root, reloaded)
        again = load_state(self.root)
        assert again is not None
        self.assertEqual(
            "sha256:the-run-that-built-it", again.strategy_fingerprint,
        )


if __name__ == "__main__":
    unittest.main()
