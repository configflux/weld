"""Tests for the generic manifest extraction strategy (package.json, Makefile)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from cortex.strategies._helpers import StrategyResult
from cortex.strategies.manifest import extract

class TestPackageJsonExtract:
    """Tests for package.json script extraction."""

    def test_extracts_build_and_test_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pj = root / "package.json"
            pj.write_text(json.dumps({
                "name": "my-app",
                "scripts": {
                    "build": "next build",
                    "test": "vitest run",
                    "lint": "eslint .",
                    "dev": "next dev",
                    "start": "next start",
                    "prepare": "husky install",
                },
            }))

            source = {"glob": "package.json"}
            result = extract(root, source, {})

            assert isinstance(result, StrategyResult)
            assert len(result.discovered_from) == 1

            # Should extract: build, test, lint, dev, start (5 targets)
            # prepare is neither build nor test pattern
            test_targets = {k: v for k, v in result.nodes.items()
                          if v["type"] == "test-target"}
            build_targets = {k: v for k, v in result.nodes.items()
                           if v["type"] == "build-target"}
            config_nodes = {k: v for k, v in result.nodes.items()
                          if v["type"] == "config"}

            # test and lint are test-targets
            assert len(test_targets) == 2
            # build, dev, start are build-targets
            assert len(build_targets) == 3
            # One manifest config node
            assert len(config_nodes) == 1

    def test_node_metadata_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pj = root / "package.json"
            pj.write_text(json.dumps({
                "name": "app",
                "scripts": {"build": "tsc", "test": "jest"},
            }))

            source = {"glob": "package.json"}
            result = extract(root, source, {})

            for nid, node in result.nodes.items():
                props = node["props"]
                assert props["source_strategy"] == "manifest"
                assert props["authority"] == "canonical"
                assert props["confidence"] == "definite"
                assert isinstance(props["roles"], list)
                assert len(props["roles"]) > 0

    def test_edge_metadata_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pj = root / "package.json"
            pj.write_text(json.dumps({
                "name": "app",
                "scripts": {"build": "tsc"},
            }))

            source = {"glob": "package.json"}
            result = extract(root, source, {})

            for edge in result.edges:
                assert edge["props"]["source_strategy"] == "manifest"
                assert edge["props"]["confidence"] == "definite"
                assert edge["type"] == "configures"

    def test_invalid_json_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pj = root / "package.json"
            pj.write_text("{ invalid json }")

            source = {"glob": "package.json"}
            result = extract(root, source, {})

            # Should discover the file but produce no target nodes
            assert len(result.discovered_from) == 1
            assert len(result.nodes) == 0

    def test_no_scripts_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pj = root / "package.json"
            pj.write_text(json.dumps({"name": "lib", "version": "1.0.0"}))

            source = {"glob": "package.json"}
            result = extract(root, source, {})

            # Manifest config node but no targets
            config_nodes = {k: v for k, v in result.nodes.items()
                          if v["type"] == "config"}
            target_nodes = {k: v for k, v in result.nodes.items()
                          if v["type"] in ("build-target", "test-target")}
            assert len(config_nodes) == 1
            assert len(target_nodes) == 0

    def test_nested_package_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            nested = root / "apps" / "web"
            nested.mkdir(parents=True)
            pj = nested / "package.json"
            pj.write_text(json.dumps({
                "name": "@app/web",
                "scripts": {"build": "next build"},
            }))

            source = {"glob": "apps/web/package.json"}
            result = extract(root, source, {})

            assert len(result.nodes) >= 1
            # Check the manifest node has the package name
            config_nodes = [v for v in result.nodes.values()
                          if v["type"] == "config"]
            assert len(config_nodes) == 1
            assert "@app/web" in config_nodes[0]["label"]

class TestMakefileExtract:
    """Tests for Makefile target extraction."""

    def test_extracts_build_and_test_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            mk = root / "Makefile"
            mk.write_text("""\
build:
\t@echo "building"

test:
\t@echo "testing"

lint:
\t@echo "linting"

clean:
\t@echo "cleaning"
""")
            source = {"glob": "Makefile"}
            result = extract(root, source, {})

            test_targets = {k: v for k, v in result.nodes.items()
                          if v["type"] == "test-target"}
            build_targets = {k: v for k, v in result.nodes.items()
                           if v["type"] == "build-target"}

            # test, lint are test-targets
            assert len(test_targets) == 2
            # build is a build-target
            assert len(build_targets) == 1

    def test_skips_phony_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            mk = root / "Makefile"
            mk.write_text("""\
.PHONY: build test

build:
\t@echo "building"
""")
            source = {"glob": "Makefile"}
            result = extract(root, source, {})

            # .PHONY should not appear as a node
            for node in result.nodes.values():
                assert ".PHONY" not in node.get("label", "")

    def test_justfile_support(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            jf = root / "Justfile"
            jf.write_text("""\
build:
    cargo build

test:
    cargo test
""")
            source = {"glob": "Justfile"}
            result = extract(root, source, {})

            assert len(result.nodes) >= 3  # config + build + test

class TestEmptyAndMissing:
    """Tests for edge cases."""

    def test_missing_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = {"glob": "nonexistent/package.json"}
            result = extract(root, source, {})

            assert result.nodes == {}
            assert result.edges == []

    def test_empty_glob(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = {"glob": ""}
            result = extract(root, source, {})

            assert result.nodes == {}

    def test_unrecognized_file_skipped(self) -> None:
        """Strategy should skip files that are not package.json or Makefile."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "setup.py").write_text("from setuptools import setup")
            source = {"glob": "setup.py"}
            result = extract(root, source, {})

            # setup.py is not a recognized manifest — no nodes
            assert len(result.nodes) == 0

    def test_exclude_pattern(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "package.json").write_text(json.dumps({
                "name": "a", "scripts": {"build": "tsc"},
            }))
            source = {"glob": "package.json", "exclude": ["package.json"]}
            result = extract(root, source, {})

            assert len(result.nodes) == 0
