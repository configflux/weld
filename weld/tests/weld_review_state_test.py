"""Review-state persistence schema (ADR 0055).

The review queue stores decisions in ``.weld/review-state.json`` using
schema v1:

  {
    "version": 1,
    "decisions": {
      "<edge-id>": {
        "decision": "accepted" | "rejected",
        "reason": "...",
        "reviewer": "...",
        "ts": "...",
        "edge_snapshot": {...}
      }
    }
  }

This test pins the read/write roundtrip and the atomic-write contract
(the state file never appears half-written on disk).
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


from weld._review_state import (  # noqa: E402
    REVIEW_STATE_VERSION,
    Decision,
    ReviewState,
    load_state,
    save_state,
    state_path,
)


class ReviewStatePathTest(unittest.TestCase):
    """``state_path`` rejects traversal escapes by anchoring under ``.weld/``."""

    def test_state_path_is_under_weld_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = state_path(root)
            self.assertEqual(p, root / ".weld" / "review-state.json")
            # The path must be relative to the supplied root; it cannot
            # be a writable target outside .weld/.
            self.assertTrue(str(p).startswith(str(root)))


class ReviewStateLoadEmptyTest(unittest.TestCase):
    """Loading a missing file returns an empty v1 state, not an error."""

    def test_load_missing_file_returns_empty_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = load_state(Path(tmp))
            self.assertEqual(state.version, REVIEW_STATE_VERSION)
            self.assertEqual(state.decisions, {})


class ReviewStateRoundtripTest(unittest.TestCase):
    """Save then load returns the same decisions, preserving snapshot detail."""

    def test_save_then_load_preserves_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = ReviewState(
                version=REVIEW_STATE_VERSION,
                decisions={
                    "abc123": Decision(
                        decision="accepted",
                        reason="LGTM",
                        reviewer="dev@example.org",
                        ts="2026-05-10T00:00:00+00:00",
                        edge_snapshot={
                            "from": "a", "to": "b", "type": "calls",
                            "props": {
                                "source_strategy": "py",
                                "confidence": "speculative",
                            },
                        },
                    ),
                },
            )
            save_state(root, state)
            loaded = load_state(root)
            self.assertEqual(loaded.version, state.version)
            self.assertIn("abc123", loaded.decisions)
            d = loaded.decisions["abc123"]
            self.assertEqual(d.decision, "accepted")
            self.assertEqual(d.reason, "LGTM")
            self.assertEqual(d.reviewer, "dev@example.org")
            self.assertEqual(d.edge_snapshot["from"], "a")

    def test_save_is_atomic_via_workspace_state_helper(self) -> None:
        """The state file is written atomically; no temp leftover."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".weld").mkdir()
            state = ReviewState(
                version=REVIEW_STATE_VERSION,
                decisions={"x": Decision(
                    decision="rejected", reason="", reviewer="a", ts="t",
                    edge_snapshot={},
                )},
            )
            save_state(root, state)
            entries = [p.name for p in (root / ".weld").iterdir()]
            # exactly review-state.json with no .tmp leftovers
            self.assertIn("review-state.json", entries)
            self.assertFalse(
                any(".tmp." in e for e in entries),
                f"temp file leak: {entries}",
            )

    def test_corrupt_file_falls_back_to_empty_state(self) -> None:
        """A junk JSON file should not crash the CLI."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".weld").mkdir()
            (root / ".weld" / "review-state.json").write_text("{bad")
            state = load_state(root)
            # Graceful: returns an empty state and the CLI continues.
            self.assertEqual(state.version, REVIEW_STATE_VERSION)
            self.assertEqual(state.decisions, {})


class ReviewStateSchemaTest(unittest.TestCase):
    """The on-disk shape matches the ADR 0055 schema."""

    def test_written_json_has_version_and_decisions_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = ReviewState(
                version=REVIEW_STATE_VERSION,
                decisions={"e1": Decision(
                    decision="accepted", reason="", reviewer="a", ts="t",
                    edge_snapshot={},
                )},
            )
            save_state(root, state)
            raw = json.loads((root / ".weld" / "review-state.json").read_text())
            self.assertEqual(raw["version"], REVIEW_STATE_VERSION)
            self.assertIn("decisions", raw)
            self.assertIn("e1", raw["decisions"])
            d = raw["decisions"]["e1"]
            for key in ("decision", "reason", "reviewer", "ts", "edge_snapshot"):
                self.assertIn(key, d)


if __name__ == "__main__":
    unittest.main()
