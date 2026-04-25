"""Tests for TSX grammar dispatch in the typescript_exports strategy.

These tests cover the change that lets ``.tsx`` files be parsed with the
TSX tree-sitter grammar (``language_tsx()``) while ``.ts`` / ``.cts`` /
``.mts`` files keep using ``language_typescript()``. Before the fix,
``_load_ts_language`` was argument-less and always returned the plain-TS
grammar, which caused JSX to silently fall through to the regex path.

The AST path is exercised by mocking ``_load_ts_language``,
``_load_ts_queries``, and ``_parse_ts_symbols`` -- tree-sitter itself is
not installed in the Bazel sandbox (see ``weld_cpp_treesitter_test`` for
the established pattern).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock


def _fake_loader_recorder() -> tuple[list[str], "callable"]:
    """Return ``(calls, loader)`` where ``loader`` records each variant."""
    calls: list[str] = []

    def loader(variant: str = "typescript") -> object:
        calls.append(variant)
        return mock.sentinel.ts_lang

    return calls, loader


def _patched_ast(module, loader, parse_return):
    """Context manager that mocks the AST pipeline with a custom loader."""
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(mock.patch.object(module, "TREE_SITTER_AVAILABLE", True))
    stack.enter_context(mock.patch.object(module, "_load_ts_language", loader))
    stack.enter_context(
        mock.patch.object(
            module, "_load_ts_queries", return_value={"exports": "(fake)"}
        )
    )
    stack.enter_context(
        mock.patch.object(module, "_parse_ts_symbols", return_value=parse_return)
    )
    return stack


class TsxVariantSelectorTest(unittest.TestCase):
    """``_ts_variant_for`` maps paths to the correct grammar key."""

    def test_tsx_suffix_selects_tsx(self) -> None:
        from weld.strategies import typescript_exports

        self.assertEqual(
            typescript_exports._ts_variant_for(Path("Button.tsx")), "tsx"
        )

    def test_ts_suffix_selects_typescript(self) -> None:
        from weld.strategies import typescript_exports

        self.assertEqual(
            typescript_exports._ts_variant_for(Path("utils.ts")), "typescript"
        )

    def test_uppercase_tsx_is_normalised(self) -> None:
        from weld.strategies import typescript_exports

        self.assertEqual(
            typescript_exports._ts_variant_for(Path("Widget.TSX")), "tsx"
        )


class TsxVariantDispatchTest(unittest.TestCase):
    """``extract`` loads the matching grammar for each file's extension."""

    def test_extract_loads_tsx_grammar_for_tsx_files(self) -> None:
        from weld.strategies import typescript_exports

        calls, loader = _fake_loader_recorder()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "src"
            src.mkdir()
            (src / "Button.tsx").write_text(
                "export const Button = () => null;\n"
            )
            with _patched_ast(
                typescript_exports,
                loader,
                {"exports": ["Button"], "classes": [], "imports": []},
            ):
                typescript_exports.extract(root, {"glob": "src/*.tsx"}, {})
        self.assertEqual(
            calls,
            ["tsx"],
            f"Expected TSX grammar dispatch, got {calls!r}",
        )

    def test_extract_loads_ts_grammar_for_ts_files(self) -> None:
        from weld.strategies import typescript_exports

        calls, loader = _fake_loader_recorder()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "src"
            src.mkdir()
            (src / "utils.ts").write_text("export function ping(): void {}\n")
            with _patched_ast(
                typescript_exports,
                loader,
                {"exports": ["ping"], "classes": [], "imports": []},
            ):
                typescript_exports.extract(root, {"glob": "src/*.ts"}, {})
        self.assertEqual(calls, ["typescript"])

    def test_extract_caches_grammar_per_variant(self) -> None:
        """Two ``.tsx`` files and one ``.ts`` file load each grammar once."""
        from weld.strategies import typescript_exports

        calls, loader = _fake_loader_recorder()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "src"
            src.mkdir()
            (src / "A.tsx").write_text("export const A = 1;\n")
            (src / "B.tsx").write_text("export const B = 2;\n")
            (src / "helpers.ts").write_text("export const H = 3;\n")
            with _patched_ast(
                typescript_exports,
                loader,
                {"exports": ["X"], "classes": [], "imports": []},
            ):
                typescript_exports.extract(
                    root, {"glob": "src/*.{ts,tsx}"}, {}
                )
        # Each variant loaded exactly once despite multiple matching files.
        self.assertEqual(sorted(calls), ["tsx", "typescript"])


if __name__ == "__main__":
    unittest.main()
