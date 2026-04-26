"""Unit tests for the edge-weight helper used by impact / plan-change."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld._agent_graph_edge_weights import (  # noqa: E402
    INCIDENTAL_WEIGHT,
    RELATED_WEIGHT,
    SAME_NAME_LABEL,
    SAME_PURPOSE_LABEL,
    SECONDARY_THRESHOLD,
    STRONG_WEIGHT,
    aggregate_weight,
    edge_weight,
    passes_secondary_threshold,
)


class EdgeWeightTest(unittest.TestCase):
    def test_strong_edges_use_strong_weight(self) -> None:
        for edge_type in (
            "uses_skill",
            "uses_command",
            "invokes_agent",
            "handoff_to",
            "generated_from",
            "provides_tool",
            "triggers_on_event",
            "implements_workflow",
        ):
            self.assertEqual(edge_weight(edge_type), STRONG_WEIGHT)

    def test_related_edges_use_related_weight(self) -> None:
        for edge_type in (
            "applies_to_path",
            "overrides",
            "duplicates",
            "conflicts_with",
            "restricts_tool",
            "part_of_platform",
            SAME_NAME_LABEL,
            SAME_PURPOSE_LABEL,
        ):
            self.assertEqual(edge_weight(edge_type), RELATED_WEIGHT)

    def test_references_file_is_incidental(self) -> None:
        self.assertEqual(edge_weight("references_file"), INCIDENTAL_WEIGHT)

    def test_unknown_edge_type_defaults_to_incidental(self) -> None:
        self.assertEqual(edge_weight("not_a_real_edge_type"), INCIDENTAL_WEIGHT)
        self.assertEqual(edge_weight(""), INCIDENTAL_WEIGHT)

    def test_aggregate_weight_sums(self) -> None:
        self.assertEqual(aggregate_weight([]), 0.0)
        self.assertEqual(
            aggregate_weight(["uses_skill", "references_file"]),
            STRONG_WEIGHT + INCIDENTAL_WEIGHT,
        )
        self.assertEqual(
            aggregate_weight(["references_file", "references_file"]),
            INCIDENTAL_WEIGHT * 2,
        )

    def test_threshold_filters_single_incidental_only(self) -> None:
        # A single incidental edge stays under the cutoff; one strong
        # or related edge clears it; two incidental edges also clear.
        self.assertLess(aggregate_weight(["references_file"]), SECONDARY_THRESHOLD)
        self.assertGreaterEqual(
            aggregate_weight(["uses_skill"]), SECONDARY_THRESHOLD,
        )
        self.assertGreaterEqual(
            aggregate_weight([SAME_NAME_LABEL]), SECONDARY_THRESHOLD,
        )
        self.assertGreaterEqual(
            aggregate_weight(["references_file", "references_file"]),
            SECONDARY_THRESHOLD,
        )

    def test_passes_threshold_uses_aggregate_weight(self) -> None:
        # Below cutoff -> filtered when not canonical.
        self.assertFalse(passes_secondary_threshold(0.5, "manual"))
        self.assertFalse(passes_secondary_threshold(0.0, ""))
        # At or above cutoff -> kept regardless of authority status.
        self.assertTrue(passes_secondary_threshold(SECONDARY_THRESHOLD, "manual"))
        self.assertTrue(passes_secondary_threshold(STRONG_WEIGHT, "generated"))

    def test_passes_threshold_canonical_bypass(self) -> None:
        # ADR 0030 audit follow-up: canonical-authority assets clear the
        # cutoff even with sub-threshold incidental-only weight.
        self.assertTrue(passes_secondary_threshold(0.0, "canonical"))
        self.assertTrue(passes_secondary_threshold(INCIDENTAL_WEIGHT, "canonical"))
        # The bypass is exact-match on "canonical"; near-misses still
        # follow the weight rule.
        self.assertFalse(passes_secondary_threshold(0.5, "Canonical"))
        self.assertFalse(passes_secondary_threshold(0.5, "derived"))


if __name__ == "__main__":
    unittest.main()
