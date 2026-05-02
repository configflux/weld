"""Tests for the python_callgraph extraction strategy.

``weld/docs/adr/0004-call-graph-schema-extension.md``.

Builds a small fixture project on disk with known intra-module and
cross-module calls and asserts that the strategy emits the expected
``symbol`` nodes and ``calls`` edges, including the unresolved sentinel
form for unknown call targets.
"""

from __future__ import annotations

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.strategies import python_callgraph  # noqa: E402

class PythonCallgraphStrategyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        # Module A: defines helper() and main() which calls helper().
        (self.tmp / "pkg").mkdir()
        (self.tmp / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        (self.tmp / "pkg" / "a.py").write_text(
            textwrap.dedent(
                """
                from pkg.b import other_helper

                def helper():
                    return 1

                def main():
                    helper()
                    other_helper()
                    unknown_func()
                    int("3")
                """
            ).lstrip(),
            encoding="utf-8",
        )
        # Module B: defines other_helper().
        (self.tmp / "pkg" / "b.py").write_text(
            textwrap.dedent(
                """
                def other_helper():
                    return 2
                """
            ).lstrip(),
            encoding="utf-8",
        )

    def _run(self) -> tuple[dict, list]:
        result = python_callgraph.extract(
            self.tmp,
            {"glob": "pkg/**/*.py"},
            {},
        )
        return result.nodes, result.edges

    def test_extracts_symbol_nodes(self) -> None:
        nodes, _ = self._run()
        # Expect symbols for helper, main, other_helper
        self.assertIn("symbol:py:pkg.a:helper", nodes)
        self.assertIn("symbol:py:pkg.a:main", nodes)
        self.assertIn("symbol:py:pkg.b:other_helper", nodes)
        for nid in (
            "symbol:py:pkg.a:helper",
            "symbol:py:pkg.a:main",
            "symbol:py:pkg.b:other_helper",
        ):
            self.assertEqual(nodes[nid]["type"], "symbol")
            self.assertEqual(
                nodes[nid]["props"]["source_strategy"], "python_callgraph"
            )
            self.assertEqual(nodes[nid]["props"]["language"], "python")

    def test_resolves_same_module_call(self) -> None:
        _, edges = self._run()
        wanted = {
            "from": "symbol:py:pkg.a:main",
            "to": "symbol:py:pkg.a:helper",
            "type": "calls",
        }
        match = next(
            (
                e
                for e in edges
                if e["from"] == wanted["from"]
                and e["to"] == wanted["to"]
                and e["type"] == "calls"
            ),
            None,
        )
        self.assertIsNotNone(match, f"missing same-module calls edge: {edges}")
        self.assertTrue(match["props"]["resolved"])
        self.assertEqual(match["props"]["raw"], "helper")
        self.assertEqual(match["props"]["resolution"], "local")
        self.assertEqual(match["props"]["provenance"], {"file": "pkg/a.py", "line": 7})

    def test_resolves_imported_call(self) -> None:
        _, edges = self._run()
        match = next(
            (
                e
                for e in edges
                if e["from"] == "symbol:py:pkg.a:main"
                and e["to"] == "symbol:py:pkg.b:other_helper"
                and e["type"] == "calls"
            ),
            None,
        )
        self.assertIsNotNone(match, f"missing import-resolved calls edge: {edges}")
        self.assertTrue(match["props"]["resolved"])
        self.assertEqual(match["props"]["raw"], "other_helper")
        self.assertEqual(match["props"]["resolution"], "import")

    def test_unresolved_call_uses_sentinel(self) -> None:
        nodes, edges = self._run()
        sentinel = "symbol:unresolved:unknown_func"
        self.assertIn(sentinel, nodes)
        self.assertEqual(nodes[sentinel]["type"], "symbol")
        self.assertFalse(nodes[sentinel]["props"]["resolved"])
        # And there is a calls edge ending at the sentinel.
        match = next(
            (
                e
                for e in edges
                if e["from"] == "symbol:py:pkg.a:main"
                and e["to"] == sentinel
                and e["type"] == "calls"
            ),
            None,
        )
        self.assertIsNotNone(match, f"missing unresolved calls edge: {edges}")
        self.assertFalse(match["props"]["resolved"])
        self.assertEqual(match["props"]["raw"], "unknown_func")
        self.assertEqual(match["props"]["resolution"], "unresolved")
        self.assertEqual(match["props"]["provenance"], {"file": "pkg/a.py", "line": 9})

    def test_builtin_call_is_classified(self) -> None:
        nodes, edges = self._run()
        sentinel = "symbol:unresolved:int"
        self.assertIn(sentinel, nodes)
        self.assertEqual(nodes[sentinel]["props"]["resolution"], "builtin")
        match = next(
            (
                e
                for e in edges
                if e["from"] == "symbol:py:pkg.a:main"
                and e["to"] == sentinel
                and e["type"] == "calls"
            ),
            None,
        )
        self.assertIsNotNone(match, f"missing builtin calls edge: {edges}")
        self.assertFalse(match["props"]["resolved"])
        self.assertEqual(match["props"]["raw"], "int")
        self.assertEqual(match["props"]["resolution"], "builtin")

    def test_strategy_handles_syntax_error_files(self) -> None:
        bad = self.tmp / "pkg" / "broken.py"
        bad.write_text("def oops(:\n", encoding="utf-8")
        # Must not raise.
        nodes, edges = self._run()
        self.assertNotIn("symbol:py:pkg.broken:oops", nodes)

if __name__ == "__main__":
    unittest.main()
