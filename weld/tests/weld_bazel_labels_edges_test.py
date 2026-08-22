"""Integration tests for Layer C1 edge emission (ADR 0044, 0121).

Split from ``weld_bazel_labels_test.py`` (bd vgmu) to keep both files under
the 400-line cap: that file covers the pure label resolvers in
:mod:`weld.strategies._bazel_labels`; this one covers the edge/node wiring
those resolvers feed in :mod:`weld.strategies.bazel` (``contains`` for
srcs, ``depends_on`` for in-repo and external deps, ``tests`` for
inferred test-subject edges, ``unresolved_labels_dropped`` props, and
sort-based determinism).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from weld.strategies.bazel import extract


class TestLayerC1EdgeEmission(unittest.TestCase):
    """Integration tests for build-target -> contains/depends_on edges."""

    def test_srcs_emit_contains_edges_to_file_nodes(self) -> None:
        """srcs entries become build-target -> contains -> <src> edges.

        Three label forms must resolve correctly:
        - bare ``foo.py``      -> ``file:<pkg>/foo``
        - ``:bar.py`` (relative) -> ``file:<pkg>/bar``
        - ``//other/path:baz.py`` (absolute) -> ``file:other/path/baz``

        Each entry also carries its ``config:`` spelling, because the
        strategy that minted the src's node chose its ID class and this one
        cannot know which (ADR 0111). The unresolved spellings are dropped
        by the post-processor's dangling-edge sweep, which does not run
        inside a bare ``extract``.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pkg = root / "weld"
            pkg.mkdir()
            (pkg / "BUILD.bazel").write_text('''\
py_library(
    name = "lib",
    srcs = [
        "foo.py",
        ":bar.py",
        "//other/path:baz.py",
    ],
)
''')
            source = {"glob": "weld/BUILD.bazel"}
            result = extract(root, source, {})

            contains = [e for e in result.edges if e["type"] == "contains"]
            tos = sorted(e["to"] for e in contains)
            self.assertEqual(
                tos,
                [
                    "config:other_path_baz_py",
                    "config:weld_bar_py",
                    "config:weld_foo_py",
                    "file:other/path/baz",
                    "file:weld/bar",
                    "file:weld/foo",
                ],
            )
            for edge in contains:
                self.assertEqual(edge["from"], "build-target://weld:lib")
                self.assertEqual(edge["props"]["source_strategy"], "bazel")
                self.assertEqual(edge["props"]["confidence"], "definite")

    def test_shell_src_reaches_the_tool_node_the_script_minted(self) -> None:
        """The regression bd i7ny names: a shell src had no first hop.

        ``tool_script`` mints ``tool:tools/publish`` for ``publish.sh``;
        the srcs resolver spelled ``file:tools/publish``, which matched
        nothing, so the dangling sweep removed the edge and ``wd impact
        tools/publish.sh`` could not reach the target that ships it. The
        assertion is on the ``tool:`` spelling being offered *and* the
        ``file:`` one being withheld -- offering both would trade a missing
        edge for a wrong one.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pkg = root / "tools"
            pkg.mkdir()
            (pkg / "BUILD.bazel").write_text('''\
sh_binary(
    name = "publish",
    srcs = ["publish.sh"],
)
''')
            result = extract(root, {"glob": "tools/BUILD.bazel"}, {})
            tos = {e["to"] for e in result.edges if e["type"] == "contains"}
            self.assertIn("tool:tools/publish", tos)
            self.assertNotIn("file:tools/publish", tos)

    def test_deps_emit_depends_on_edges_to_build_targets(self) -> None:
        """deps entries become build-target -> depends_on -> build-target edges.

        Two label forms must resolve to the existing build-target ID
        convention ``build-target://<pkg>:<name>``:
        - ``:foo`` (relative)       -> ``build-target://<pkg>:foo``
        - ``//other:bar`` (absolute) -> ``build-target://other:bar``
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pkg = root / "weld"
            pkg.mkdir()
            (pkg / "BUILD.bazel").write_text('''\
py_library(
    name = "lib",
    srcs = ["lib.py"],
    deps = [
        ":foo",
        "//other:bar",
    ],
)
''')
            source = {"glob": "weld/BUILD.bazel"}
            result = extract(root, source, {})

            depends_on = [
                e for e in result.edges if e["type"] == "depends_on"
            ]
            tos = sorted(e["to"] for e in depends_on)
            self.assertEqual(
                tos,
                [
                    "build-target://other:bar",
                    "build-target://weld:foo",
                ],
            )

    def test_external_labels_resolve_instead_of_dropping(self) -> None:
        """``@external//foo:bar`` mints an external-dep node, not a drop (ADR 0121).

        Superseded assertion from pre-ADR-0121: this exact label used to be
        the "dropped, counted, no edge" example. It is now the "resolved"
        example instead -- ``test_genuinely_malformed_label_still_dropped``
        below keeps coverage of the still-dropped path with a label neither
        resolver can place.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pkg = root / "weld"
            pkg.mkdir()
            (pkg / "BUILD.bazel").write_text('''\
py_library(
    name = "lib",
    srcs = ["lib.py"],
    deps = [
        "@external//foo:bar",
        ":local",
    ],
)
''')
            source = {"glob": "weld/BUILD.bazel"}
            result = extract(root, source, {})

            depends_on = [
                e for e in result.edges if e["type"] == "depends_on"
            ]
            tos = sorted(e["to"] for e in depends_on)
            self.assertEqual(
                tos,
                ["build-target://weld:local", "external-dep:external:bar"],
            )

            node = result.nodes["build-target://weld:lib"]
            self.assertEqual(node["props"]["unresolved_labels_dropped"], 0)
            self.assertEqual(node["props"]["unresolved_labels"], [])

            ext_node = result.nodes["external-dep:external:bar"]
            self.assertEqual(ext_node["type"], "external-dep")
            self.assertEqual(ext_node["props"]["ecosystem"], "external")
            self.assertEqual(ext_node["props"]["package"], "bar")

    def test_genuinely_malformed_label_still_dropped(self) -> None:
        """Neither resolver can place this -- still dropped, still counted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pkg = root / "weld"
            pkg.mkdir()
            (pkg / "BUILD.bazel").write_text('''\
py_library(
    name = "lib",
    srcs = ["lib.py"],
    deps = [
        "nonsense",
        ":local",
    ],
)
''')
            source = {"glob": "weld/BUILD.bazel"}
            result = extract(root, source, {})

            depends_on = [
                e for e in result.edges if e["type"] == "depends_on"
            ]
            self.assertEqual(len(depends_on), 1)
            self.assertEqual(depends_on[0]["to"], "build-target://weld:local")

            node = result.nodes["build-target://weld:lib"]
            self.assertEqual(node["props"]["unresolved_labels_dropped"], 1)
            self.assertEqual(node["props"]["unresolved_labels"], ["nonsense"])

    def test_external_dep_edge_carries_no_tests_edge(self) -> None:
        """A test-target's external dep gets ``depends_on``, never ``tests``.

        Only an in-repo dep represents "the library under test" -- a
        grammar wheel is a precondition, not the subject under test (ADR
        0121). ``py_test``\\ s emit an inferred ``tests`` edge for every
        in-repo dep; this asserts the external one is excluded from that.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pkg = root / "tools"
            pkg.mkdir()
            (pkg / "BUILD.bazel").write_text('''\
py_test(
    name = "gate_test",
    srcs = ["gate_test.py"],
    deps = [
        ":gate_lib",
        "@pypi//tree_sitter",
        "@pypi//tree_sitter_cpp",
    ],
)

py_library(
    name = "gate_lib",
    srcs = ["gate_lib.py"],
)
''')
            result = extract(root, {"glob": "tools/BUILD.bazel"}, {})

            depends_on_tos = sorted(
                e["to"] for e in result.edges if e["type"] == "depends_on"
                and e["from"] == "test-target://tools:gate_test"
            )
            self.assertEqual(
                depends_on_tos,
                [
                    "build-target://tools:gate_lib",
                    "external-dep:pypi:tree_sitter",
                    "external-dep:pypi:tree_sitter_cpp",
                ],
            )

            tests_tos = sorted(
                e["to"] for e in result.edges if e["type"] == "tests"
                and e["from"] == "test-target://tools:gate_test"
            )
            self.assertEqual(tests_tos, ["build-target://tools:gate_lib"])

            for ext_id in (
                "external-dep:pypi:tree_sitter",
                "external-dep:pypi:tree_sitter_cpp",
            ):
                self.assertEqual(result.nodes[ext_id]["type"], "external-dep")

    def test_both_bazel_spellings_collapse_to_one_node(self) -> None:
        """``@pypi//x`` and ``@pypi//x:x`` from two targets share one node."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pkg = root / "weld"
            pkg.mkdir()
            (pkg / "BUILD.bazel").write_text('''\
py_library(
    name = "a",
    srcs = ["a.py"],
    deps = ["@pypi//tree_sitter_cpp"],
)

py_library(
    name = "b",
    srcs = ["b.py"],
    deps = ["@pypi//tree_sitter_cpp:tree_sitter_cpp"],
)
''')
            result = extract(root, {"glob": "weld/BUILD.bazel"}, {})

            ext_nodes = {
                nid for nid, n in result.nodes.items()
                if n["type"] == "external-dep"
            }
            self.assertEqual(ext_nodes, {"external-dep:pypi:tree_sitter_cpp"})

            depends_on = {
                (e["from"], e["to"])
                for e in result.edges if e["type"] == "depends_on"
            }
            self.assertIn(
                ("build-target://weld:a", "external-dep:pypi:tree_sitter_cpp"),
                depends_on,
            )
            self.assertIn(
                ("build-target://weld:b", "external-dep:pypi:tree_sitter_cpp"),
                depends_on,
            )

    def test_emit_is_deterministic_double_run_byte_identical(self) -> None:
        """Running extract twice yields byte-identical edges (sorted)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pkg = root / "weld"
            pkg.mkdir()
            (pkg / "BUILD.bazel").write_text('''\
py_library(
    name = "lib",
    srcs = ["c.py", "a.py", "b.py"],
    deps = ["//z:z", ":a", "//m:m"],
)
''')
            source = {"glob": "weld/BUILD.bazel"}
            r1 = extract(root, source, {})
            r2 = extract(root, source, {})
            j1 = json.dumps(
                {"nodes": r1.nodes, "edges": r1.edges},
                sort_keys=True,
            )
            j2 = json.dumps(
                {"nodes": r2.nodes, "edges": r2.edges},
                sort_keys=True,
            )
            self.assertEqual(j1, j2)
            # Order check: contains edges must be sorted by 'to'.
            contains = [e for e in r1.edges if e["type"] == "contains"]
            tos = [e["to"] for e in contains]
            self.assertEqual(tos, sorted(tos))


if __name__ == "__main__":
    unittest.main()
