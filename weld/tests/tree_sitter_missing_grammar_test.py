"""Tests for the per-language grammar-missing detection path.

When ``tree-sitter`` is installed but a per-language grammar package
(e.g. ``tree_sitter_c_sharp``) is missing, the strategy must short-
circuit before re-raising and emit ONE structured warning naming the
missing grammar and the install command. Without this, ``wd discover``
silently produces zero nodes for that language while reporting success.

Detection is now lazy: ``_parse_file_symbols`` raises ``ImportError``
on the first file when the grammar is missing, the strategy catches
it, emits one warning to ``context["_warnings"]``, and breaks out of
the file loop. This converges on the same behaviour in normal Python
environments AND in Bazel's hermetic test sandbox (where the per-
language grammar wheel is not in runfiles).

These tests simulate the missing-grammar condition by patching
``_parse_file_symbols`` to raise ``ImportError``; the public
``grammar_available`` probe stays exercised by a separate present-
grammar test that exercises the import-based check directly.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock


class MissingGrammarPackageTest(unittest.TestCase):
    """Grammar-missing path: catch ImportError + structured warning + dedup."""

    def test_missing_csharp_grammar_warns_and_returns_empty(self) -> None:
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "x.cs").write_text("class X {}\n", encoding="utf-8")

            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     side_effect=ImportError(
                         "tree-sitter grammar for 'csharp' not installed: "
                         "pip install tree-sitter-c-sharp"
                     ),
                 ):
                ctx: dict = {}
                result = tree_sitter.extract(
                    root=root,
                    source={"glob": "**/*.cs", "language": "csharp"},
                    context=ctx,
                )

            self.assertEqual(result.nodes, {})
            self.assertEqual(result.edges, [])
            # Glob did run; the discovered_from dirs may be non-empty.

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
            (root / "a.cs").write_text("class A {}\n", encoding="utf-8")
            (root / "src").mkdir()
            (root / "src" / "b.cs").write_text("class B {}\n", encoding="utf-8")

            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     side_effect=ImportError("missing"),
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

    def test_missing_grammar_breaks_after_first_file(self) -> None:
        """Once the grammar is known missing, we must not retry every file."""
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for i in range(5):
                (root / f"f{i}.cs").write_text("class F {}\n", encoding="utf-8")

            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     side_effect=ImportError("missing"),
                 ) as parse_mock:
                ctx: dict = {}
                tree_sitter.extract(
                    root=root,
                    source={"glob": "**/*.cs", "language": "csharp"},
                    context=ctx,
                )

            # Exactly one parse attempt before we break out of the loop.
            self.assertEqual(
                parse_mock.call_count, 1,
                f"Expected single parse attempt; got: {parse_mock.call_count}",
            )

    def test_present_grammar_is_not_short_circuited(self) -> None:
        """When ``_parse_file_symbols`` returns symbols, extraction proceeds."""
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "x.py").write_text("def f(): pass\n", encoding="utf-8")

            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     return_value={"exports": ["f"], "classes": [], "imports": []},
                 ) as parse_mock:
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


class GrammarAvailableProbeTest(unittest.TestCase):
    """The exported ``grammar_available`` helper uses real import semantics."""

    def test_returns_true_for_importable_module(self) -> None:
        from weld.strategies._ts_parse import grammar_available

        # The standard library is always importable; we use a stable
        # alias to avoid coupling this unit test to optional pip pkgs.
        with mock.patch(
            "weld.strategies._ts_parse.grammar_module_name",
            return_value="json",
        ):
            self.assertTrue(grammar_available("json"))

    def test_returns_false_for_missing_module(self) -> None:
        from weld.strategies._ts_parse import grammar_available

        with mock.patch(
            "weld.strategies._ts_parse.grammar_module_name",
            return_value="weld_definitely_not_a_real_module_xyz",
        ):
            self.assertFalse(grammar_available("xyz"))


if __name__ == "__main__":
    unittest.main()
