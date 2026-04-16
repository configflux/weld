"""Tests for the tree-sitter universal extraction strategy.

Covers both the graceful-degradation path (tree-sitter not installed)
and the query-file validation logic.  The actual tree-sitter parsing
path is tested via mocking since tree-sitter is an optional dependency
not available in the Bazel sandbox.
"""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from weld.strategies._helpers import StrategyResult

class TreeSitterNotInstalledTest(unittest.TestCase):
    """When tree-sitter is not pip-installed the strategy must degrade
    gracefully: no traceback, clear install instructions in warnings."""

    def test_not_installed_returns_empty_result(self) -> None:
        from weld.strategies import tree_sitter

        # Force the not-installed path
        with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", False):
            ctx: dict = {}
            result = tree_sitter.extract(
                root=Path("/dummy"),
                source={"glob": "**/*.py", "language": "python"},
                context=ctx,
            )
        self.assertIsInstance(result, StrategyResult)
        self.assertEqual(result.nodes, {})
        self.assertEqual(result.edges, [])

    def test_not_installed_adds_warning(self) -> None:
        from weld.strategies import tree_sitter

        with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", False):
            ctx: dict = {}
            tree_sitter.extract(
                root=Path("/dummy"),
                source={"glob": "**/*.py", "language": "python"},
                context=ctx,
            )
        warnings = ctx.get("_warnings", [])
        self.assertTrue(len(warnings) > 0, "Expected at least one warning")
        self.assertIn("pip install", warnings[0])
        self.assertIn("tree-sitter", warnings[0])

    def test_not_installed_no_traceback(self) -> None:
        """Importing the module must not raise even when tree-sitter is absent."""
        from weld.strategies import tree_sitter

        with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", False):
            # Should not raise any exception
            result = tree_sitter.extract(
                root=Path("/dummy"),
                source={"glob": "**/*.py", "language": "python"},
                context={},
            )
        self.assertIsNotNone(result)

class QueryFileLoadingTest(unittest.TestCase):
    """Query YAML files in weld/languages/ must be loadable and validated."""

    def test_load_bundled_python_queries(self) -> None:
        from weld.strategies.tree_sitter import load_language_queries

        queries = load_language_queries("python")
        self.assertIn("exports", queries)
        self.assertIn("classes", queries)
        self.assertIn("imports", queries)
        # Each query should be a non-empty string
        for name, query_str in queries.items():
            self.assertIsInstance(query_str, str, f"Query {name} should be a string")
            self.assertTrue(len(query_str.strip()) > 0, f"Query {name} should not be empty")

    def test_load_bundled_typescript_queries(self) -> None:
        from weld.strategies.tree_sitter import load_language_queries

        queries = load_language_queries("typescript")
        self.assertIn("exports", queries)

    def test_load_bundled_go_queries(self) -> None:
        from weld.strategies.tree_sitter import load_language_queries

        queries = load_language_queries("go")
        self.assertIn("exports", queries)

    def test_load_bundled_rust_queries(self) -> None:
        from weld.strategies.tree_sitter import load_language_queries

        queries = load_language_queries("rust")
        self.assertIn("exports", queries)
        self.assertIn("classes", queries)
        self.assertIn("imports", queries)

    def test_load_bundled_csharp_queries(self) -> None:
        from weld.strategies.tree_sitter import load_language_queries

        queries = load_language_queries("csharp")
        for key in (
            "exports", "classes", "imports", "methods", "properties",
            "attributes", "namespaces", "calls",
        ):
            self.assertIn(key, queries)

    def test_unsupported_language_raises(self) -> None:
        from weld.strategies.tree_sitter import load_language_queries

        with self.assertRaises(FileNotFoundError) as cm:
            load_language_queries("brainfuck")
        self.assertIn("brainfuck", str(cm.exception))

    def test_malformed_query_file_raises(self) -> None:
        from weld.strategies.tree_sitter import load_language_queries

        with tempfile.TemporaryDirectory() as td:
            bad_file = Path(td) / "bad_lang.yaml"
            bad_file.write_text("this is not valid: [[[yaml: content\n")
            with mock.patch(
                "weld.strategies.tree_sitter._languages_dir",
                return_value=Path(td),
            ):
                with self.assertRaises(ValueError) as cm:
                    load_language_queries("bad_lang")
                self.assertIn("bad_lang.yaml", str(cm.exception))

class MissingLanguageFieldTest(unittest.TestCase):
    """Source entries without a 'language' key must produce a clear error."""

    def test_missing_language_warns(self) -> None:
        from weld.strategies import tree_sitter

        with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True):
            ctx: dict = {}
            result = tree_sitter.extract(
                root=Path("/dummy"),
                source={"glob": "**/*.py"},  # no 'language' key
                context=ctx,
            )
        warnings = ctx.get("_warnings", [])
        self.assertTrue(len(warnings) > 0)
        self.assertIn("language", warnings[0])
        self.assertEqual(result.nodes, {})

