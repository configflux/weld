"""Tests for the yaml_meta discovery strategy.

The strategy walks YAML files matching ``glob`` and emits one
``workflow:<stem>`` node per file. It does line-oriented parsing (not
true YAML) for ``name:`` and trigger keys so the strategy can run
without a YAML dependency in the discover pipeline.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld.strategies._helpers import StrategyResult
from weld.strategies.yaml_meta import extract


_HAPPY_WORKFLOW = """\
name: Build and Test
on: push
jobs:
  build:
    runs-on: ubuntu-latest
"""

_NO_NAME_WORKFLOW = """\
on: workflow_dispatch
jobs:
  noop:
    runs-on: ubuntu-latest
"""

_QUOTED_NAME_WORKFLOW = """\
name: "CI Pipeline"
on: schedule
"""


class TestYamlMetaEmptyAndMissing(unittest.TestCase):
    """Missing parent directory must yield a well-formed empty result."""

    def test_missing_workflows_dir_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = extract(root, {"glob": ".github/workflows/*.yml"}, {})
            self.assertIsInstance(result, StrategyResult)
            self.assertEqual(result.nodes, {})
            self.assertEqual(result.edges, [])

    def test_empty_directory_returns_no_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".github" / "workflows").mkdir(parents=True)
            result = extract(root, {"glob": ".github/workflows/*.yml"}, {})
            self.assertEqual(result.nodes, {})


class TestYamlMetaHappyPath(unittest.TestCase):
    """Canonical extraction populates node id, label, and triggers."""

    def test_extracts_workflow_node_with_name_and_triggers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wf = root / ".github" / "workflows"
            wf.mkdir(parents=True)
            (wf / "ci.yml").write_text(_HAPPY_WORKFLOW, encoding="utf-8")
            result = extract(root, {"glob": ".github/workflows/*.yml"}, {})
            self.assertIn("workflow:ci", result.nodes)
            node = result.nodes["workflow:ci"]
            self.assertEqual(node["type"], "workflow")
            self.assertEqual(node["label"], "Build and Test")
            props = node["props"]
            self.assertEqual(props["file"], ".github/workflows/ci.yml")
            self.assertEqual(props["source_strategy"], "yaml_meta")
            self.assertEqual(props["authority"], "canonical")
            self.assertEqual(props["confidence"], "definite")
            self.assertEqual(props["roles"], ["config"])
            triggers = props["triggers"]
            # An inline ``on: <event>`` value is captured directly as a
            # trigger string.
            self.assertIn("push", triggers)

    def test_quoted_name_value_is_unwrapped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wf = root / ".github" / "workflows"
            wf.mkdir(parents=True)
            (wf / "pipeline.yml").write_text(
                _QUOTED_NAME_WORKFLOW, encoding="utf-8"
            )
            result = extract(root, {"glob": ".github/workflows/*.yml"}, {})
            self.assertEqual(
                result.nodes["workflow:pipeline"]["label"], "CI Pipeline"
            )


class TestYamlMetaEdgeCases(unittest.TestCase):
    """Files without a ``name:`` line and exclude rules behave as documented."""

    def test_label_falls_back_to_file_stem_when_name_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wf = root / ".github" / "workflows"
            wf.mkdir(parents=True)
            (wf / "untitled.yml").write_text(
                _NO_NAME_WORKFLOW, encoding="utf-8"
            )
            result = extract(root, {"glob": ".github/workflows/*.yml"}, {})
            self.assertIn("workflow:untitled", result.nodes)
            self.assertEqual(
                result.nodes["workflow:untitled"]["label"], "untitled"
            )
            triggers = result.nodes["workflow:untitled"]["props"]["triggers"]
            self.assertIn("workflow_dispatch", triggers)

    def test_exclude_pattern_drops_matching_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wf = root / ".github" / "workflows"
            wf.mkdir(parents=True)
            (wf / "keep.yml").write_text(_HAPPY_WORKFLOW, encoding="utf-8")
            (wf / "drop.yml").write_text(_HAPPY_WORKFLOW, encoding="utf-8")
            source = {
                "glob": ".github/workflows/*.yml",
                "exclude": ["drop.yml"],
            }
            result = extract(root, source, {})
            self.assertIn("workflow:keep", result.nodes)
            self.assertNotIn("workflow:drop", result.nodes)


if __name__ == "__main__":
    unittest.main()
