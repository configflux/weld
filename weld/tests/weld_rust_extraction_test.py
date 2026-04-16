"""Tests for Rust language support in the tree-sitter strategy.

Verifies that:
- rust.yaml query file loads correctly with expected query keys
- Mocked tree-sitter extraction produces nodes with public Rust symbols
- Extraction distinguishes public structs/enums/traits in the types list
- Imports from use statements are captured
- Files with no public items are skipped
- Node shape matches the required contract properties
"""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

class RustQueryFileLoadingTest(unittest.TestCase):
    """The bundled rust.yaml must be loadable and contain expected queries."""

    def test_load_bundled_rust_queries(self) -> None:
        from weld.strategies.tree_sitter import load_language_queries

        queries = load_language_queries("rust")
        self.assertIn("exports", queries)
        self.assertIn("classes", queries)
        self.assertIn("imports", queries)

    def test_rust_queries_are_nonempty_strings(self) -> None:
        from weld.strategies.tree_sitter import load_language_queries

        queries = load_language_queries("rust")
        for name, query_str in queries.items():
            self.assertIsInstance(
                query_str, str, f"Query {name} should be a string"
            )
            self.assertTrue(
                len(query_str.strip()) > 0,
                f"Query {name} should not be empty",
            )

    def test_rust_exports_query_targets_public_items(self) -> None:
        """The exports query should contain patterns for pub fn, pub struct,
        pub enum, pub trait, and pub type."""
        from weld.strategies.tree_sitter import load_language_queries

        queries = load_language_queries("rust")
        exports = queries["exports"]
        self.assertIn("function_item", exports)
        self.assertIn("struct_item", exports)
        self.assertIn("enum_item", exports)
        self.assertIn("trait_item", exports)
        self.assertIn("type_item", exports)
        self.assertIn("visibility_modifier", exports)

class RustExtractWithMockedTreeSitterTest(unittest.TestCase):
    """Test Rust extraction with mocked tree-sitter internals."""

    def _make_rust_tree(self, tmp: str) -> Path:
        root = Path(tmp)
        src = root / "src"
        src.mkdir()
        (src / "lib.rs").write_text(
            textwrap.dedent("""\
                use std::collections::HashMap;

                pub struct Config {
                    pub name: String,
                }

                pub enum Status {
                    Active,
                    Inactive,
                }

                pub trait Handler {
                    fn handle(&self);
                }

                pub fn process(cfg: &Config) -> Status {
                    Status::Active
                }

                fn internal_helper() {}
            """)
        )
        return root

    def test_extract_produces_nodes_with_rust_exports(self) -> None:
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = self._make_rust_tree(td)

            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     return_value={
                         "exports": ["Config", "Status", "Handler", "process"],
                         "classes": ["Config", "Status", "Handler"],
                         "imports": ["std"],
                     },
                 ):
                ctx: dict = {}
                result = tree_sitter.extract(
                    root=root,
                    source={
                        "glob": "**/*.rs",
                        "language": "rust",
                    },
                    context=ctx,
                )
            self.assertTrue(
                len(result.nodes) > 0, "Should produce at least one node"
            )
            node = list(result.nodes.values())[0]
            self.assertEqual(node["type"], "file")
            self.assertIn("exports", node["props"])
            self.assertIn("Config", node["props"]["exports"])
            self.assertIn("process", node["props"]["exports"])
            self.assertIn("line_count", node["props"])

    def test_extract_includes_types_for_structs_and_traits(self) -> None:
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = self._make_rust_tree(td)

            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     return_value={
                         "exports": ["Config", "Handler"],
                         "classes": ["Config", "Handler"],
                         "imports": [],
                     },
                 ):
                result = tree_sitter.extract(
                    root=root,
                    source={"glob": "**/*.rs", "language": "rust"},
                    context={},
                )
            node = list(result.nodes.values())[0]
            self.assertIn("types", node["props"])
            self.assertIn("Config", node["props"]["types"])
            self.assertIn("Handler", node["props"]["types"])

    def test_extract_includes_imports_from_use(self) -> None:
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = self._make_rust_tree(td)

            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     return_value={
                         "exports": ["Config"],
                         "classes": ["Config"],
                         "imports": ["std"],
                     },
                 ):
                result = tree_sitter.extract(
                    root=root,
                    source={"glob": "**/*.rs", "language": "rust"},
                    context={},
                )
            node = list(result.nodes.values())[0]
            self.assertIn("imports_from", node["props"])
            self.assertIn("std", node["props"]["imports_from"])

    def test_extract_produces_edges_with_package(self) -> None:
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = self._make_rust_tree(td)

            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     return_value={
                         "exports": ["Config"],
                         "classes": ["Config"],
                         "imports": [],
                     },
                 ):
                result = tree_sitter.extract(
                    root=root,
                    source={
                        "glob": "**/*.rs",
                        "language": "rust",
                        "package": "pkg:rust-lib",
                    },
                    context={},
                )
            self.assertTrue(
                len(result.edges) > 0, "Should produce contains edges"
            )
            edge = result.edges[0]
            self.assertEqual(edge["from"], "pkg:rust-lib")
            self.assertEqual(edge["type"], "contains")

    def test_no_exports_skips_file(self) -> None:
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "src"
            src.mkdir()
            (src / "internal.rs").write_text("fn private_only() {}\n")

            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     return_value={
                         "exports": [],
                         "classes": [],
                         "imports": [],
                     },
                 ):
                result = tree_sitter.extract(
                    root=root,
                    source={"glob": "**/*.rs", "language": "rust"},
                    context={},
                )
            self.assertEqual(result.nodes, {})

    def test_node_confidence_is_definite(self) -> None:
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = self._make_rust_tree(td)

            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     return_value={
                         "exports": ["process"],
                         "classes": [],
                         "imports": [],
                     },
                 ):
                result = tree_sitter.extract(
                    root=root,
                    source={"glob": "**/*.rs", "language": "rust"},
                    context={},
                )
            node = list(result.nodes.values())[0]
            self.assertEqual(node["props"]["confidence"], "definite")

    def test_node_source_strategy_is_tree_sitter(self) -> None:
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = self._make_rust_tree(td)

            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     return_value={
                         "exports": ["process"],
                         "classes": [],
                         "imports": [],
                     },
                 ):
                result = tree_sitter.extract(
                    root=root,
                    source={"glob": "**/*.rs", "language": "rust"},
                    context={},
                )
            node = list(result.nodes.values())[0]
            self.assertEqual(node["props"]["source_strategy"], "tree_sitter")

class RustNodeShapeTest(unittest.TestCase):
    """Rust nodes must contain all required contract properties."""

    REQUIRED_PROPS = {
        "file",
        "exports",
        "line_count",
        "source_strategy",
        "authority",
        "confidence",
        "roles",
    }

    def test_rust_node_has_required_props(self) -> None:
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "src"
            src.mkdir()
            (src / "lib.rs").write_text("pub fn main() {}\n")

            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     return_value={
                         "exports": ["main"],
                         "classes": [],
                         "imports": [],
                     },
                 ):
                result = tree_sitter.extract(
                    root=root,
                    source={"glob": "**/*.rs", "language": "rust"},
                    context={},
                )
            node = list(result.nodes.values())[0]
            for key in self.REQUIRED_PROPS:
                self.assertIn(key, node["props"], f"Missing key: {key}")

if __name__ == "__main__":
    unittest.main()
