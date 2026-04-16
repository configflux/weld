"""Tests for AST-based TypeScript extraction via tree-sitter.

Verifies that:
- typescript_exports uses tree-sitter when available, producing richer nodes
  with confidence: definite
- typescript_exports falls back to regex with confidence: inferred when
  tree-sitter is unavailable
- AST-based extraction distinguishes function, class, interface, and enum
  exports
- Backward compatibility: the fallback regex path produces the same shape of
  nodes as before
"""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

def _mock_ast_context(ts_mod, parse_return):
    """Context manager that mocks all tree-sitter internals for the AST path.

    Mocks TREE_SITTER_AVAILABLE, _load_ts_language, _load_ts_queries, and
    _parse_ts_symbols so that the AST path runs without tree-sitter installed.
    """
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(
        mock.patch.object(ts_mod, "TREE_SITTER_AVAILABLE", True)
    )
    stack.enter_context(
        mock.patch.object(
            ts_mod, "_load_ts_language", return_value=mock.sentinel.ts_lang
        )
    )
    stack.enter_context(
        mock.patch.object(
            ts_mod,
            "_load_ts_queries",
            return_value={"exports": "(fake)", "classes": "(fake)", "imports": "(fake)"},
        )
    )
    stack.enter_context(
        mock.patch.object(
            ts_mod, "_parse_ts_symbols", return_value=parse_return
        )
    )
    return stack

class ASTExtractionWhenTreeSitterAvailableTest(unittest.TestCase):
    """When tree-sitter is available, typescript_exports should use AST
    parsing and emit confidence: definite."""

    def _make_ts_tree(self, tmp: str) -> Path:
        root = Path(tmp)
        src = root / "src"
        src.mkdir()
        (src / "utils.ts").write_text(
            textwrap.dedent("""\
                export function formatPrice(): string { return '0'; }
                export class PriceWidget {}
                export interface PriceConfig { locale: string; }
                export enum Currency { USD, EUR }
                export const VERSION = '1.0';
            """)
        )
        return root

    def test_ast_nodes_get_definite_confidence(self) -> None:
        from weld.strategies import typescript_exports

        with tempfile.TemporaryDirectory() as td:
            root = self._make_ts_tree(td)
            with _mock_ast_context(
                typescript_exports,
                {
                    "exports": [
                        "formatPrice", "PriceWidget", "PriceConfig",
                        "Currency", "VERSION",
                    ],
                    "classes": ["PriceWidget", "PriceConfig"],
                    "imports": [],
                },
            ):
                result = typescript_exports.extract(
                    root, {"glob": "src/*.ts"}, {}
                )
        self.assertTrue(result.nodes)
        for nid, node in result.nodes.items():
            self.assertEqual(
                node["props"]["confidence"],
                "definite",
                f"AST node {nid} should have definite confidence",
            )

    def test_ast_source_strategy_label(self) -> None:
        from weld.strategies import typescript_exports

        with tempfile.TemporaryDirectory() as td:
            root = self._make_ts_tree(td)
            with _mock_ast_context(
                typescript_exports,
                {"exports": ["formatPrice"], "classes": [], "imports": []},
            ):
                result = typescript_exports.extract(
                    root, {"glob": "src/*.ts"}, {}
                )
        node = list(result.nodes.values())[0]
        self.assertEqual(
            node["props"]["source_strategy"],
            "typescript_exports",
        )

    def test_ast_extracts_richer_type_info(self) -> None:
        """AST path should include types list for classes/interfaces."""
        from weld.strategies import typescript_exports

        with tempfile.TemporaryDirectory() as td:
            root = self._make_ts_tree(td)
            with _mock_ast_context(
                typescript_exports,
                {
                    "exports": ["PriceWidget", "PriceConfig"],
                    "classes": ["PriceWidget", "PriceConfig"],
                    "imports": ["./helpers"],
                },
            ):
                result = typescript_exports.extract(
                    root, {"glob": "src/*.ts"}, {}
                )
        node = list(result.nodes.values())[0]
        self.assertIn("types", node["props"])
        self.assertEqual(node["props"]["types"], ["PriceWidget", "PriceConfig"])

    def test_ast_includes_imports_from(self) -> None:
        """AST path should include imports_from list when imports exist."""
        from weld.strategies import typescript_exports

        with tempfile.TemporaryDirectory() as td:
            root = self._make_ts_tree(td)
            with _mock_ast_context(
                typescript_exports,
                {
                    "exports": ["Widget"],
                    "classes": [],
                    "imports": ["./helpers", "react"],
                },
            ):
                result = typescript_exports.extract(
                    root, {"glob": "src/*.ts"}, {}
                )
        node = list(result.nodes.values())[0]
        self.assertIn("imports_from", node["props"])
        self.assertIn("./helpers", node["props"]["imports_from"])

    def test_ast_edges_get_definite_confidence(self) -> None:
        from weld.strategies import typescript_exports

        with tempfile.TemporaryDirectory() as td:
            root = self._make_ts_tree(td)
            with _mock_ast_context(
                typescript_exports,
                {"exports": ["formatPrice"], "classes": [], "imports": []},
            ):
                result = typescript_exports.extract(
                    root,
                    {"glob": "src/*.ts", "package": "pkg:web"},
                    {},
                )
        self.assertTrue(result.edges)
        for edge in result.edges:
            self.assertEqual(edge["props"]["confidence"], "definite")

