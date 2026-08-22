"""Tests for Go tree-sitter import-origin enrichment."""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from weld.strategies import _go_tree_sitter


class GoTreeSitterImportOriginTest(unittest.TestCase):
    def test_load_module_path_reads_root_go_mod(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "go.mod").write_text(
                "module example.com/myapi\n\ngo 1.22\n",
                encoding="utf-8",
            )

            self.assertEqual(
                _go_tree_sitter.load_module_path(root),
                "example.com/myapi",
            )

    def test_strip_import_quotes_removes_surrounding_quotes(self) -> None:
        """bd bt5m: the raw tree-sitter capture keeps its source quotes.

        ``interpreted_string_literal`` (the node ``weld/languages/go.yaml``
        captures for ``imports``) yields the token text verbatim, quotes
        included. ``props.imports_from`` must not: every other Tier-1
        language's value is already a clean path/specifier string, and
        an exact-string consumer like ``package_import_resolver`` can
        only match against a clean string.
        """
        self.assertEqual(
            _go_tree_sitter.strip_import_quotes(
                ['"fmt"', '"github.com/spf13/cobra"'],
            ),
            ["fmt", "github.com/spf13/cobra"],
        )

    def test_strip_import_quotes_is_idempotent_on_clean_input(self) -> None:
        """Already-unquoted entries pass through unchanged (order kept)."""
        self.assertEqual(
            _go_tree_sitter.strip_import_quotes(["fmt", "os"]),
            ["fmt", "os"],
        )

    def test_import_origin_map_classifies_raw_tree_sitter_imports(self) -> None:
        imports = [
            '"fmt"',
            '"example.com/myapi/internal"',
            '"github.com/example/external"',
        ]

        self.assertEqual(
            _go_tree_sitter.import_origin_map(imports, "example.com/myapi"),
            {
                '"example.com/myapi/internal"': "project",
                '"fmt"': "stdlib",
                '"github.com/example/external"': "external",
            },
        )

    def test_extract_adds_import_origin_map_to_go_file_node(self) -> None:
        """``imports_from`` and ``imports_origin`` both use clean paths.

        ``_parse_file_symbols`` is mocked to return the raw quoted
        tree-sitter capture text (what the ``interpreted_string_literal``
        node actually yields) so this exercises the real quote-stripping
        step in :mod:`weld.strategies.tree_sitter`, not a pre-cleaned
        fixture. Before bd bt5m, ``imports_from`` kept the quotes
        (the one Tier-1 language whose value disagreed with every
        sibling's clean-path shape, which silently defeated any
        exact-string consumer such as
        :mod:`weld.cross_repo.package_import_resolver`) while
        ``imports_origin``'s keys were quoted too; both are asserted
        unquoted here so a regression that reintroduces the quotes on
        either prop fails this test.
        """
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "go.mod").write_text(
                "module example.com/myapi\n\ngo 1.22\n",
                encoding="utf-8",
            )
            (root / "main.go").write_text(
                textwrap.dedent(
                    """\
                    package main

                    func main() {}
                    """
                ),
                encoding="utf-8",
            )

            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     return_value={
                         "exports": ["main"],
                         "classes": [],
                         "imports": [
                             '"fmt"',
                             '"example.com/myapi/internal"',
                             '"github.com/example/external"',
                         ],
                     },
                 ):
                result = tree_sitter.extract(
                    root=root,
                    source={"glob": "**/*.go", "language": "go"},
                    context={},
                )

            node = result.nodes["file:main"]
            self.assertEqual(
                node["props"]["imports_from"],
                ["fmt", "example.com/myapi/internal", "github.com/example/external"],
            )
            self.assertEqual(
                node["props"]["imports_origin"],
                {
                    "example.com/myapi/internal": "project",
                    "fmt": "stdlib",
                    "github.com/example/external": "external",
                },
            )


if __name__ == "__main__":
    unittest.main()