class ExtractWithMockedTreeSitterTest(unittest.TestCase):
    """Test the extraction logic with mocked tree-sitter internals."""

    def _make_source_tree(self, tmp: str) -> Path:
        """Create a minimal Python source tree for testing."""
        root = Path(tmp)
        src = root / "example.py"
        src.write_text(
            textwrap.dedent("""\
                class MyModel:
                    pass

                def helper():
                    pass

                def _private():
                    pass
            """)
        )
        return root

    def test_extract_produces_nodes_with_exports(self) -> None:
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = self._make_source_tree(td)

            # Mock the tree-sitter parsing to return known symbols
            mock_symbols = ["MyModel", "helper"]

            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     return_value={
                         "exports": mock_symbols,
                         "classes": ["MyModel"],
                         "imports": [],
                     },
                 ):
                ctx: dict = {}
                result = tree_sitter.extract(
                    root=root,
                    source={
                        "glob": "**/*.py",
                        "language": "python",
                    },
                    context=ctx,
                )
            self.assertTrue(len(result.nodes) > 0, "Should produce at least one node")
            node = list(result.nodes.values())[0]
            self.assertEqual(node["type"], "file")
            self.assertIn("exports", node["props"])
            self.assertIn("MyModel", node["props"]["exports"])
            self.assertIn("helper", node["props"]["exports"])
            self.assertIn("line_count", node["props"])

    def test_extract_produces_edges_with_package(self) -> None:
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = self._make_source_tree(td)

            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     return_value={
                         "exports": ["MyModel"],
                         "classes": ["MyModel"],
                         "imports": [],
                     },
                 ):
                ctx: dict = {}
                result = tree_sitter.extract(
                    root=root,
                    source={
                        "glob": "**/*.py",
                        "language": "python",
                        "package": "pkg:example",
                    },
                    context=ctx,
                )
            self.assertTrue(len(result.edges) > 0, "Should produce contains edges")
            edge = result.edges[0]
            self.assertEqual(edge["from"], "pkg:example")
            self.assertEqual(edge["type"], "contains")

    def test_no_matches_returns_empty(self) -> None:
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # Empty directory, no files match

            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True):
                result = tree_sitter.extract(
                    root=root,
                    source={"glob": "**/*.py", "language": "python"},
                    context={},
                )
            self.assertEqual(result.nodes, {})
            self.assertEqual(result.edges, [])

class TreeSitter025ApiTest(unittest.TestCase):
    """Regression tests for tree-sitter 0.25 API compatibility.

    tree-sitter 0.25 changed three things:
    1. Parser() requires a Language object, not a raw PyCapsule
    2. Query is constructed via tree_sitter.Query(language, s_expr)
    3. Captures use QueryCursor(query).matches(node) returning
       (pattern_idx, {"capture_name": [nodes]})

    _parse_file_symbols must use the new API exclusively.
    """

    def _make_mock_ts(self) -> tuple[mock.MagicMock, object, object]:
        """Build a mock tree_sitter package and return (mock, capsule, lang)."""
        fake_capsule = object()
        fake_language = object()

        mock_ts = mock.MagicMock()
        mock_ts.Language.return_value = fake_language

        fake_tree = mock.MagicMock()
        fake_tree.root_node = mock.MagicMock()
        mock_ts.Parser.return_value.parse.return_value = fake_tree

        return mock_ts, fake_capsule, fake_language

    def _run_with_mock_ts(
        self,
        mock_ts: mock.MagicMock,
        fake_capsule: object,
        queries: dict[str, str],
    ) -> dict[str, list[str]]:
        """Reload ts_mod with mock_ts injected and call _parse_file_symbols."""
        import importlib
        import sys

        from weld.strategies import tree_sitter as ts_mod

        original = sys.modules.get("tree_sitter")
        sys.modules["tree_sitter"] = mock_ts
        try:
            importlib.reload(ts_mod)
            with tempfile.TemporaryDirectory() as td:
                src = Path(td) / "example.py"
                src.write_text("x = 1\n")
                with mock.patch.object(
                    ts_mod, "_load_ts_language", return_value=fake_capsule,
                ):
                    return ts_mod._parse_file_symbols(src, "python", queries)
        finally:
            if original is not None:
                sys.modules["tree_sitter"] = original
            else:
                sys.modules.pop("tree_sitter", None)
            importlib.reload(ts_mod)

    def test_parser_receives_language_not_capsule(self) -> None:
        mock_ts, fake_capsule, fake_language = self._make_mock_ts()
        self._run_with_mock_ts(mock_ts, fake_capsule, {})

        mock_ts.Language.assert_called_once_with(fake_capsule)
        mock_ts.Parser.assert_called_once_with(fake_language)

    def test_query_uses_new_constructor(self) -> None:
        mock_ts, fake_capsule, fake_language = self._make_mock_ts()

        # Set up QueryCursor to return empty matches
        mock_ts.QueryCursor.return_value.matches.return_value = []

        self._run_with_mock_ts(mock_ts, fake_capsule, {"exports": "(function_definition)"})

        # Query must be constructed with Language + s-expression
        mock_ts.Query.assert_called_once_with(fake_language, "(function_definition)")
        # QueryCursor must be constructed with the Query
        mock_ts.QueryCursor.assert_called_once_with(mock_ts.Query.return_value)

    def test_captures_use_new_dict_format(self) -> None:
        mock_ts, fake_capsule, fake_language = self._make_mock_ts()

        # Simulate the 0.25 captures format: (pattern_idx, {"name": [node]})
        fake_node = mock.MagicMock()
        fake_node.text = b"my_function"
        mock_ts.QueryCursor.return_value.matches.return_value = [
            (0, {"name": [fake_node]}),
        ]

        result = self._run_with_mock_ts(
            mock_ts, fake_capsule, {"exports": "(function_definition)"},
        )

        self.assertEqual(result["exports"], ["my_function"])

if __name__ == "__main__":
    unittest.main()
