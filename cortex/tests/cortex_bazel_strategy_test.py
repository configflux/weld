"""Tests for the Bazel build/test target extraction strategy."""

from __future__ import annotations

import tempfile
from pathlib import Path

from cortex.strategies._helpers import StrategyResult
from cortex.strategies.bazel import extract, _parse_build_file

class TestParseBuildFile:
    """Unit tests for the BUILD file parser."""

    def test_extracts_py_library(self) -> None:
        text = '''\
py_library(
    name = "runtime",
    srcs = ["__init__.py", "discover.py"],
    deps = [
        "//cortex/strategies",
        ":yaml",
    ],
)
'''
        targets = _parse_build_file(text)
        assert len(targets) == 1
        t = targets[0]
        assert t["rule"] == "py_library"
        assert t["name"] == "runtime"
        assert "//cortex/strategies" in t["deps"]
        assert ":yaml" in t["deps"]

    def test_extracts_py_test(self) -> None:
        text = '''\
py_test(
    name = "contract_test",
    srcs = ["contract_test.py"],
    deps = [
        "//cortex:contract",
        "//cortex:runtime",
    ],
    local = True,
    tags = ["no-sandbox"],
)
'''
        targets = _parse_build_file(text)
        assert len(targets) == 1
        t = targets[0]
        assert t["rule"] == "py_test"
        assert t["name"] == "contract_test"
        assert "//cortex:contract" in t["deps"]

    def test_extracts_sh_test(self) -> None:
        text = '''\
sh_test(
    name = "cortex_test",
    srcs = ["cortex_test.sh"],
    data = [
        "cortex_test_lib.sh",
        "//cortex:module_entrypoint",
    ],
    local = True,
)
'''
        targets = _parse_build_file(text)
        assert len(targets) == 1
        t = targets[0]
        assert t["rule"] == "sh_test"
        assert t["name"] == "cortex_test"

    def test_extracts_multiple_targets(self) -> None:
        text = '''\
py_library(
    name = "helpers",
    srcs = ["_helpers.py"],
)

py_library(
    name = "strategies",
    srcs = ["compose.py", "dockerfile.py"],
    deps = [":helpers"],
)
'''
        targets = _parse_build_file(text)
        assert len(targets) == 2
        names = [t["name"] for t in targets]
        assert "helpers" in names
        assert "strategies" in names

    def test_ignores_unknown_rules(self) -> None:
        text = '''\
load("@rules_python//python:defs.bzl", "py_library")

some_custom_rule(
    name = "custom",
)

py_library(
    name = "lib",
    srcs = ["lib.py"],
)
'''
        targets = _parse_build_file(text)
        assert len(targets) == 1
        assert targets[0]["name"] == "lib"

    def test_empty_file(self) -> None:
        targets = _parse_build_file("")
        assert targets == []

    def test_extracts_genrule(self) -> None:
        text = '''\
genrule(
    name = "gen_proto",
    srcs = ["schema.proto"],
    outs = ["schema_pb2.py"],
    cmd = "protoc ...",
)
'''
        targets = _parse_build_file(text)
        assert len(targets) == 1
        assert targets[0]["rule"] == "genrule"
        assert targets[0]["name"] == "gen_proto"

