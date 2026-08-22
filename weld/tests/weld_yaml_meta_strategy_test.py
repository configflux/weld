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
            self.assertIn("workflow:.github/workflows/ci", result.nodes)
            node = result.nodes["workflow:.github/workflows/ci"]
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
                result.nodes["workflow:.github/workflows/pipeline"]["label"], "CI Pipeline"
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
            self.assertIn("workflow:.github/workflows/untitled", result.nodes)
            self.assertEqual(
                result.nodes["workflow:.github/workflows/untitled"]["label"], "untitled"
            )
            triggers = result.nodes["workflow:.github/workflows/untitled"]["props"]["triggers"]
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
            self.assertIn("workflow:.github/workflows/keep", result.nodes)
            self.assertNotIn("workflow:.github/workflows/drop", result.nodes)


class TestYamlMetaInvokesEdges(unittest.TestCase):
    """``run:`` steps that invoke a repo script by path get an edge (bd lwrh).

    Mirrors ``TestToolScriptInvokes`` (``weld_tool_script_invokes_test.py``):
    the same evidence rule, reused rather than re-derived, now sourced from a
    workflow file's ``run:`` steps instead of a whole script's body.
    """

    def _invokes(self, result) -> list[dict]:
        return [e for e in result.edges if e["type"] == "invokes"]

    def test_inline_run_becomes_an_inferred_invokes_edge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wf = root / ".github" / "workflows"
            wf.mkdir(parents=True)
            (root / "tools").mkdir()
            (root / "tools" / "audit.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (wf / "ci.yml").write_text(
                "name: CI\non: push\njobs:\n  x:\n    steps:\n"
                "      - run: tools/audit.sh --dry-run\n",
                encoding="utf-8",
            )
            result = extract(root, {"glob": ".github/workflows/*.yml"}, {})
            edges = self._invokes(result)
            self.assertTrue(edges)
            self.assertEqual(
                {e["from"] for e in edges}, {"workflow:.github/workflows/ci"}
            )
            self.assertIn("tool:tools/audit", {e["to"] for e in edges})
            for edge in edges:
                self.assertEqual(edge["props"]["confidence"], "inferred")
                self.assertEqual(edge["props"]["source_strategy"], "yaml_meta")
                # ADR 0074 (bd 57lra): provenance names the workflow file
                # this edge was scanned from, never the invoked target --
                # see incremental_inbound_edge_provenance_purge_test.py for
                # the incremental-purge contract this stamp exists to keep.
                self.assertEqual(
                    edge["props"]["provenance"], {"file": ".github/workflows/ci.yml"}
                )

    def test_conditional_block_run_is_followed(self) -> None:
        # The exact shape the gap was filed against: a `run: |` block that
        # conditionally invokes a script.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wf = root / ".github" / "workflows"
            wf.mkdir(parents=True)
            (root / "tools").mkdir()
            (root / "tools" / "verify.py").write_text("", encoding="utf-8")
            (wf / "release.yml").write_text(
                "name: Release\non: push\njobs:\n  verify:\n    steps:\n"
                "      - name: Verify\n"
                "        run: |\n"
                "          if [ -f tools/verify.py ]; then\n"
                "            python tools/verify.py --strict\n"
                "          fi\n",
                encoding="utf-8",
            )
            result = extract(root, {"glob": ".github/workflows/*.yml"}, {})
            targets = {e["to"] for e in self._invokes(result)}
            self.assertIn("file:tools/verify", targets)

    def test_unresolvable_variable_path_yields_no_edge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wf = root / ".github" / "workflows"
            wf.mkdir(parents=True)
            (root / "tools").mkdir()
            (root / "tools" / "real.py").write_text("", encoding="utf-8")
            (wf / "ci.yml").write_text(
                "name: CI\non: push\njobs:\n  x:\n    steps:\n"
                '      - run: python "tools/${SCRIPT}.py"\n',
                encoding="utf-8",
            )
            result = extract(root, {"glob": ".github/workflows/*.yml"}, {})
            self.assertEqual(self._invokes(result), [])

    def test_no_run_steps_yields_no_edges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wf = root / ".github" / "workflows"
            wf.mkdir(parents=True)
            (wf / "ci.yml").write_text(_HAPPY_WORKFLOW, encoding="utf-8")
            result = extract(root, {"glob": ".github/workflows/*.yml"}, {})
            self.assertEqual(self._invokes(result), [])


if __name__ == "__main__":
    unittest.main()
