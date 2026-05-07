"""Tests for Bazel label resolution and Layer C1 edge emission (ADR 0044).

Two scopes covered:

- :mod:`weld.strategies._bazel_labels` -- pure label resolvers, one
  ``resolve_src_label`` and one ``resolve_dep_label``.
- :mod:`weld.strategies.bazel` -- the Layer C1 edge wiring on top of the
  resolvers (``contains`` for srcs, ``depends_on`` for deps,
  ``unresolved_labels_dropped`` props for visibility, sort-based
  determinism).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from weld.strategies._bazel_labels import (
    resolve_dep_label,
    resolve_src_label,
)
from weld.strategies.bazel import extract


class TestResolveSrcLabel(unittest.TestCase):
    """Unit tests for the srcs label -> file: ID resolver."""

    def test_bare_filename_resolves_against_pkg(self) -> None:
        self.assertEqual(
            resolve_src_label("foo.py", "//weld"), "file:weld/foo",
        )

    def test_bare_filename_at_root_pkg(self) -> None:
        self.assertEqual(resolve_src_label("foo.py", "//"), "file:foo")

    def test_relative_label_resolves_against_pkg(self) -> None:
        self.assertEqual(
            resolve_src_label(":bar.py", "//weld"), "file:weld/bar",
        )

    def test_absolute_label_resolves_to_other_pkg(self) -> None:
        self.assertEqual(
            resolve_src_label("//other/path:baz.py", "//weld"),
            "file:other/path/baz",
        )

    def test_absolute_label_at_root_target_pkg(self) -> None:
        self.assertEqual(
            resolve_src_label("//:top.py", "//weld"), "file:top",
        )

    def test_external_label_returns_none(self) -> None:
        self.assertIsNone(
            resolve_src_label("@external//foo:bar.py", "//weld"),
        )

    def test_malformed_label_returns_none(self) -> None:
        self.assertIsNone(resolve_src_label("", "//weld"))
        # No filename portion at all -- not a src form we can resolve.
        self.assertIsNone(resolve_src_label("//path", "//weld"))


class TestResolveDepLabel(unittest.TestCase):
    """Unit tests for the deps label -> build-target: ID resolver."""

    def test_relative_dep_resolves_against_pkg(self) -> None:
        self.assertEqual(
            resolve_dep_label(":foo", "//weld"), "build-target://weld:foo",
        )

    def test_absolute_dep_resolves_to_other_pkg(self) -> None:
        self.assertEqual(
            resolve_dep_label("//other:bar", "//weld"),
            "build-target://other:bar",
        )

    def test_absolute_dep_with_implicit_target_name(self) -> None:
        # ``//path/to`` (no ``:name``) resolves to target named after the
        # last segment, per Bazel convention.
        self.assertEqual(
            resolve_dep_label("//path/to", "//weld"),
            "build-target://path/to:to",
        )

    def test_external_dep_returns_none(self) -> None:
        self.assertIsNone(resolve_dep_label("@external//foo:bar", "//weld"))

    def test_malformed_dep_returns_none(self) -> None:
        self.assertIsNone(resolve_dep_label("", "//weld"))
        self.assertIsNone(resolve_dep_label("not-a-label", "//weld"))


class TestLayerC1EdgeEmission(unittest.TestCase):
    """Integration tests for build-target -> contains/depends_on edges."""

    def test_srcs_emit_contains_edges_to_file_nodes(self) -> None:
        """srcs entries become build-target -> contains -> file:<src> edges.

        Three label forms must resolve correctly:
        - bare ``foo.py``      -> ``file:<pkg>/foo``
        - ``:bar.py`` (relative) -> ``file:<pkg>/bar``
        - ``//other/path:baz.py`` (absolute) -> ``file:other/path/baz``
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
                    "file:other/path/baz",
                    "file:weld/bar",
                    "file:weld/foo",
                ],
            )
            for edge in contains:
                self.assertEqual(edge["from"], "build-target://weld:lib")
                self.assertEqual(edge["props"]["source_strategy"], "bazel")
                self.assertEqual(edge["props"]["confidence"], "definite")

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

    def test_external_labels_dropped_with_count_in_props(self) -> None:
        """``@external//foo:bar`` labels emit no edge but bump dropped count."""
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
            self.assertEqual(len(depends_on), 1)
            self.assertEqual(depends_on[0]["to"], "build-target://weld:local")

            node = result.nodes["build-target://weld:lib"]
            self.assertEqual(node["props"]["unresolved_labels_dropped"], 1)
            self.assertEqual(
                node["props"]["unresolved_labels"],
                ["@external//foo:bar"],
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
