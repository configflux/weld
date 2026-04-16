"""Tests for C# language support in the tree-sitter strategy."""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock


class CSharpTreeSitterSupportTest(unittest.TestCase):
    """C# support adds metadata and using dependency edges."""

    def _make_csharp_tree(self, tmp: str) -> Path:
        root = Path(tmp)
        src = root / "src"
        src.mkdir()
        (src / "OrdersController.cs").write_text(
            textwrap.dedent("""\
                using System.Threading.Tasks;
                using Microsoft.AspNetCore.Mvc;

                namespace Sample.Api.Controllers;

                [ApiController]
                public class OrdersController {
                    [HttpGet("{id}")]
                    public Task<OrderDto> GetAsync(int id) =>
                        Task.FromResult(new OrderDto(id));
                    private string Helper => "ok";
                }
            """)
        )
        return root

    def test_csharp_extract_adds_metadata_and_dependency_edges(self) -> None:
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = self._make_csharp_tree(td)
            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     return_value={
                         "exports": ["OrdersController", "GetAsync", "Helper"],
                         "classes": ["OrdersController"],
                         "imports": [
                             "System.Threading.Tasks",
                             "Microsoft.AspNetCore.Mvc",
                         ],
                         "methods": ["GetAsync"],
                         "properties": ["Helper"],
                         "attributes": ["ApiController", "HttpGet"],
                         "namespaces": ["Sample.Api.Controllers"],
                     },
                 ):
                result = tree_sitter.extract(
                    root,
                    {"glob": "**/*.cs", "language": "csharp"},
                    {},
                )
        file_node = next(n for n in result.nodes.values() if n["type"] == "file")
        props = file_node["props"]
        self.assertEqual(props["types"], ["OrdersController"])
        self.assertIn("ApiController", props["attributes"])
        self.assertEqual(props["method_visibility"]["GetAsync"], ["public"])
        self.assertEqual(props["property_visibility"]["Helper"], ["private"])
        deps = [e for e in result.edges if e["type"] == "depends_on"]
        self.assertEqual(len(deps), 2)
        self.assertIn("package:csharp:Microsoft.AspNetCore.Mvc", result.nodes)

    def test_csharp_wrapper_sets_language_and_strategy_label(self) -> None:
        from weld.strategies import csharp, tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = self._make_csharp_tree(td)
            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     return_value={
                         "exports": ["OrdersController"],
                         "classes": ["OrdersController"],
                         "imports": [],
                     },
                 ):
                result = csharp.extract(root, {"glob": "**/*.cs"}, {})
        node = next(n for n in result.nodes.values() if n["type"] == "file")
        self.assertEqual(node["props"]["source_strategy"], "csharp")

    def test_csharp_grammar_aliases_match_pypi_package(self) -> None:
        from weld.strategies._ts_parse import (
            grammar_module_name,
            grammar_package_name,
        )

        self.assertEqual(grammar_module_name("csharp"), "tree_sitter_c_sharp")
        self.assertEqual(grammar_package_name("csharp"), "tree-sitter-c-sharp")

    def test_init_detect_maps_cs_extension(self) -> None:
        from weld.init_detect import EXT_TO_LANG

        self.assertEqual(EXT_TO_LANG.get(".cs"), "csharp")


if __name__ == "__main__":
    unittest.main()