class TestBazelExtract:
    """Integration tests for the Bazel strategy extract() function."""

    def test_extracts_build_and_test_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pkg = root / "cortex" / "tests"
            pkg.mkdir(parents=True)
            build = pkg / "BUILD.bazel"
            build.write_text('''\
py_test(
    name = "contract_test",
    srcs = ["contract_test.py"],
    deps = [
        "//cortex:contract",
    ],
    local = True,
)

py_library(
    name = "helpers",
    srcs = ["_helpers.py"],
)
''')
            source = {"glob": "cortex/tests/BUILD.bazel"}
            result = extract(root, source, {})

            assert isinstance(result, StrategyResult)
            assert len(result.nodes) == 2
            assert len(result.discovered_from) == 1

            # Check test target
            test_nodes = {k: v for k, v in result.nodes.items()
                         if v["type"] == "test-target"}
            assert len(test_nodes) == 1
            test_nid = list(test_nodes.keys())[0]
            test_node = test_nodes[test_nid]
            assert test_node["props"]["rule"] == "py_test"
            assert test_node["props"]["source_strategy"] == "bazel"
            assert test_node["props"]["authority"] == "canonical"
            assert test_node["props"]["confidence"] == "definite"
            assert "test" in test_node["props"]["roles"]

            # Check build target
            build_nodes = {k: v for k, v in result.nodes.items()
                          if v["type"] == "build-target"}
            assert len(build_nodes) == 1

    def test_recursive_glob(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            # Create nested BUILD files
            (root / "a").mkdir()
            (root / "a" / "BUILD.bazel").write_text('''\
py_library(
    name = "a_lib",
    srcs = ["a.py"],
)
''')
            (root / "b" / "c").mkdir(parents=True)
            (root / "b" / "c" / "BUILD.bazel").write_text('''\
py_test(
    name = "c_test",
    srcs = ["c_test.py"],
    deps = ["//a:a_lib"],
)
''')
            source = {"glob": "**/BUILD.bazel"}
            result = extract(root, source, {})

            assert len(result.nodes) == 2
            assert len(result.discovered_from) == 2

    def test_empty_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = {"glob": "nonexistent/BUILD.bazel"}
            result = extract(root, source, {})

            assert result.nodes == {}
            assert result.edges == []
            assert result.discovered_from == []

    def test_node_metadata_contract(self) -> None:
        """Every node must include source_strategy, authority, confidence, roles."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "BUILD.bazel").write_text('''\
py_library(
    name = "root_lib",
    srcs = ["main.py"],
)
''')
            source = {"glob": "BUILD.bazel"}
            result = extract(root, source, {})

            for nid, node in result.nodes.items():
                props = node["props"]
                assert props["source_strategy"] == "bazel"
                assert props["authority"] == "canonical"
                assert props["confidence"] == "definite"
                assert isinstance(props["roles"], list)
                assert len(props["roles"]) > 0

    def test_edge_metadata_contract(self) -> None:
        """Every edge must include source_strategy and confidence."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "BUILD.bazel").write_text('''\
py_test(
    name = "my_test",
    srcs = ["test.py"],
    deps = ["//other:lib"],
)
''')
            source = {"glob": "BUILD.bazel"}
            result = extract(root, source, {})

            for edge in result.edges:
                assert "source_strategy" in edge["props"]
                assert edge["props"]["source_strategy"] == "bazel"
                assert "confidence" in edge["props"]

    def test_bazel_label_in_props(self) -> None:
        """Targets must have a bazel_label prop for tooling lookup."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pkg = root / "cortex"
            pkg.mkdir()
            (pkg / "BUILD.bazel").write_text('''\
py_library(
    name = "runtime",
    srcs = ["__init__.py"],
)
''')
            source = {"glob": "cortex/BUILD.bazel"}
            result = extract(root, source, {})

            assert len(result.nodes) == 1
            node = list(result.nodes.values())[0]
            assert "bazel_label" in node["props"]
            assert node["props"]["bazel_label"] == "//cortex:runtime"

    def test_excludes_worktree_copies(self) -> None:
        """BUILD files inside .claude/worktrees should be filtered out."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            # Canonical BUILD
            (root / "pkg").mkdir()
            (root / "pkg" / "BUILD.bazel").write_text('''\
py_library(
    name = "real",
    srcs = ["real.py"],
)
''')
            # Worktree copy
            wt = root / ".claude" / "worktrees" / "agent-1" / "pkg"
            wt.mkdir(parents=True)
            (wt / "BUILD.bazel").write_text('''\
py_library(
    name = "shadow",
    srcs = ["shadow.py"],
)
''')
            source = {"glob": "**/BUILD.bazel"}
            result = extract(root, source, {})

            names = [n["props"].get("bazel_label", "") for n in result.nodes.values()]
            assert any("real" in name for name in names)
            assert not any("shadow" in name for name in names)
