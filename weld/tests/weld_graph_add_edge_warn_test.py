"""Tests for ``Graph.add_edge`` confidence warning behavior (ADR 0050).

ADR 0050 makes ``confidence`` a required field on every emitted edge,
with a one-minor-release migration window during which the contract
validator emits a structured warning instead of raising. These tests
exercise the warning path on the ``Graph.add_edge`` boundary.

The warning posture is deliberately *informational* -- the edge is
still appended -- so that downstream features and tests do not break
mid-migration. The test ensures:

1. A valid confidence value is silent.
2. A missing ``confidence`` prop emits a structured warning that names
   the offending ``source_strategy`` (or ``"<unset>"`` when no
   ``source_strategy`` is present) and the edge type, so the missing-
   label set is easy to attribute from a single discovery run.
3. An invalid confidence value also warns.
4. Post-warning, the edge is appended (warnings are not blocking).
"""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

# Ensure weld package is importable from the repo root
_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.graph import Graph  # noqa: E402


class _GraphHarness:
    """Construct a temporary :class:`Graph` plus two reachable nodes."""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        (root / ".weld").mkdir()
        self.graph = Graph(root)
        self.graph.load()
        self.graph.add_node("a", "service", "A", {})
        self.graph.add_node("b", "service", "B", {})

    def cleanup(self) -> None:
        self._tmp.cleanup()


class AddEdgeConfidenceWarnTest(unittest.TestCase):

    def setUp(self) -> None:
        self.harness = _GraphHarness()
        self.addCleanup(self.harness.cleanup)

    def _add_edge_capturing_stderr(self, props: dict) -> str:
        buf = io.StringIO()
        with redirect_stderr(buf):
            self.harness.graph.add_edge("a", "b", "depends_on", props)
        return buf.getvalue()

    def test_valid_confidence_does_not_warn(self) -> None:
        for value in ("definite", "inferred", "speculative"):
            with self.subTest(confidence=value):
                stderr = self._add_edge_capturing_stderr(
                    {"source_strategy": "tree_sitter", "confidence": value},
                )
                self.assertEqual(
                    stderr, "",
                    f"valid confidence={value!r} must not warn; got: {stderr!r}",
                )

    def test_missing_confidence_warns_with_strategy_and_type(self) -> None:
        stderr = self._add_edge_capturing_stderr(
            {"source_strategy": "tree_sitter"},
        )
        self.assertIn("[weld] warning", stderr)
        self.assertIn("missing confidence", stderr)
        self.assertIn("tree_sitter", stderr)
        # Edge type is included so a single discovery run can produce an
        # attributable list of every (strategy, edge_type) pair that
        # still needs migration.
        self.assertIn("depends_on", stderr)

    def test_missing_confidence_without_strategy_uses_unset_marker(self) -> None:
        stderr = self._add_edge_capturing_stderr({})
        self.assertIn("[weld] warning", stderr)
        # Per ADR 0050: the warning must be attributable. When there is
        # no source_strategy at all, the warning still has to be parseable
        # so the operator can find the producer in the codebase.
        self.assertIn("<unset>", stderr)

    def test_invalid_confidence_value_warns(self) -> None:
        stderr = self._add_edge_capturing_stderr(
            {"source_strategy": "tree_sitter", "confidence": "maybe"},
        )
        self.assertIn("[weld] warning", stderr)
        self.assertIn("invalid confidence", stderr)
        self.assertIn("'maybe'", stderr)

    def test_warning_does_not_block_edge_append(self) -> None:
        # The migration-window posture is informational. The edge must
        # still be appended to the graph; only after the window does the
        # validator hard-raise.
        self._add_edge_capturing_stderr({"source_strategy": "tree_sitter"})
        edges = self.harness.graph.dump()["edges"]
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["from"], "a")

    def test_duplicate_edge_warns_only_once(self) -> None:
        # Graph.add_edge dedupes exact duplicates; the warning path must
        # not stutter when a duplicate is presented because some
        # discovery passes call add_edge idempotently.
        first = self._add_edge_capturing_stderr({"source_strategy": "x"})
        second = self._add_edge_capturing_stderr({"source_strategy": "x"})
        self.assertIn("[weld] warning", first)
        # Second call sees a duplicate edge and should not append it,
        # therefore should not re-emit the warning.
        self.assertEqual(second, "")
        self.assertEqual(len(self.harness.graph.dump()["edges"]), 1)


if __name__ == "__main__":
    unittest.main()
