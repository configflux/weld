"""Tests for the per-language grammar-availability guard.

When ``tree-sitter`` is installed but a per-language grammar package
(e.g. ``tree_sitter_c_sharp``) is missing, the strategy must
short-circuit before iterating files and emit ONE structured warning
naming the missing grammar and the install command. Without this,
``wd discover`` silently produces zero nodes for that language while
reporting success.

These tests exercise the probe in ``weld.strategies._ts_parse`` via the
strategy entry point so the public behaviour stays under test even if
the helper module is refactored.
"""

from __future__ import annotations

import importlib.util as importlib_util
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def _patched_find_spec(*missing_modules: str):
    """Return a fake ``find_spec`` that reports the named modules absent."""
    real_find_spec = importlib_util.find_spec
    missing = frozenset(missing_modules)

    def _fake(name: str, package: object = None):
        if name in missing:
            return None
        return real_find_spec(name, package)

    return _fake


class MissingGrammarPackageTest(unittest.TestCase):
    """Grammar-missing path: short-circuit + structured warning + dedup."""

    def test_missing_csharp_grammar_warns_and_returns_empty(self) -> None:
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "x.cs").write_text("class X {}\n", encoding="utf-8")

            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                 ) as parse_mock, \
                 mock.patch(
                     "weld.strategies._ts_parse._importlib_util.find_spec",
                     side_effect=_patched_find_spec("tree_sitter_c_sharp"),
                 ):
                ctx: dict = {}
                result = tree_sitter.extract(
                    root=root,
                    source={"glob": "**/*.cs", "language": "csharp"},
                    context=ctx,
                )

            # No file iteration: parse must not have been called.
            parse_mock.assert_not_called()
            self.assertEqual(result.nodes, {})
            self.assertEqual(result.edges, [])
            self.assertEqual(result.discovered_from, [])

            warnings = ctx.get("_warnings", [])
            self.assertEqual(
                len(warnings), 1,
                f"Expected exactly one warning; got: {warnings!r}",
            )
            msg = warnings[0]
            self.assertIn("csharp", msg)
            self.assertIn("tree-sitter-c-sharp", msg)
            self.assertIn("pip install", msg)

    def test_missing_grammar_dedupes_across_sources(self) -> None:
        """Two source entries with the same missing grammar -> one warning."""
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch(
                     "weld.strategies._ts_parse._importlib_util.find_spec",
                     side_effect=_patched_find_spec("tree_sitter_c_sharp"),
                 ):
                ctx: dict = {}
                tree_sitter.extract(
                    root=root,
                    source={"glob": "**/*.cs", "language": "csharp"},
                    context=ctx,
                )
                tree_sitter.extract(
                    root=root,
                    source={"glob": "src/**/*.cs", "language": "csharp"},
                    context=ctx,
                )

            warnings = ctx.get("_warnings", [])
            self.assertEqual(
                len(warnings), 1,
                f"Expected dedup to a single warning; got: {warnings!r}",
            )

    def test_present_grammar_is_not_short_circuited(self) -> None:
        """When the grammar IS installed the probe must not return early."""
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "x.py").write_text("def f(): pass\n", encoding="utf-8")

            real_find_spec = importlib_util.find_spec

            def _passthrough(name: str, package: object = None):
                if name == "tree_sitter_python":
                    return object()  # truthy sentinel; ``is not None``
                return real_find_spec(name, package)

            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     return_value={"exports": ["f"], "classes": [], "imports": []},
                 ) as parse_mock, \
                 mock.patch(
                     "weld.strategies._ts_parse._importlib_util.find_spec",
                     side_effect=_passthrough,
                 ):
                ctx: dict = {}
                result = tree_sitter.extract(
                    root=root,
                    source={"glob": "**/*.py", "language": "python"},
                    context=ctx,
                )

            parse_mock.assert_called()
            self.assertTrue(len(result.nodes) > 0)
            warnings = ctx.get("_warnings", [])
            grammar_warns = [w for w in warnings if "grammar for" in w]
            self.assertEqual(grammar_warns, [])


if __name__ == "__main__":
    unittest.main()
