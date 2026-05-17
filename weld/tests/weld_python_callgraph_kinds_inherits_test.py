"""ADR 0064 criterion 1 + 2 coverage for the python_callgraph strategy.

Companion to :mod:`weld.tests.weld_python_callgraph_strategy_test`,
which covers the historical symbol/call-graph extraction surface. The
tests in this module exercise the additions that landed in bd 81s1:

* ``props.kind`` on every emitted python symbol, drawn from the
  vocabulary declared in :mod:`tools.tier_check_kinds`
  (``class`` / ``function`` / ``method``) so criterion 1
  (kind_correctness) measures rather than reporting stub-by-empty.
* ``inherits`` edges from subclass symbol -> base symbol so criterion
  2 (class_level_edges) can score on a bundled python fixture.

Resolution mirrors the existing call-target resolver: same-module
sibling class, import-table hit, or unresolved sentinel. The tests
below pin each branch.
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

from weld.strategies import python_callgraph as pc  # noqa: E402


class PythonCallgraphKindAndInheritsTest(unittest.TestCase):
    """ADR 0064 criterion 1 + 2 coverage: kind vocabulary + inherits edges."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="weld_kind_inherits_"))
        (self.tmp / "pkg").mkdir()
        (self.tmp / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        # ``base.py``: declares the parent class.
        (self.tmp / "pkg" / "base.py").write_text(
            textwrap.dedent(
                """
                class Base:
                    def hello(self):
                        return "hi"
                """
            ).lstrip(),
            encoding="utf-8",
        )
        # ``derived.py``: declares Child(Base), an unrelated module-level
        # function, plus a class with two bases (one resolved, one not).
        (self.tmp / "pkg" / "derived.py").write_text(
            textwrap.dedent(
                """
                from pkg.base import Base

                def free_function(x: int) -> int:
                    return x + 1

                class Child(Base):
                    def greet(self):
                        return self.hello() + "!"

                class Mixed(Base, Unknown):
                    pass
                """
            ).lstrip(),
            encoding="utf-8",
        )

    def _run(self) -> tuple[dict, list]:
        result = pc.extract(self.tmp, {"glob": "pkg/**/*.py"}, {})
        return result.nodes, result.edges

    # -- kind vocabulary -------------------------------------------------

    def test_class_symbol_has_kind_class(self) -> None:
        nodes, _ = self._run()
        for nid in ("symbol:py:pkg.base:Base", "symbol:py:pkg.derived:Child"):
            self.assertIn(nid, nodes)
            self.assertEqual(
                nodes[nid]["props"].get("kind"),
                "class",
                f"{nid} should be kind=class",
            )

    def test_module_function_has_kind_function(self) -> None:
        nodes, _ = self._run()
        nid = "symbol:py:pkg.derived:free_function"
        self.assertIn(nid, nodes)
        self.assertEqual(nodes[nid]["props"].get("kind"), "function")

    def test_method_symbol_has_kind_method(self) -> None:
        nodes, _ = self._run()
        nid = "symbol:py:pkg.derived:Child.greet"
        self.assertIn(nid, nodes)
        self.assertEqual(nodes[nid]["props"].get("kind"), "method")

    # -- inherits edge emission ------------------------------------------

    def test_inherits_edge_resolves_to_imported_base(self) -> None:
        _, edges = self._run()
        wanted = {
            "from": "symbol:py:pkg.derived:Child",
            "to": "symbol:py:pkg.base:Base",
            "type": "inherits",
        }
        match = next(
            (
                e
                for e in edges
                if e["from"] == wanted["from"]
                and e["to"] == wanted["to"]
                and e["type"] == wanted["type"]
            ),
            None,
        )
        self.assertIsNotNone(
            match, f"missing inherits edge Child -> Base: {edges}"
        )
        self.assertTrue(match["props"]["resolved"])
        self.assertEqual(match["props"]["raw"], "Base")

    def test_inherits_edge_falls_back_to_unresolved(self) -> None:
        nodes, edges = self._run()
        match = next(
            (
                e
                for e in edges
                if e["from"] == "symbol:py:pkg.derived:Mixed"
                and e["to"] == "symbol:unresolved:Unknown"
                and e["type"] == "inherits"
            ),
            None,
        )
        self.assertIsNotNone(
            match, f"missing unresolved-base inherits edge: {edges}"
        )
        self.assertFalse(match["props"]["resolved"])
        # Sentinel must exist so the graph is referentially closed.
        self.assertIn("symbol:unresolved:Unknown", nodes)

    def test_inherits_edge_originates_at_symbol_node(self) -> None:
        """ADR 0064 criterion 2: inherits originates at the class symbol,
        not at the file node."""
        _, edges = self._run()
        relevant = [e for e in edges if e["type"] == "inherits"]
        self.assertGreater(len(relevant), 0)
        for e in relevant:
            self.assertTrue(
                e["from"].startswith("symbol:"),
                f"inherits edge must originate at symbol, got {e['from']}",
            )

    def test_no_inherits_edge_for_object_only_classes(self) -> None:
        """A class without explicit bases emits no inherits edge.

        ``ast.ClassDef.bases`` is empty for ``class Base:`` -- emitting an
        implicit ``inherits -> object`` would clutter the graph with
        edges that carry no extraction signal.
        """
        _, edges = self._run()
        base_inherits = [
            e
            for e in edges
            if e["type"] == "inherits" and e["from"] == "symbol:py:pkg.base:Base"
        ]
        self.assertEqual(base_inherits, [])

    def test_inherits_edge_resolves_to_same_module_class(self) -> None:
        """A base defined in the same module resolves via visitor.symbols.

        Distinct from the import-table path; the same-module lookup is
        the first branch of :func:`_python_inherits._resolve_base` and
        the most common shape in real python code (subclass declared
        right under its parent in the same file).
        """
        td = Path(tempfile.mkdtemp(prefix="weld_same_module_base_"))
        (td / "pkg").mkdir()
        (td / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        (td / "pkg" / "single.py").write_text(
            textwrap.dedent(
                """
                class Parent:
                    pass

                class Child(Parent):
                    pass
                """
            ).lstrip(),
            encoding="utf-8",
        )
        result = pc.extract(td, {"glob": "pkg/**/*.py"}, {})
        match = next(
            (
                e
                for e in result.edges
                if e["from"] == "symbol:py:pkg.single:Child"
                and e["to"] == "symbol:py:pkg.single:Parent"
                and e["type"] == "inherits"
            ),
            None,
        )
        self.assertIsNotNone(
            match, f"missing same-module inherits edge: {result.edges}"
        )
        self.assertTrue(match["props"]["resolved"])
        self.assertEqual(match["props"]["resolution"], "local")


if __name__ == "__main__":
    unittest.main()
