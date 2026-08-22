"""Tests for the Bazel build/test target extraction strategy."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld.strategies._helpers import StrategyResult
from weld.strategies.bazel import extract, _parse_build_file

class TestParseBuildFile(unittest.TestCase):
    """Unit tests for the BUILD file parser."""

    def test_extracts_py_library(self) -> None:
        text = '''\
py_library(
    name = "runtime",
    srcs = ["__init__.py", "discover.py"],
    deps = [
        "//weld/strategies",
        ":yaml",
    ],
)
'''
        targets = _parse_build_file(text)
        self.assertEqual(len(targets), 1)
        t = targets[0]
        self.assertEqual(t["rule"], "py_library")
        self.assertEqual(t["name"], "runtime")
        self.assertIn("//weld/strategies", t["deps"])
        self.assertIn(":yaml", t["deps"])

    def test_extracts_py_test(self) -> None:
        text = '''\
py_test(
    name = "contract_test",
    srcs = ["contract_test.py"],
    deps = [
        "//weld:contract",
        "//weld:runtime",
    ],
    local = True,
    tags = ["no-sandbox"],
)
'''
        targets = _parse_build_file(text)
        self.assertEqual(len(targets), 1)
        t = targets[0]
        self.assertEqual(t["rule"], "py_test")
        self.assertEqual(t["name"], "contract_test")
        self.assertIn("//weld:contract", t["deps"])

    def test_extracts_sh_test(self) -> None:
        text = '''\
sh_test(
    name = "weld_test",
    srcs = ["weld_test.sh"],
    data = [
        "weld_test_lib.sh",
        "//weld:module_entrypoint",
    ],
    local = True,
)
'''
        targets = _parse_build_file(text)
        self.assertEqual(len(targets), 1)
        t = targets[0]
        self.assertEqual(t["rule"], "sh_test")
        self.assertEqual(t["name"], "weld_test")

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
        self.assertEqual(len(targets), 2)
        names = [t["name"] for t in targets]
        self.assertIn("helpers", names)
        self.assertIn("strategies", names)

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
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0]["name"], "lib")

    def test_empty_file(self) -> None:
        targets = _parse_build_file("")
        self.assertEqual(targets, [])

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
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0]["rule"], "genrule")
        self.assertEqual(targets[0]["name"], "gen_proto")

class TestBazelExtract(unittest.TestCase):
    """Integration tests for the Bazel strategy extract() function."""

    def test_extracts_build_and_test_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pkg = root / "weld" / "tests"
            pkg.mkdir(parents=True)
            build = pkg / "BUILD.bazel"
            build.write_text('''\
py_test(
    name = "contract_test",
    srcs = ["contract_test.py"],
    deps = [
        "//weld:contract",
    ],
    local = True,
)

py_library(
    name = "helpers",
    srcs = ["_helpers.py"],
)
''')
            source = {"glob": "weld/tests/BUILD.bazel"}
            result = extract(root, source, {})

            self.assertIsInstance(result, StrategyResult)
            self.assertEqual(len(result.nodes), 2)
            self.assertEqual(len(result.discovered_from), 1)

            # Check test target
            test_nodes = {k: v for k, v in result.nodes.items()
                         if v["type"] == "test-target"}
            self.assertEqual(len(test_nodes), 1)
            test_nid = list(test_nodes.keys())[0]
            test_node = test_nodes[test_nid]
            self.assertEqual(test_node["props"]["rule"], "py_test")
            self.assertEqual(test_node["props"]["source_strategy"], "bazel")
            self.assertEqual(test_node["props"]["authority"], "canonical")
            self.assertEqual(test_node["props"]["confidence"], "definite")
            self.assertIn("test", test_node["props"]["roles"])

            # Check build target
            build_nodes = {k: v for k, v in result.nodes.items()
                          if v["type"] == "build-target"}
            self.assertEqual(len(build_nodes), 1)

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

            self.assertEqual(len(result.nodes), 2)
            self.assertEqual(len(result.discovered_from), 2)

    def test_empty_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = {"glob": "nonexistent/BUILD.bazel"}
            result = extract(root, source, {})

            self.assertEqual(result.nodes, {})
            self.assertEqual(result.edges, [])
            self.assertEqual(result.discovered_from, [])

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
                self.assertEqual(props["source_strategy"], "bazel")
                self.assertEqual(props["authority"], "canonical")
                self.assertEqual(props["confidence"], "definite")
                self.assertIsInstance(props["roles"], list)
                self.assertGreater(len(props["roles"]), 0)

    def test_external_dep_node_metadata(self) -> None:
        """``external-dep`` nodes carry the strategy's usual provenance (ADR 0121).

        Deliberately not covered by ``test_node_metadata_contract`` above,
        which asserts a non-empty ``roles`` on every node this strategy
        emits: an external dependency gets no ``roles`` at all, on purpose
        -- none of ``weld.contract.ROLE_VALUES`` honestly describes "a
        dependency this repo declares but never analyzes", and the
        project's "omit instead of guess" rule wins over stamping the
        closest wrong word.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "BUILD.bazel").write_text('''\
py_library(
    name = "root_lib",
    srcs = ["main.py"],
    deps = ["@pypi//tree_sitter_cpp"],
)
''')
            source = {"glob": "BUILD.bazel"}
            result = extract(root, source, {})

            ext_node = result.nodes["external-dep:pypi:tree_sitter_cpp"]
            self.assertEqual(ext_node["type"], "external-dep")
            self.assertEqual(ext_node["label"], "@pypi//tree_sitter_cpp")
            props = ext_node["props"]
            self.assertEqual(props["source_strategy"], "bazel")
            self.assertEqual(props["authority"], "canonical")
            self.assertEqual(props["confidence"], "definite")
            self.assertEqual(props["ecosystem"], "pypi")
            self.assertEqual(props["package"], "tree_sitter_cpp")
            self.assertEqual(props["bazel_label"], "@pypi//tree_sitter_cpp")
            self.assertNotIn("roles", props)

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
                self.assertIn("source_strategy", edge["props"])
                self.assertEqual(edge["props"]["source_strategy"], "bazel")
                self.assertIn("confidence", edge["props"])

    def test_every_edge_kind_carries_build_file_provenance(self) -> None:
        """ADR 0074: every emitted edge names the BUILD file that declared it.

        Not decoration -- the incremental purge attributes an edge to a file
        or falls back to endpoint membership, and every edge this strategy
        emits crosses out of the BUILD file into something another source
        entry owns. Unstamped, editing a declared source dropped its inbound
        ``contains`` edge for good, because the clean BUILD file never re-ran
        to re-mint it (bd cpkp). The fixture below emits all five shapes:
        ``contains`` (srcs), ``depends_on`` (deps, data, and the loaded
        ``.bzl``) and ``tests``.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pkg = root / "pkg"
            pkg.mkdir()
            (pkg / "srcs.bzl").write_text('LIB_SRCS = ["lib.py"]\n')
            (pkg / "BUILD.bazel").write_text('''\
load(":srcs.bzl", "LIB_SRCS")

py_library(
    name = "lib",
    srcs = LIB_SRCS,
)

py_test(
    name = "lib_test",
    srcs = ["lib_test.py"],
    deps = [":lib"],
    data = ["fixture.txt"],
)
''')
            result = extract(root, {"glob": "**/BUILD.bazel"}, {})

            self.assertTrue(result.edges)
            for edge in result.edges:
                self.assertEqual(
                    edge["props"].get("provenance"), {"file": "pkg/BUILD.bazel"},
                    f"edge {edge['from']} -{edge['type']}-> {edge['to']} is "
                    "unattributable to its producing BUILD file",
                )
            self.assertEqual(
                {e["type"] for e in result.edges}, {"contains", "depends_on", "tests"},
            )
            targets = {e["to"] for e in result.edges}
            self.assertIn("file:pkg/srcs", targets)  # the loaded .bzl
            # The deferred ``data`` entry, in the ADR 0111 referrer spelling a
            # ``.txt`` resolves to -- what it resolves to does not matter here,
            # only that the second pass emitted it and it carries provenance.
            self.assertIn("config:pkg_fixture_txt", targets)

    def test_bazel_label_in_props(self) -> None:
        """Targets must have a bazel_label prop for tooling lookup."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pkg = root / "weld"
            pkg.mkdir()
            (pkg / "BUILD.bazel").write_text('''\
py_library(
    name = "runtime",
    srcs = ["__init__.py"],
)
''')
            source = {"glob": "weld/BUILD.bazel"}
            result = extract(root, source, {})

            self.assertEqual(len(result.nodes), 1)
            node = list(result.nodes.values())[0]
            self.assertIn("bazel_label", node["props"])
            self.assertEqual(node["props"]["bazel_label"], "//weld:runtime")

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
            self.assertTrue(any("real" in name for name in names))
            self.assertFalse(any("shadow" in name for name in names))


if __name__ == "__main__":
    unittest.main()
