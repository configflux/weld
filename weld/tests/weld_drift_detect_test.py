"""Tests for workspace ledger drift detection.

Covers:

* :func:`detect_drift` -- classifies children as drifted/stable/added/removed
  based on ``graph_sha256`` and ``head_sha`` changes in the workspace ledger.
* :class:`DriftResult` -- frozen attribute access and ``has_changes`` property.
"""

from __future__ import annotations

import unittest

from weld.cross_repo.incremental import DriftResult, detect_drift


def _ledger_entry(
    *, graph_sha256: str = "abc123", head_sha: str = "def456",
) -> dict[str, object]:
    return {"graph_sha256": graph_sha256, "head_sha": head_sha, "status": "present"}


class DetectDriftTests(unittest.TestCase):
    """Drift detection from workspace-state.json ledger snapshots."""

    def test_identical_snapshots_yield_all_stable(self) -> None:
        prior = {"a": _ledger_entry(), "b": _ledger_entry()}
        current = {"a": _ledger_entry(), "b": _ledger_entry()}
        result = detect_drift(prior, current)
        self.assertEqual(result.stable, frozenset({"a", "b"}))
        self.assertEqual(result.drifted, frozenset())
        self.assertFalse(result.has_changes)

    def test_graph_sha256_change_marks_drifted(self) -> None:
        prior = {"a": _ledger_entry(graph_sha256="old"), "b": _ledger_entry()}
        current = {"a": _ledger_entry(graph_sha256="new"), "b": _ledger_entry()}
        result = detect_drift(prior, current)
        self.assertEqual(result.drifted, frozenset({"a"}))
        self.assertEqual(result.stable, frozenset({"b"}))
        self.assertTrue(result.has_changes)

    def test_head_sha_change_marks_drifted(self) -> None:
        prior = {"a": _ledger_entry(head_sha="old")}
        current = {"a": _ledger_entry(head_sha="new")}
        result = detect_drift(prior, current)
        self.assertEqual(result.drifted, frozenset({"a"}))
        self.assertTrue(result.has_changes)

    def test_new_child_is_added(self) -> None:
        prior = {"a": _ledger_entry()}
        current = {"a": _ledger_entry(), "b": _ledger_entry()}
        result = detect_drift(prior, current)
        self.assertEqual(result.added, frozenset({"b"}))
        self.assertEqual(result.stable, frozenset({"a"}))
        self.assertTrue(result.has_changes)

    def test_removed_child_is_detected(self) -> None:
        prior = {"a": _ledger_entry(), "b": _ledger_entry()}
        current = {"a": _ledger_entry()}
        result = detect_drift(prior, current)
        self.assertEqual(result.removed, frozenset({"b"}))
        self.assertEqual(result.stable, frozenset({"a"}))
        self.assertTrue(result.has_changes)

    def test_both_hashes_unchanged_is_stable(self) -> None:
        """Mtime or other metadata changes do not trigger drift."""
        entry = _ledger_entry(graph_sha256="same", head_sha="same")
        prior = {"a": {**entry, "last_seen_utc": "2025-01-01T00:00:00Z"}}
        current = {"a": {**entry, "last_seen_utc": "2025-06-01T00:00:00Z"}}
        result = detect_drift(prior, current)
        self.assertEqual(result.stable, frozenset({"a"}))
        self.assertFalse(result.has_changes)

    def test_empty_prior_treats_all_as_added(self) -> None:
        current = {"a": _ledger_entry(), "b": _ledger_entry()}
        result = detect_drift({}, current)
        self.assertEqual(result.added, frozenset({"a", "b"}))
        self.assertEqual(result.stable, frozenset())

    def test_simultaneous_drift_of_multiple_children(self) -> None:
        prior = {
            "a": _ledger_entry(graph_sha256="a1"),
            "b": _ledger_entry(graph_sha256="b1"),
            "c": _ledger_entry(graph_sha256="c1"),
        }
        current = {
            "a": _ledger_entry(graph_sha256="a2"),
            "b": _ledger_entry(graph_sha256="b2"),
            "c": _ledger_entry(graph_sha256="c1"),
        }
        result = detect_drift(prior, current)
        self.assertEqual(result.drifted, frozenset({"a", "b"}))
        self.assertEqual(result.stable, frozenset({"c"}))


class DriftResultTests(unittest.TestCase):
    """DriftResult attribute tests."""

    def test_frozenset_attributes(self) -> None:
        result = DriftResult(
            drifted={"a"}, stable={"b"}, added={"c"}, removed={"d"},
        )
        self.assertIsInstance(result.drifted, frozenset)
        self.assertIsInstance(result.stable, frozenset)
        self.assertIsInstance(result.added, frozenset)
        self.assertIsInstance(result.removed, frozenset)

    def test_has_changes_false_when_all_stable(self) -> None:
        result = DriftResult(
            drifted=set(), stable={"a"}, added=set(), removed=set(),
        )
        self.assertFalse(result.has_changes)

    def test_has_changes_true_for_drifted(self) -> None:
        result = DriftResult(
            drifted={"a"}, stable=set(), added=set(), removed=set(),
        )
        self.assertTrue(result.has_changes)

    def test_has_changes_true_for_added(self) -> None:
        result = DriftResult(
            drifted=set(), stable=set(), added={"a"}, removed=set(),
        )
        self.assertTrue(result.has_changes)

    def test_has_changes_true_for_removed(self) -> None:
        result = DriftResult(
            drifted=set(), stable=set(), added=set(), removed={"a"},
        )
        self.assertTrue(result.has_changes)


if __name__ == "__main__":
    unittest.main()
