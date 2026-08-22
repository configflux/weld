"""Intent and failure are kept apart in the discovery state (bd hch4).

The end-to-end half is ``discover_failed_strategy_repairs_test``. This is the
contract underneath it: which set each consumer exempts, what the reporting
channel promises, and how a state written by a weld that had only one set is
read by a weld that has two.

The whole behavioural change is one row of the exemption matrix. Both sets say
"this file has no anchoring node and we know why", and both are exempt from the
vouching audit -- an inventory that names its holes is not an inventory that
misdescribes the body beside it. Only ``files_with_no_nodes`` is exempt from the
ADR 0008 per-file repair, because only a strategy that ran and declined has
decided anything worth standing on.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from weld._discover_state_check import inventory_describes_graph, save_state_for_graph
from weld._graph_anchors import compute_files_with_no_nodes, files_missing_from_graph
from weld.discovery_state import DiscoveryState, load_state, save_state
from weld.strategies._strategy_failure import (
    STRATEGY_FAILURE_KEY,
    drain_strategy_failures,
    note_strategy_failure,
)


def _graph(*anchored: str) -> dict:
    return {
        "nodes": {
            f"file:{rel}": {"type": "file", "props": {"file": rel}}
            for rel in anchored
        },
        "edges": [],
        "meta": {},
    }


class ComputeFilesWithNoNodesTest(unittest.TestCase):
    def test_withholds_the_files_no_strategy_spoke_for(self) -> None:
        out = compute_files_with_no_nodes(
            {"a.py", "b.py", "c.py"}, {"a.py"}, {"c.py"},
        )
        self.assertEqual({"b.py"}, out)

    def test_absent_failure_set_is_the_plain_complement(self) -> None:
        self.assertEqual(
            {"b.py"}, compute_files_with_no_nodes({"a.py", "b.py"}, {"a.py"}),
        )


class ExemptionMatrixTest(unittest.TestCase):
    """One row differs, and it is the row the defect lived in."""

    def _state(self, **kw) -> DiscoveryState:
        return DiscoveryState(files={"kept.py": "sha256:x", "hole.py": "sha256:y"}, **kw)

    def test_a_declined_file_is_exempt_from_the_repair(self) -> None:
        state = self._state(files_with_no_nodes={"hole.py"})
        self.assertEqual(
            set(),
            files_missing_from_graph(state, set(state.files), {"kept.py"}),
        )

    def test_a_failed_file_is_not_exempt_from_the_repair(self) -> None:
        state = self._state(files_with_failed_strategy={"hole.py"})
        self.assertEqual(
            {"hole.py"},
            files_missing_from_graph(state, set(state.files), {"kept.py"}),
        )


class VanishedFileIsAFailureTest(unittest.TestCase):
    """A file that vanished mid-run decided nothing either (bd rt65).

    ``compute_files_with_no_nodes`` withholds the files a strategy *reported*
    as failures, which covers every strategy served the run-start listing from
    the glob memo. The ones that re-list inside their own ``extract``
    (``pydantic``, ``fastapi``, ``worker_stage``) never see a file deleted
    before that listing, so they report nothing and the orchestrator would
    otherwise write the path into ``files_with_no_nodes`` -- the set that
    means "a strategy looked and decided". That exemption is keyed on the path
    alone, so a file that returned byte-identical would never be dirty and the
    ADR 0008 per-file repair would never re-run it.

    Only this layer can tell: the inventory says the file belonged to a
    source, and no strategy claimed it.
    """

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / ".weld").mkdir()
        (self.root / "kept.py").write_text("X = 1\n", encoding="utf-8")
        (self.root / "present.py").write_text("Y = 2\n", encoding="utf-8")
        # ``gone.py`` is deliberately never created: the run hashed it at
        # start, and by state-write time it is not on disk.
        self.hashes = {
            "kept.py": "sha256:a",
            "present.py": "sha256:b",
            "gone.py": "sha256:c",
        }

    def _save(self) -> DiscoveryState:
        save_state_for_graph(
            self.root, self.hashes, _graph("kept.py"), graph_published=True,
        )
        state = load_state(self.root)
        assert state is not None
        return state

    def test_a_vanished_file_is_recorded_as_a_failure(self) -> None:
        state = self._save()
        self.assertIn("gone.py", state.files_with_failed_strategy)

    def test_a_vanished_file_is_not_recorded_as_a_decision(self) -> None:
        state = self._save()
        self.assertNotIn("gone.py", state.files_with_no_nodes)

    def test_a_present_file_without_a_node_is_still_a_decision(self) -> None:
        """The distinction has to cut both ways to be worth anything.

        A file that is on disk and produced no node is exactly what
        ``files_with_no_nodes`` is for; sweeping it into the failure set
        would put a file weld never had trouble with into the repair queue
        on every run, forever.
        """
        state = self._save()
        self.assertIn("present.py", state.files_with_no_nodes)
        self.assertNotIn("present.py", state.files_with_failed_strategy)

    def test_the_vanished_file_stays_in_the_repair_queue(self) -> None:
        """The point of the reclassification, asserted at the consumer."""
        state = self._save()
        self.assertIn(
            "gone.py",
            files_missing_from_graph(state, set(state.files), {"kept.py"}),
        )


class InventoryDescribesGraphTest(unittest.TestCase):
    """The vouching audit tolerates a named hole, never an unexplained one."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / ".weld").mkdir()
        self.graph = self.root / ".weld" / "graph.json"
        self.graph.write_text(json.dumps(_graph("kept.py")), encoding="utf-8")
        self.files = {"kept.py": "sha256:x", "hole.py": "sha256:y"}

    def test_an_unexplained_hole_fails_the_audit(self) -> None:
        self.assertFalse(
            inventory_describes_graph(DiscoveryState(files=self.files), self.graph),
        )

    def test_a_recorded_failure_passes_the_audit(self) -> None:
        state = DiscoveryState(
            files=self.files, files_with_failed_strategy={"hole.py"},
        )
        self.assertTrue(inventory_describes_graph(state, self.graph))

    def test_a_failure_that_does_not_cover_the_hole_still_fails(self) -> None:
        state = DiscoveryState(
            files=self.files, files_with_failed_strategy={"unrelated.py"},
        )
        self.assertFalse(inventory_describes_graph(state, self.graph))


class StateRoundTripTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.path = self.root / ".weld" / "discovery-state.json"

    def _raw(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_round_trips_sorted(self) -> None:
        save_state(
            self.root,
            DiscoveryState(
                files={"a.py": "sha256:x"},
                files_with_failed_strategy={"c.py", "b.py"},
            ),
        )
        self.assertEqual(["b.py", "c.py"], self._raw()["files_with_failed_strategy"])
        self.assertEqual(
            {"b.py", "c.py"}, load_state(self.root).files_with_failed_strategy,
        )

    def test_a_state_from_an_older_weld_reads_as_no_failures(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({
                "version": 1,
                "created_at": "2026-01-01T00:00:00+00:00",
                "files": {"a.py": "sha256:x"},
                "files_with_no_nodes": ["b.py"],
            }),
            encoding="utf-8",
        )
        state = load_state(self.root)
        self.assertEqual(set(), state.files_with_failed_strategy)
        self.assertEqual({"b.py"}, state.files_with_no_nodes)

    def test_a_malformed_value_reads_as_no_failures(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({
                "version": 1,
                "created_at": "2026-01-01T00:00:00+00:00",
                "files": {"a.py": "sha256:x"},
                "files_with_failed_strategy": "not-a-list",
            }),
            encoding="utf-8",
        )
        self.assertEqual(set(), load_state(self.root).files_with_failed_strategy)


class SaveStateForGraphTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / ".weld").mkdir()

    def _save(self, hashes: dict, graph: dict, failed: set[str]) -> DiscoveryState:
        # Materialize the inventory. ``current_hashes`` comes from hashing
        # files the run walked, so every key names a file that was on disk --
        # and since bd rt65 one that is *not* there any more is read as a
        # vanish rather than as a strategy's decision. A fixture of paths
        # that never existed would exercise that branch by accident.
        for rel in hashes:
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("X = 1\n", encoding="utf-8")
        save_state_for_graph(
            self.root, hashes, graph, graph_published=False, strategy_failed=failed,
        )
        state = load_state(self.root)
        assert state is not None
        return state

    def test_splits_the_two_reasons(self) -> None:
        state = self._save(
            {"anchored.py": "1", "declined.py": "2", "failed.py": "3"},
            _graph("anchored.py"),
            {"failed.py"},
        )
        self.assertEqual({"declined.py"}, state.files_with_no_nodes)
        self.assertEqual({"failed.py"}, state.files_with_failed_strategy)

    def test_bounds_the_failure_set_to_the_inventory(self) -> None:
        state = self._save({"a.py": "1"}, _graph(), {"a.py", "not-tracked.py"})
        self.assertEqual({"a.py"}, state.files_with_failed_strategy)

    def test_a_file_the_graph_still_anchors_is_not_a_hole(self) -> None:
        """An incremental pass reports a whole source when its strategy will
        not load; a clean sibling whose nodes survived the purge is fine."""
        state = self._save(
            {"clean.py": "1", "dirty.py": "2"},
            _graph("clean.py"),
            {"clean.py", "dirty.py"},
        )
        self.assertEqual({"dirty.py"}, state.files_with_failed_strategy)
        self.assertEqual(set(), state.files_with_no_nodes)


class ReportingChannelTest(unittest.TestCase):
    def test_notes_accumulate_across_producers(self) -> None:
        context: dict = {}
        note_strategy_failure(context, ["a.py"])
        note_strategy_failure(context, ["b.py", "a.py"])
        self.assertEqual({"a.py", "b.py"}, context[STRATEGY_FAILURE_KEY])

    def test_drain_empties_the_bucket(self) -> None:
        context: dict = {}
        note_strategy_failure(context, ["a.py"])
        self.assertEqual({"a.py"}, drain_strategy_failures(context))
        self.assertEqual(set(), drain_strategy_failures(context))
        self.assertNotIn(STRATEGY_FAILURE_KEY, context)

    def test_a_contextless_caller_is_not_an_error(self) -> None:
        """A strategy called directly must not fail on orchestrator bookkeeping."""
        note_strategy_failure(None, ["a.py"])
        self.assertEqual(set(), drain_strategy_failures(None))

    def test_a_poisoned_key_is_replaced_not_trusted(self) -> None:
        context: dict = {STRATEGY_FAILURE_KEY: "not-a-set"}
        note_strategy_failure(context, ["a.py"])
        self.assertEqual({"a.py"}, drain_strategy_failures(context))


if __name__ == "__main__":
    unittest.main()
