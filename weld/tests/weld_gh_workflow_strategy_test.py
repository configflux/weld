"""Tests for the GitHub Actions workflow extraction strategy."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld.strategies._helpers import StrategyResult
from weld.strategies.gh_workflow import extract

_SIMPLE_WORKFLOW = """\
name: CI

on:
  pull_request:
    branches:
      - main
  push:
    branches:
      - main

permissions:
  contents: read

concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: npm install
      - run: npm test

  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: npm run lint
"""

_DEPLOY_WORKFLOW = """\
name: Delivery Placeholder

on:
  workflow_dispatch:
    inputs:
      release_label:
        description: Label used for the generated image-plan artifact.
        required: true
        type: string

concurrency:
  group: delivery-${{ inputs.release_label }}
  cancel-in-progress: false

permissions:
  contents: read
  packages: write

jobs:
  plan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: echo "plan images"

  publish:
    runs-on: ubuntu-latest
    needs: plan
    steps:
      - run: echo "publish images"
"""

_MINIMAL_WORKFLOW = """\
name: Simple
on: push
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - run: echo hello
"""

class TestGhWorkflowExtract(unittest.TestCase):
    """Tests for gh_workflow strategy extract()."""

    def test_extracts_workflow_node(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wf_dir = root / ".github" / "workflows"
            wf_dir.mkdir(parents=True)
            (wf_dir / "ci.yml").write_text(_SIMPLE_WORKFLOW)

            source = {"glob": ".github/workflows/*.yml"}
            result = extract(root, source, {})

            self.assertIsInstance(result, StrategyResult)
            self.assertIn("workflow:.github/workflows/ci", result.nodes)
            node = result.nodes["workflow:.github/workflows/ci"]
            self.assertEqual(node["type"], "workflow")
            self.assertEqual(node["label"], "CI")

    def test_extracts_triggers(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wf_dir = root / ".github" / "workflows"
            wf_dir.mkdir(parents=True)
            (wf_dir / "ci.yml").write_text(_SIMPLE_WORKFLOW)

            source = {"glob": ".github/workflows/*.yml"}
            result = extract(root, source, {})

            props = result.nodes["workflow:.github/workflows/ci"]["props"]
            self.assertIn("pull_request", props["triggers"])
            self.assertIn("push", props["triggers"])

    def test_extracts_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wf_dir = root / ".github" / "workflows"
            wf_dir.mkdir(parents=True)
            (wf_dir / "ci.yml").write_text(_SIMPLE_WORKFLOW)

            source = {"glob": ".github/workflows/*.yml"}
            result = extract(root, source, {})

            props = result.nodes["workflow:.github/workflows/ci"]["props"]
            self.assertIn("build", props["jobs"])
            self.assertIn("lint", props["jobs"])

    def test_extracts_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wf_dir = root / ".github" / "workflows"
            wf_dir.mkdir(parents=True)
            (wf_dir / "ci.yml").write_text(_SIMPLE_WORKFLOW)

            source = {"glob": ".github/workflows/*.yml"}
            result = extract(root, source, {})

            props = result.nodes["workflow:.github/workflows/ci"]["props"]
            self.assertIn("contents: read", props["permissions"])

    def test_extracts_concurrency(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wf_dir = root / ".github" / "workflows"
            wf_dir.mkdir(parents=True)
            (wf_dir / "ci.yml").write_text(_SIMPLE_WORKFLOW)

            source = {"glob": ".github/workflows/*.yml"}
            result = extract(root, source, {})

            props = result.nodes["workflow:.github/workflows/ci"]["props"]
            self.assertIsNotNone(props["concurrency"])

    def test_normalized_metadata_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wf_dir = root / ".github" / "workflows"
            wf_dir.mkdir(parents=True)
            (wf_dir / "ci.yml").write_text(_SIMPLE_WORKFLOW)

            source = {"glob": ".github/workflows/*.yml"}
            result = extract(root, source, {})

            for nid, node in result.nodes.items():
                props = node["props"]
                self.assertEqual(props["source_strategy"], "gh_workflow")
                self.assertEqual(props["authority"], "canonical")
                self.assertEqual(props["confidence"], "definite")
                self.assertIsInstance(props["roles"], list)
                self.assertGreater(len(props["roles"]), 0)

    def test_deploy_workflow_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wf_dir = root / ".github" / "workflows"
            wf_dir.mkdir(parents=True)
            (wf_dir / "delivery_placeholder.yml").write_text(_DEPLOY_WORKFLOW)

            source = {"glob": ".github/workflows/*.yml"}
            result = extract(root, source, {})

            node = result.nodes["workflow:.github/workflows/delivery_placeholder"]
            self.assertEqual(node["type"], "workflow")
            props = node["props"]
            self.assertIn("workflow_dispatch", props["triggers"])
            self.assertIn("plan", props["jobs"])
            self.assertIn("publish", props["jobs"])

    def test_multiple_workflows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wf_dir = root / ".github" / "workflows"
            wf_dir.mkdir(parents=True)
            (wf_dir / "ci.yml").write_text(_SIMPLE_WORKFLOW)
            (wf_dir / "deploy.yml").write_text(_DEPLOY_WORKFLOW)

            source = {"glob": ".github/workflows/*.yml"}
            result = extract(root, source, {})

            self.assertIn("workflow:.github/workflows/ci", result.nodes)
            self.assertIn("workflow:.github/workflows/deploy", result.nodes)
            self.assertEqual(len(result.discovered_from), 2)

    def test_exclude_pattern(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wf_dir = root / ".github" / "workflows"
            wf_dir.mkdir(parents=True)
            (wf_dir / "ci.yml").write_text(_SIMPLE_WORKFLOW)

            source = {"glob": ".github/workflows/*.yml", "exclude": ["ci.yml"]}
            result = extract(root, source, {})

            self.assertEqual(len(result.nodes), 0)

    def test_missing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = {"glob": ".github/workflows/*.yml"}
            result = extract(root, source, {})

            self.assertEqual(result.nodes, {})
            self.assertEqual(result.edges, [])

    def test_malformed_yaml_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wf_dir = root / ".github" / "workflows"
            wf_dir.mkdir(parents=True)
            (wf_dir / "bad.yml").write_text("{{not yaml at all}}")

            source = {"glob": ".github/workflows/*.yml"}
            result = extract(root, source, {})

            # Should discover the file but produce no nodes
            self.assertEqual(len(result.discovered_from), 1)
            self.assertEqual(len(result.nodes), 0)

    def test_minimal_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wf_dir = root / ".github" / "workflows"
            wf_dir.mkdir(parents=True)
            (wf_dir / "simple.yml").write_text(_MINIMAL_WORKFLOW)

            source = {"glob": ".github/workflows/*.yml"}
            result = extract(root, source, {})

            self.assertIn("workflow:.github/workflows/simple", result.nodes)
            props = result.nodes["workflow:.github/workflows/simple"]["props"]
            self.assertEqual(props["triggers"], ["push"])
            self.assertIn("check", props["jobs"])

    def test_discovered_from_populated(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wf_dir = root / ".github" / "workflows"
            wf_dir.mkdir(parents=True)
            (wf_dir / "ci.yml").write_text(_SIMPLE_WORKFLOW)

            source = {"glob": ".github/workflows/*.yml"}
            result = extract(root, source, {})

            self.assertIn(".github/workflows/ci.yml", result.discovered_from)


class TestGhWorkflowInvokesEdges(unittest.TestCase):
    """``run:`` steps that invoke a repo script by path get an edge (bd lwrh).

    Mirrors ``TestYamlMetaInvokesEdges`` -- both strategies mint the same
    ``workflow:`` node shape and share the same run:-block extractor
    (:mod:`weld.strategies._workflow_run_refs`), so both get the same
    capability.
    """

    def _invokes(self, result) -> list[dict]:
        return [e for e in result.edges if e["type"] == "invokes"]

    def test_inline_run_becomes_an_inferred_invokes_edge(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wf_dir = root / ".github" / "workflows"
            wf_dir.mkdir(parents=True)
            (root / "tools").mkdir()
            (root / "tools" / "audit.sh").write_text("#!/bin/sh\n")
            (wf_dir / "ci.yml").write_text(
                "name: CI\non: push\njobs:\n  x:\n    steps:\n"
                "      - run: tools/audit.sh --dry-run\n"
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
                self.assertEqual(edge["props"]["source_strategy"], "gh_workflow")
                # ADR 0074 (bd 57lra): provenance names the workflow file
                # this edge was scanned from, never the invoked target --
                # the same stamp yaml_meta takes, for the identical reason
                # (this strategy is not wired into this repo's own
                # discover.yaml, so the incremental-purge contract itself
                # is pinned by yaml_meta's equivalence test; this asserts
                # the two strategies stay in the same shape).
                self.assertEqual(
                    edge["props"]["provenance"], {"file": ".github/workflows/ci.yml"}
                )

    def test_conditional_block_run_is_followed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wf_dir = root / ".github" / "workflows"
            wf_dir.mkdir(parents=True)
            (root / "tools").mkdir()
            (root / "tools" / "verify.py").write_text("")
            (wf_dir / "release.yml").write_text(
                "name: Release\non: push\njobs:\n  verify:\n    steps:\n"
                "      - name: Verify\n"
                "        run: |\n"
                "          if [ -f tools/verify.py ]; then\n"
                "            python tools/verify.py --strict\n"
                "          fi\n"
            )
            result = extract(root, {"glob": ".github/workflows/*.yml"}, {})
            targets = {e["to"] for e in self._invokes(result)}
            self.assertIn("file:tools/verify", targets)

    def test_unresolvable_variable_path_yields_no_edge(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wf_dir = root / ".github" / "workflows"
            wf_dir.mkdir(parents=True)
            (root / "tools").mkdir()
            (root / "tools" / "real.py").write_text("")
            (wf_dir / "ci.yml").write_text(
                "name: CI\non: push\njobs:\n  x:\n    steps:\n"
                '      - run: python "tools/${SCRIPT}.py"\n'
            )
            result = extract(root, {"glob": ".github/workflows/*.yml"}, {})
            self.assertEqual(self._invokes(result), [])

    def test_simple_workflow_run_steps_yield_no_edges(self) -> None:
        # _SIMPLE_WORKFLOW's run: steps (npm install/test/lint) name no
        # repo-relative script -- a regression guard that new edges do not
        # appear from nowhere.
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wf_dir = root / ".github" / "workflows"
            wf_dir.mkdir(parents=True)
            (wf_dir / "ci.yml").write_text(_SIMPLE_WORKFLOW)
            result = extract(root, {"glob": ".github/workflows/*.yml"}, {})
            self.assertEqual(self._invokes(result), [])


if __name__ == "__main__":
    unittest.main()