class RegexFallbackTest(unittest.TestCase):
    """When tree-sitter is NOT available, typescript_exports must fall
    back to the regex path with confidence: inferred."""

    def test_fallback_nodes_get_inferred_confidence(self) -> None:
        from weld.strategies import typescript_exports

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "src"
            src.mkdir()
            (src / "utils.ts").write_text(
                "export function formatPrice(): string { return '0'; }\n"
            )
            with mock.patch.object(
                typescript_exports, "TREE_SITTER_AVAILABLE", False
            ):
                result = typescript_exports.extract(
                    root, {"glob": "src/*.ts"}, {}
                )
        self.assertTrue(result.nodes)
        for nid, node in result.nodes.items():
            self.assertEqual(node["props"]["confidence"], "inferred")

    def test_fallback_edges_get_inferred_confidence(self) -> None:
        from weld.strategies import typescript_exports

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "src"
            src.mkdir()
            (src / "widget.ts").write_text("export class Widget {}\n")
            with mock.patch.object(
                typescript_exports, "TREE_SITTER_AVAILABLE", False
            ):
                result = typescript_exports.extract(
                    root,
                    {"glob": "src/*.ts", "package": "pkg:web"},
                    {},
                )
        self.assertTrue(result.edges)
        for edge in result.edges:
            self.assertEqual(edge["props"]["confidence"], "inferred")

    def test_fallback_source_strategy_is_typescript_exports(self) -> None:
        from weld.strategies import typescript_exports

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "src"
            src.mkdir()
            (src / "lib.ts").write_text("export const FOO = 1;\n")
            with mock.patch.object(
                typescript_exports, "TREE_SITTER_AVAILABLE", False
            ):
                result = typescript_exports.extract(
                    root, {"glob": "src/*.ts"}, {}
                )
        node = list(result.nodes.values())[0]
        self.assertEqual(
            node["props"]["source_strategy"], "typescript_exports"
        )

    def test_fallback_does_not_include_types(self) -> None:
        """Regex fallback should NOT include types or imports_from."""
        from weld.strategies import typescript_exports

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "src"
            src.mkdir()
            (src / "lib.ts").write_text("export class Widget {}\n")
            with mock.patch.object(
                typescript_exports, "TREE_SITTER_AVAILABLE", False
            ):
                result = typescript_exports.extract(
                    root, {"glob": "src/*.ts"}, {}
                )
        node = list(result.nodes.values())[0]
        self.assertNotIn("types", node["props"])
        self.assertNotIn("imports_from", node["props"])

