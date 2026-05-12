"""Integration tests: ``wd discover`` drains tree-sitter grammar warnings.

When the base ``tree-sitter`` package is installed but a per-language
grammar (e.g. ``tree_sitter_c_sharp``) is missing, the tree-sitter
strategy short-circuits and appends a structured warning to
``context["_warnings"]``. Discovery must surface that warning to stderr
at the end of the run -- otherwise the user sees ``wd discover`` succeed
with zero nodes for that language and no signal as to why.

These tests run discovery against a minimal repo that configures a
single tree-sitter source for C#, patches ``importlib.util.find_spec``
to report the C# grammar absent, and asserts that exactly one explicit
WARN line is printed to stderr naming the missing grammar and the
install command.
"""

from __future__ import annotations

import importlib.util as importlib_util
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld import discover as discover_mod  # noqa: E402


def _make_csharp_repo(root: Path) -> None:
    """Set up a minimal C# repo with a tree-sitter source entry."""
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    (root / ".weld" / "discover.yaml").write_text(
        "sources:\n"
        '  - glob: "**/*.cs"\n'
        "    type: file\n"
        "    strategy: tree_sitter\n"
        "    language: csharp\n",
        encoding="utf-8",
    )
    (root / "Sample.cs").write_text(
        "namespace N { public class C {} }\n",
        encoding="utf-8",
    )


def _fake_find_spec_factory(*missing_modules: str):
    """Return a ``find_spec`` replacement that reports the named modules absent."""
    real_find_spec = importlib_util.find_spec
    missing = frozenset(missing_modules)

    def _fake(name: str, package: object = None):
        if name in missing:
            return None
        return real_find_spec(name, package)

    return _fake


class DiscoverDrainsGrammarWarningTest(unittest.TestCase):
    """End-to-end: missing C# grammar -> one ``[weld] warning:`` line."""

    def test_missing_csharp_grammar_emits_one_warning_to_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_csharp_repo(root)
            err = io.StringIO()
            out = io.StringIO()
            with mock.patch(
                "weld.strategies.tree_sitter.TREE_SITTER_AVAILABLE", True,
            ), mock.patch(
                "weld.strategies._ts_parse._importlib_util.find_spec",
                side_effect=_fake_find_spec_factory("tree_sitter_c_sharp"),
            ), redirect_stderr(err), redirect_stdout(out):
                rc = discover_mod.main([
                    str(root),
                    "--quiet",
                    "--no-enrich",
                    "--no-sqlite",
                ])

            self.assertEqual(rc, 0, f"discover failed: {err.getvalue()}")
            stderr = err.getvalue()

            warn_lines = [
                line for line in stderr.splitlines()
                if "[weld] warning:" in line
            ]
            # Exactly one warning, and it identifies the missing grammar.
            self.assertEqual(
                len(warn_lines), 1,
                f"Expected exactly one [weld] warning line, got "
                f"{len(warn_lines)}: {warn_lines!r}",
            )
            line = warn_lines[0]
            self.assertIn("csharp", line)
            self.assertIn("tree-sitter-c-sharp", line)
            self.assertIn("pip install", line)

    def test_drain_dedupes_repeated_warnings(self) -> None:
        """Two source entries with the same missing grammar -> one stderr line."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".weld").mkdir(parents=True, exist_ok=True)
            (root / ".weld" / "discover.yaml").write_text(
                "sources:\n"
                '  - glob: "**/*.cs"\n'
                "    type: file\n"
                "    strategy: tree_sitter\n"
                "    language: csharp\n"
                '  - glob: "src/**/*.cs"\n'
                "    type: file\n"
                "    strategy: tree_sitter\n"
                "    language: csharp\n",
                encoding="utf-8",
            )
            (root / "Sample.cs").write_text(
                "namespace N { public class C {} }\n", encoding="utf-8",
            )

            err = io.StringIO()
            out = io.StringIO()
            with mock.patch(
                "weld.strategies.tree_sitter.TREE_SITTER_AVAILABLE", True,
            ), mock.patch(
                "weld.strategies._ts_parse._importlib_util.find_spec",
                side_effect=_fake_find_spec_factory("tree_sitter_c_sharp"),
            ), redirect_stderr(err), redirect_stdout(out):
                rc = discover_mod.main([
                    str(root),
                    "--quiet",
                    "--no-enrich",
                    "--no-sqlite",
                ])

            self.assertEqual(rc, 0, f"discover failed: {err.getvalue()}")
            stderr = err.getvalue()
            warn_lines = [
                line for line in stderr.splitlines()
                if "[weld] warning:" in line and "grammar" in line
            ]
            self.assertEqual(
                len(warn_lines), 1,
                f"Expected dedup to a single grammar-warning line; "
                f"got: {warn_lines!r}",
            )


if __name__ == "__main__":
    unittest.main()
