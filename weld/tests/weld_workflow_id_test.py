"""Tests for :func:`weld._node_ids.workflow_id` (bd lwrh).

Split from ``weld_canonical_node_ids_test.py`` when the combined file
crossed the 400-line cap -- the same split ``weld_tool_script_invokes_test``
made from ``weld_tool_script_strategy_test`` for the same reason.
``workflow_id`` is :func:`weld._node_ids.file_id`'s rule under a different
prefix, mirroring :func:`weld._node_ids.tool_id`: a bare stem collided once
a second directory could hold a same-named workflow file, which is exactly
what widening workflow discovery past ``.github/workflows/`` to
``tools/publish_overlays/*.yml`` (the internal home for the public repo's
release workflows) creates.
"""

from __future__ import annotations

import unittest

from weld._node_ids import workflow_id


class WorkflowIdTest(unittest.TestCase):
    """Edge-case coverage for :func:`workflow_id`."""

    def test_matches_file_id_rule_under_a_different_prefix(self) -> None:
        self.assertEqual(
            workflow_id(".github/workflows/ci.yml"),
            "workflow:.github/workflows/ci",
        )

    def test_stem_collision_across_directories_is_removed(self) -> None:
        # The regression this function exists to prevent: two different
        # workflow files sharing a stem must not mint the same node.
        a = workflow_id(".github/workflows/install-test.yml")
        b = workflow_id("tools/publish_overlays/install-test.yml")
        self.assertNotEqual(a, b)
        self.assertEqual(a, "workflow:.github/workflows/install-test")
        self.assertEqual(b, "workflow:tools/publish_overlays/install-test")

    def test_deterministic(self) -> None:
        for value in (".github/workflows/ci.yml", "tools/publish_overlays/x.yml"):
            self.assertEqual(workflow_id(value), workflow_id(value))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
