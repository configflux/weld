"""Unit tests for the shared "does this .py file get a file anchor?" predicate.

``python_module`` decides whether a Python source file becomes a
``file:`` anchor; ``python_package`` needs the *same* answer to decide
whether a directory has any member worth parenting (issue ``ddsy``).
Before this module existed the rule lived only inside
``python_module.extract``, so any second caller had to restate it --
exactly the duplicated-skip-rule drift that ADR 0041 § Layer 3 was
written to stop.

These tests pin the predicate itself so a change to the anchor rule has
to break here first, in one place, rather than silently desynchronising
two strategies.
"""

from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path

from weld.strategies._python_anchor import (
    module_exports,
    path_yields_file_anchor,
    yields_file_anchor,
)


def _tree(src: str) -> ast.Module:
    return ast.parse(src)


class ModuleExportsTest(unittest.TestCase):
    """``module_exports`` mirrors python_module's export collection."""

    def test_classes_and_public_functions_are_exports(self) -> None:
        exports = module_exports(_tree("class A:\n    pass\n\ndef b():\n    pass\n"))
        self.assertEqual(exports, ["A", "b"])

    def test_private_functions_are_not_exports(self) -> None:
        self.assertEqual(module_exports(_tree("def _hidden():\n    pass\n")), [])

    def test_private_classes_are_still_exports(self) -> None:
        """Underscore-prefixed *classes* count -- python_module filters the
        leading underscore only for functions, and the shared predicate
        must not quietly tighten that."""
        self.assertEqual(module_exports(_tree("class _Private:\n    pass\n")), ["_Private"])

    def test_async_functions_are_exports(self) -> None:
        self.assertEqual(module_exports(_tree("async def go():\n    pass\n")), ["go"])

    def test_nested_definitions_are_not_exports(self) -> None:
        """Only ``tree.body`` counts; a function defined inside another is
        not a module-level export."""
        src = "def outer():\n    def inner():\n        pass\n"
        self.assertEqual(module_exports(_tree(src)), ["outer"])

    def test_module_with_only_assignments_has_no_exports(self) -> None:
        self.assertEqual(module_exports(_tree("X = 1\nY = 2\n")), [])


class YieldsFileAnchorTest(unittest.TestCase):
    """``yields_file_anchor`` is the negation of python_module's skip."""

    def test_exportless_init_yields_no_anchor(self) -> None:
        self.assertFalse(yields_file_anchor("__init__.py", []))

    def test_init_with_exports_yields_anchor(self) -> None:
        self.assertTrue(yields_file_anchor("__init__.py", ["Thing"]))

    def test_ordinary_module_without_exports_yields_anchor(self) -> None:
        """A constants-only module is still a real anchor -- the skip rule
        is scoped to ``__init__.py`` alone."""
        self.assertTrue(yields_file_anchor("constants.py", []))

    def test_ordinary_module_with_exports_yields_anchor(self) -> None:
        self.assertTrue(yields_file_anchor("service.py", ["Service"]))


class PathYieldsFileAnchorTest(unittest.TestCase):
    """``path_yields_file_anchor`` parses a real file and answers."""

    def test_docstring_only_init_yields_no_anchor(self) -> None:
        """The ``weld/demos/__init__.py`` shape from issue ``ddsy``: a
        real module with a real docstring but no exported surface."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "__init__.py"
            path.write_text('"""Just a docstring."""\n', encoding="utf-8")
            self.assertFalse(path_yields_file_anchor(path))

    def test_empty_init_yields_no_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "__init__.py"
            path.write_text("", encoding="utf-8")
            self.assertFalse(path_yields_file_anchor(path))

    def test_init_with_class_yields_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "__init__.py"
            path.write_text("class Facade:\n    pass\n", encoding="utf-8")
            self.assertTrue(path_yields_file_anchor(path))

    def test_module_yields_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "thing.py"
            path.write_text("X = 1\n", encoding="utf-8")
            self.assertTrue(path_yields_file_anchor(path))

    def test_syntax_error_yields_no_anchor(self) -> None:
        """python_module swallows ``SyntaxError`` and emits nothing, so the
        shared predicate must agree rather than promising an anchor that
        never arrives."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "broken.py"
            path.write_text("def (:\n", encoding="utf-8")
            self.assertFalse(path_yields_file_anchor(path))


class PredicateMatchesPythonModuleTest(unittest.TestCase):
    """The predicate must stay in lock-step with the real strategy.

    ``python_module.extract`` is the authority on which files become
    ``file:`` anchors. This test runs the strategy over a fixture that
    covers every branch of the predicate and asserts the two agree
    file-for-file, so a future edit to either side fails here.
    """

    def test_strategy_output_matches_predicate(self) -> None:
        from weld.strategies.python_module import extract

        cases = {
            "docstring_init/__init__.py": '"""Doc only."""\n',
            "empty_init/__init__.py": "",
            "exporting_init/__init__.py": "class Facade:\n    pass\n",
            "plain/module.py": "X = 1\n",
            "plain/broken.py": "def (:\n",
        }
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for rel, body in cases.items():
                target = root / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(body, encoding="utf-8")
            result = extract(root, {"glob": "**/*.py"}, {})
            anchored = {
                node["props"]["file"].replace("\\", "/")
                for node in result.nodes.values()
            }
            for rel in cases:
                expected = path_yields_file_anchor(root / rel)
                self.assertEqual(
                    rel in anchored, expected,
                    f"predicate and python_module disagree on {rel}",
                )


if __name__ == "__main__":
    unittest.main()
