"""A footprint-less source entry gets an identity to fail loudly under (bd um00).

``files_with_failed_strategy`` (bd hch4) records failures keyed by
repo-relative path. A source entry with no ``glob``/``path``/``files`` key --
a command-only ``external_json`` adapter is the shipped example -- resolves
no files at all, so that channel is a structural no-op for it: there is no
path to record. This is the contract underneath the entry-keyed sibling that
closes the gap. The end-to-end half, over a real ``discover()``, is
``discover_failed_source_repairs_test``.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from weld._discover_basis import entry_fingerprint, sources_needing_retry
from weld._discover_state_check import save_state_for_graph
from weld.discovery_state import DiscoveryState, load_state, save_state
from weld.strategies._strategy_failure import (
    SOURCE_FAILURE_KEY,
    KIND_NONZERO_EXIT,
    drain_source_failures,
    note_source_failure,
)


class EntryFingerprintTest(unittest.TestCase):
    """Stable identity for a source entry's own config dict."""

    def test_same_entry_same_fingerprint(self) -> None:
        entry = {"strategy": "external_json", "command": "tools/adapter"}
        self.assertEqual(entry_fingerprint(entry), entry_fingerprint(dict(entry)))

    def test_key_order_does_not_matter(self) -> None:
        a = {"strategy": "external_json", "command": "x"}
        b = {"command": "x", "strategy": "external_json"}
        self.assertEqual(entry_fingerprint(a), entry_fingerprint(b))

    def test_different_content_different_fingerprint(self) -> None:
        a = {"strategy": "external_json", "command": "x"}
        b = {"strategy": "external_json", "command": "y"}
        self.assertNotEqual(entry_fingerprint(a), entry_fingerprint(b))

    def test_position_in_a_list_is_irrelevant(self) -> None:
        """The whole point: a reorder of ``sources:`` cannot orphan a record."""
        entry = {"strategy": "external_json", "command": "x"}
        sources_a = [{"strategy": "python_module", "glob": "**/*.py"}, entry]
        sources_b = [entry, {"strategy": "python_module", "glob": "**/*.py"}]
        fp_a = entry_fingerprint(sources_a[1])
        fp_b = entry_fingerprint(sources_b[0])
        self.assertEqual(fp_a, fp_b)


class ReportingChannelTest(unittest.TestCase):
    def test_records_kind_and_reason(self) -> None:
        context: dict = {}
        note_source_failure(context, "eid1", kind=KIND_NONZERO_EXIT, reason="exited 1")
        self.assertEqual(
            {"eid1": {"kind": "nonzero_exit", "reason": "exited 1"}},
            context[SOURCE_FAILURE_KEY],
        )

    def test_a_second_failure_for_the_same_entry_overwrites(self) -> None:
        """Latest attempt wins -- not accumulated like the file-keyed sibling."""
        context: dict = {}
        note_source_failure(context, "eid1", kind="timeout", reason="timed out after 5s")
        note_source_failure(context, "eid1", kind=KIND_NONZERO_EXIT, reason="exited 1")
        self.assertEqual(
            {"kind": "nonzero_exit", "reason": "exited 1"},
            context[SOURCE_FAILURE_KEY]["eid1"],
        )

    def test_distinct_entries_both_survive(self) -> None:
        context: dict = {}
        note_source_failure(context, "eid1", kind=KIND_NONZERO_EXIT, reason="a")
        note_source_failure(context, "eid2", kind=KIND_NONZERO_EXIT, reason="b")
        self.assertEqual({"eid1", "eid2"}, set(context[SOURCE_FAILURE_KEY]))

    def test_reason_is_bounded(self) -> None:
        context: dict = {}
        note_source_failure(context, "eid1", kind=KIND_NONZERO_EXIT, reason="x" * 500)
        self.assertEqual(200, len(context[SOURCE_FAILURE_KEY]["eid1"]["reason"]))

    def test_drain_empties_the_bucket(self) -> None:
        context: dict = {}
        note_source_failure(context, "eid1", kind=KIND_NONZERO_EXIT, reason="a")
        self.assertEqual(
            {"eid1": {"kind": "nonzero_exit", "reason": "a"}},
            drain_source_failures(context),
        )
        self.assertEqual({}, drain_source_failures(context))
        self.assertNotIn(SOURCE_FAILURE_KEY, context)

    def test_a_contextless_caller_is_not_an_error(self) -> None:
        note_source_failure(None, "eid1", kind=KIND_NONZERO_EXIT, reason="a")
        self.assertEqual({}, drain_source_failures(None))

    def test_a_poisoned_key_is_replaced_not_trusted(self) -> None:
        context: dict = {SOURCE_FAILURE_KEY: "not-a-dict"}
        note_source_failure(context, "eid1", kind=KIND_NONZERO_EXIT, reason="a")
        self.assertEqual(
            {"eid1": {"kind": "nonzero_exit", "reason": "a"}},
            drain_source_failures(context),
        )


