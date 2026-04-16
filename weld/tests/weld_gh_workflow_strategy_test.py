"""Tests for the GitHub Actions workflow extraction strategy."""

from __future__ import annotations

import tempfile
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

class TestGhWorkflowExtract:
    """Tests for gh_workflow strategy extract()."""

    def test_extracts_workflow_node(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wf_dir = root / ".github" / "workflows"
            wf_dir.mkdir(parents=True)
            (wf_dir / "ci.yml").write_text(_SIMPLE_WORKFLOW)

            source = {"glob": ".github/workflows/*.yml"}
            result = extract(root, source, {})

            assert isinstance(result, StrategyResult)
            assert "workflow:ci" in result.nodes
            node = result.nodes["workflow:ci"]
            assert node["type"] == "workflow"
            assert node["label"] == "CI"

    def test_extracts_triggers(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wf_dir = root / ".github" / "workflows"
            wf_dir.mkdir(parents=True)
            (wf_dir / "ci.yml").write_text(_SIMPLE_WORKFLOW)

            source = {"glob": ".github/workflows/*.yml"}
            result = extract(root, source, {})

            props = result.nodes["workflow:ci"]["props"]
            assert "pull_request" in props["triggers"]
            assert "push" in props["triggers"]

    def test_extracts_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wf_dir = root / ".github" / "workflows"
            wf_dir.mkdir(parents=True)
            (wf_dir / "ci.yml").write_text(_SIMPLE_WORKFLOW)

            source = {"glob": ".github/workflows/*.yml"}
            result = extract(root, source, {})

            props = result.nodes["workflow:ci"]["props"]
            assert "build" in props["jobs"]
            assert "lint" in props["jobs"]

    def test_extracts_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wf_dir = root / ".github" / "workflows"
            wf_dir.mkdir(parents=True)
            (wf_dir / "ci.yml").write_text(_SIMPLE_WORKFLOW)

            source = {"glob": ".github/workflows/*.yml"}
            result = extract(root, source, {})

            props = result.nodes["workflow:ci"]["props"]
            assert "contents: read" in props["permissions"]

    def test_extracts_concurrency(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wf_dir = root / ".github" / "workflows"
            wf_dir.mkdir(parents=True)
            (wf_dir / "ci.yml").write_text(_SIMPLE_WORKFLOW)

            source = {"glob": ".github/workflows/*.yml"}
            result = extract(root, source, {})

            props = result.nodes["workflow:ci"]["props"]
            assert props["concurrency"] is not None

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
                assert props["source_strategy"] == "gh_workflow"
                assert props["authority"] == "canonical"
                assert props["confidence"] == "definite"
                assert isinstance(props["roles"], list)
                assert len(props["roles"]) > 0

    def test_deploy_workflow_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wf_dir = root / ".github" / "workflows"
            wf_dir.mkdir(parents=True)
            (wf_dir / "delivery_placeholder.yml").write_text(_DEPLOY_WORKFLOW)

            source = {"glob": ".github/workflows/*.yml"}
            result = extract(root, source, {})

            node = result.nodes["workflow:delivery_placeholder"]
            assert node["type"] == "workflow"
            props = node["props"]
            assert "workflow_dispatch" in props["triggers"]
            assert "plan" in props["jobs"]
            assert "publish" in props["jobs"]

    def test_multiple_workflows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wf_dir = root / ".github" / "workflows"
            wf_dir.mkdir(parents=True)
            (wf_dir / "ci.yml").write_text(_SIMPLE_WORKFLOW)
            (wf_dir / "deploy.yml").write_text(_DEPLOY_WORKFLOW)

            source = {"glob": ".github/workflows/*.yml"}
            result = extract(root, source, {})

            assert "workflow:ci" in result.nodes
            assert "workflow:deploy" in result.nodes
            assert len(result.discovered_from) == 2

    def test_exclude_pattern(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wf_dir = root / ".github" / "workflows"
            wf_dir.mkdir(parents=True)
            (wf_dir / "ci.yml").write_text(_SIMPLE_WORKFLOW)

            source = {"glob": ".github/workflows/*.yml", "exclude": ["ci.yml"]}
            result = extract(root, source, {})

            assert len(result.nodes) == 0

    def test_missing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = {"glob": ".github/workflows/*.yml"}
            result = extract(root, source, {})

            assert result.nodes == {}
            assert result.edges == []

    def test_malformed_yaml_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wf_dir = root / ".github" / "workflows"
            wf_dir.mkdir(parents=True)
            (wf_dir / "bad.yml").write_text("{{not yaml at all}}")

            source = {"glob": ".github/workflows/*.yml"}
            result = extract(root, source, {})

            # Should discover the file but produce no nodes
            assert len(result.discovered_from) == 1
            assert len(result.nodes) == 0

    def test_minimal_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wf_dir = root / ".github" / "workflows"
            wf_dir.mkdir(parents=True)
            (wf_dir / "simple.yml").write_text(_MINIMAL_WORKFLOW)

            source = {"glob": ".github/workflows/*.yml"}
            result = extract(root, source, {})

            assert "workflow:simple" in result.nodes
            props = result.nodes["workflow:simple"]["props"]
            assert props["triggers"] == ["push"]
            assert "check" in props["jobs"]

    def test_discovered_from_populated(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            wf_dir = root / ".github" / "workflows"
            wf_dir.mkdir(parents=True)
            (wf_dir / "ci.yml").write_text(_SIMPLE_WORKFLOW)

            source = {"glob": ".github/workflows/*.yml"}
            result = extract(root, source, {})

            assert ".github/workflows/ci.yml" in result.discovered_from
