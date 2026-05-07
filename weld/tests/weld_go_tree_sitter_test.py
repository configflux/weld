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
                node["props"]["imports_origin"],
                {
                    '"example.com/myapi/internal"': "project",
                    '"fmt"': "stdlib",
                    '"github.com/example/external"': "external",
                },
            )


if __name__ == "__main__":
    unittest.main()