class SourcesNeedingRetryTest(unittest.TestCase):
    _EXTERNAL_JSON = {"strategy": "external_json", "command": "tools/adapter"}

    def test_no_state_means_nothing_to_retry(self) -> None:
        self.assertEqual(frozenset(), sources_needing_retry([self._EXTERNAL_JSON], None))

    def test_a_clean_state_means_nothing_to_retry(self) -> None:
        state = DiscoveryState(files={})
        self.assertEqual(
            frozenset(), sources_needing_retry([self._EXTERNAL_JSON], state),
        )

    def test_a_recorded_failure_for_a_still_present_entry_is_forced(self) -> None:
        eid = entry_fingerprint(self._EXTERNAL_JSON)
        state = DiscoveryState(
            files={},
            sources_with_failed_strategy={eid: {"kind": "nonzero_exit", "reason": "x"}},
        )
        self.assertEqual(
            frozenset({eid}), sources_needing_retry([self._EXTERNAL_JSON], state),
        )

    def test_a_failure_for_an_entry_no_longer_present_is_not_forced(self) -> None:
        """Editing or removing the entry already forces a full run (config
        fingerprint mismatch) -- this function must not also force it here,
        or a stale record for an unrelated old entry id would linger."""
        stale_eid = "sha256:" + "0" * 64
        state = DiscoveryState(
            files={},
            sources_with_failed_strategy={stale_eid: {"kind": "timeout", "reason": "x"}},
        )
        self.assertEqual(
            frozenset(), sources_needing_retry([self._EXTERNAL_JSON], state),
        )

    def test_only_the_failing_entry_is_named_among_several(self) -> None:
        healthy = {"strategy": "python_module", "glob": "**/*.py"}
        eid = entry_fingerprint(self._EXTERNAL_JSON)
        state = DiscoveryState(
            files={},
            sources_with_failed_strategy={eid: {"kind": "timeout", "reason": "x"}},
        )
        self.assertEqual(
            frozenset({eid}),
            sources_needing_retry([healthy, self._EXTERNAL_JSON], state),
        )


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
                sources_with_failed_strategy={
                    "eid-b": {"kind": "timeout", "reason": "y"},
                    "eid-a": {"kind": "nonzero_exit", "reason": "x"},
                },
            ),
        )
        self.assertEqual(
            ["eid-a", "eid-b"],
            list(self._raw()["sources_with_failed_strategy"]),
        )
        loaded = load_state(self.root)
        self.assertEqual(
            {"nonzero_exit"}, {loaded.sources_with_failed_strategy["eid-a"]["kind"]},
        )

    def test_a_state_from_an_older_weld_reads_as_no_failed_sources(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({
                "version": 1,
                "files": {"a.py": "sha256:x"},
                "files_with_failed_strategy": ["b.py"],
            }),
            encoding="utf-8",
        )
        state = load_state(self.root)
        self.assertEqual({}, state.sources_with_failed_strategy)
        self.assertEqual({"b.py"}, state.files_with_failed_strategy)

    def test_a_malformed_top_level_value_reads_as_empty(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({
                "version": 1,
                "files": {"a.py": "sha256:x"},
                "sources_with_failed_strategy": "not-a-dict",
            }),
            encoding="utf-8",
        )
        self.assertEqual({}, load_state(self.root).sources_with_failed_strategy)

    def test_a_malformed_entry_value_is_dropped_not_trusted(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({
                "version": 1,
                "files": {"a.py": "sha256:x"},
                "sources_with_failed_strategy": {
                    "eid-good": {"kind": "timeout", "reason": "x"},
                    "eid-bad": "not-a-dict",
                },
            }),
            encoding="utf-8",
        )
        state = load_state(self.root)
        self.assertEqual({"eid-good"}, set(state.sources_with_failed_strategy))


class SaveStateForGraphThreadsSourceFailedTest(unittest.TestCase):
    """``source_failed`` is carried straight through -- no graph-anchoring bound."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / ".weld").mkdir()

    def test_source_failed_lands_verbatim(self) -> None:
        save_state_for_graph(
            self.root, {}, {"nodes": {}, "edges": [], "meta": {}},
            graph_published=False,
            source_failed={"eid1": {"kind": "timeout", "reason": "slow"}},
        )
        state = load_state(self.root)
        self.assertEqual(
            {"eid1": {"kind": "timeout", "reason": "slow"}},
            state.sources_with_failed_strategy,
        )

    def test_absent_source_failed_is_the_empty_dict(self) -> None:
        save_state_for_graph(
            self.root, {}, {"nodes": {}, "edges": [], "meta": {}},
            graph_published=False,
        )
        self.assertEqual({}, load_state(self.root).sources_with_failed_strategy)


if __name__ == "__main__":
    unittest.main()
