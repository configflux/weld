"""Tests for the pure Bazel label resolvers (ADR 0044, 0121).

Covers :mod:`weld.strategies._bazel_labels`'s resolvers: one
``resolve_src_path`` / ``resolve_src_labels`` pair, one ``resolve_dep_label``
(in-repo ``deps``), and one ``resolve_external_dep_label``
(external-workspace ``deps``, ADR 0121). The edge/node wiring these
resolvers feed in :mod:`weld.strategies.bazel` is covered separately in
``weld_bazel_labels_edges_test.py`` (split out to keep both files under the
400-line cap, bd vgmu).
"""

from __future__ import annotations

import unittest

from weld.strategies._bazel_labels import (
    resolve_dep_label,
    resolve_external_dep_label,
    resolve_src_labels,
    resolve_src_path,
)


class TestResolveSrcPath(unittest.TestCase):
    """Unit tests for the srcs label -> repo-relative path resolver."""

    def test_bare_filename_resolves_against_pkg(self) -> None:
        self.assertEqual(
            resolve_src_path("foo.py", "//weld"), "weld/foo.py",
        )

    def test_bare_filename_at_root_pkg(self) -> None:
        self.assertEqual(resolve_src_path("foo.py", "//"), "foo.py")

    def test_relative_label_resolves_against_pkg(self) -> None:
        self.assertEqual(
            resolve_src_path(":bar.py", "//weld"), "weld/bar.py",
        )

    def test_absolute_label_resolves_to_other_pkg(self) -> None:
        self.assertEqual(
            resolve_src_path("//other/path:baz.py", "//weld"),
            "other/path/baz.py",
        )

    def test_absolute_label_at_root_target_pkg(self) -> None:
        self.assertEqual(
            resolve_src_path("//:top.py", "//weld"), "top.py",
        )

    def test_external_label_returns_none(self) -> None:
        self.assertIsNone(
            resolve_src_path("@external//foo:bar.py", "//weld"),
        )

    def test_malformed_label_returns_none(self) -> None:
        self.assertIsNone(resolve_src_path("", "//weld"))
        # No filename portion at all -- not a src form we can resolve.
        self.assertIsNone(resolve_src_path("//path", "//weld"))


class TestResolveSrcLabels(unittest.TestCase):
    """The srcs resolver offers every plausible spelling (ADR 0111)."""

    def test_python_src_offers_the_file_spelling(self) -> None:
        self.assertIn("file:weld/foo", resolve_src_labels("foo.py", "//weld"))

    def test_shell_src_offers_tool_not_file(self) -> None:
        """A ``.sh`` src reaches the graph as ``tool:``, never ``file:``.

        Both halves matter. Offering ``tool:`` is what recovers the edge
        ``tool_script`` minted the node for; withholding ``file:`` is what
        stops ``publish.sh``'s edge landing on an unrelated
        ``publish.py``, since ``file_id`` strips the extension and both
        spell ``file:tools/publish``.
        """
        spellings = resolve_src_labels("publish.sh", "//tools")
        self.assertIn("tool:tools/publish", spellings)
        self.assertNotIn("file:tools/publish", spellings)

    def test_extensionless_src_offers_tool(self) -> None:
        self.assertIn("tool:gradlew", resolve_src_labels("gradlew", "//"))

    def test_markdown_src_offers_doc_not_file(self) -> None:
        spellings = resolve_src_labels("mcp.md", "//docs")
        self.assertIn("doc:docs/mcp", spellings)
        self.assertNotIn("file:docs/mcp", spellings)

    def test_unresolvable_label_yields_no_spellings(self) -> None:
        """Empty, not ``[None]`` -- the caller counts it as unresolved."""
        self.assertEqual(resolve_src_labels("@ext//foo:bar.py", "//weld"), [])
        self.assertEqual(resolve_src_labels("//path", "//weld"), [])

    def test_spellings_are_order_stable(self) -> None:
        self.assertEqual(
            resolve_src_labels("foo.py", "//weld"),
            resolve_src_labels("foo.py", "//weld"),
        )


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


class TestResolveExternalDepLabel(unittest.TestCase):
    """Unit tests for the external-workspace deps label resolver (ADR 0121)."""

    def test_implicit_target_name_from_package_path(self) -> None:
        self.assertEqual(
            resolve_external_dep_label("@pypi//tree_sitter_cpp"),
            ("external-dep:pypi:tree_sitter_cpp", "pypi", "tree_sitter_cpp"),
        )

    def test_explicit_colon_name_same_id_as_implicit(self) -> None:
        """Both bazel spellings of one wheel collapse to the same node."""
        implicit = resolve_external_dep_label("@pypi//tree_sitter_cpp")
        explicit = resolve_external_dep_label(
            "@pypi//tree_sitter_cpp:tree_sitter_cpp",
        )
        self.assertEqual(implicit, explicit)

    def test_root_of_repo_colon_form(self) -> None:
        self.assertEqual(
            resolve_external_dep_label("@pypi//:tree_sitter_cpp"),
            ("external-dep:pypi:tree_sitter_cpp", "pypi", "tree_sitter_cpp"),
        )

    def test_id_folds_case_repo_and_name_stay_raw(self) -> None:
        """The node id is case-insensitive (PEP 503); the raw pair is not."""
        node_id, repo, name = resolve_external_dep_label("@PyPI//Tree_Sitter")
        self.assertEqual(node_id, "external-dep:pypi:tree_sitter")
        self.assertEqual((repo, name), ("PyPI", "Tree_Sitter"))

    def test_different_ecosystem_is_a_different_node_with_no_new_code(self) -> None:
        """The resolver is ecosystem-agnostic: the repo name IS the tag."""
        self.assertEqual(
            resolve_external_dep_label("@npm//left-pad"),
            ("external-dep:npm:left-pad", "npm", "left-pad"),
        )
        self.assertEqual(
            resolve_external_dep_label("@crates//serde"),
            ("external-dep:crates:serde", "crates", "serde"),
        )

    def test_in_repo_label_returns_none(self) -> None:
        """No leading ``@`` -- :func:`resolve_dep_label`'s job, not this one's."""
        self.assertIsNone(resolve_external_dep_label("//weld:foo"))
        self.assertIsNone(resolve_external_dep_label(":foo"))

    def test_bare_repo_with_no_package_path_returns_none(self) -> None:
        self.assertIsNone(resolve_external_dep_label("@pypi"))

    def test_bzlmod_self_reference_returns_none(self) -> None:
        """``@//...`` names the root module itself -- not an external dep."""
        self.assertIsNone(resolve_external_dep_label("@//foo:bar"))

    def test_empty_label_returns_none(self) -> None:
        self.assertIsNone(resolve_external_dep_label(""))

    def test_repo_with_no_package_or_name_returns_none(self) -> None:
        self.assertIsNone(resolve_external_dep_label("@pypi//"))


if __name__ == "__main__":
    unittest.main()