class NodeShapeCompatibilityTest(unittest.TestCase):
    """Both AST and regex paths must produce nodes with the same required
    keys to ensure downstream consumers are not broken."""

    REQUIRED_PROPS = {
        "file",
        "exports",
        "line_count",
        "source_strategy",
        "authority",
        "confidence",
        "roles",
    }

    def test_regex_node_has_required_props(self) -> None:
        from weld.strategies import typescript_exports

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "src"
            src.mkdir()
            (src / "a.ts").write_text("export function f() {}\n")
            with mock.patch.object(
                typescript_exports, "TREE_SITTER_AVAILABLE", False
            ):
                result = typescript_exports.extract(
                    root, {"glob": "src/*.ts"}, {}
                )
        node = list(result.nodes.values())[0]
        for key in self.REQUIRED_PROPS:
            self.assertIn(key, node["props"], f"Missing key: {key}")

    def test_ast_node_has_required_props(self) -> None:
        from weld.strategies import typescript_exports

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "src"
            src.mkdir()
            (src / "a.ts").write_text("export function f() {}\n")
            with _mock_ast_context(
                typescript_exports,
                {"exports": ["f"], "classes": [], "imports": []},
            ):
                result = typescript_exports.extract(
                    root, {"glob": "src/*.ts"}, {}
                )
        node = list(result.nodes.values())[0]
        for key in self.REQUIRED_PROPS:
            self.assertIn(key, node["props"], f"Missing key: {key}")

class TreeSitterParsingFallbackTest(unittest.TestCase):
    """If tree-sitter is flagged available but actual parsing fails for a
    file, the strategy should fall back to regex for that file."""

    def test_parse_failure_falls_back_to_regex(self) -> None:
        from weld.strategies import typescript_exports

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "src"
            src.mkdir()
            (src / "ok.ts").write_text("export function hello() {}\n")

            def _failing_parse(*args, **kwargs):
                raise RuntimeError("tree-sitter crashed")

            with mock.patch.object(
                typescript_exports, "TREE_SITTER_AVAILABLE", True
            ), mock.patch.object(
                typescript_exports,
                "_load_ts_language",
                return_value=mock.sentinel.ts_lang,
            ), mock.patch.object(
                typescript_exports,
                "_load_ts_queries",
                return_value={"exports": "(fake)"},
            ), mock.patch.object(
                typescript_exports, "_parse_ts_symbols", _failing_parse
            ):
                result = typescript_exports.extract(
                    root, {"glob": "src/*.ts"}, {}
                )
        # Should still produce a node via regex fallback
        self.assertTrue(result.nodes)
        node = list(result.nodes.values())[0]
        self.assertEqual(node["props"]["confidence"], "inferred")

class EmptyExportsSkippedTest(unittest.TestCase):
    """Files with no exports should be skipped in both paths."""

    def test_no_exports_no_nodes_regex(self) -> None:
        from weld.strategies import typescript_exports

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "src"
            src.mkdir()
            (src / "internal.ts").write_text("const x = 1;\n")
            with mock.patch.object(
                typescript_exports, "TREE_SITTER_AVAILABLE", False
            ):
                result = typescript_exports.extract(
                    root, {"glob": "src/*.ts"}, {}
                )
        self.assertEqual(result.nodes, {})

    def test_no_exports_no_nodes_ast(self) -> None:
        from weld.strategies import typescript_exports

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "src"
            src.mkdir()
            (src / "internal.ts").write_text("const x = 1;\n")
            with _mock_ast_context(
                typescript_exports,
                {"exports": [], "classes": [], "imports": []},
            ):
                result = typescript_exports.extract(
                    root, {"glob": "src/*.ts"}, {}
                )
        self.assertEqual(result.nodes, {})

if __name__ == "__main__":
    unittest.main()
