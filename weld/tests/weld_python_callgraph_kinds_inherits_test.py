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

import tempfile
import textwrap
import unittest
from pathlib import Path


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


class QualnameFromSymbolIdTest(unittest.TestCase):
    """``qualname_from_symbol_id`` returns the bare qualname, never module:qual.

    Pins the parsing fix for the cross-glob node-clobber bug: the resolved
    target mint must not leak ``<module>:<qualname>`` into the label. A
    dotted qualname (``Class.method``) must survive verbatim, and a malformed
    id falls back to the trailing colon-segment without raising.
    """

    def _qfn(self):
        from weld.strategies._python_origin import qualname_from_symbol_id

        return qualname_from_symbol_id

    def test_simple_qualname(self) -> None:
        self.assertEqual(
            self._qfn()("symbol:py:weld._git:commits_behind"), "commits_behind"
        )

    def test_dotted_module_simple_qualname(self) -> None:
        self.assertEqual(self._qfn()("symbol:py:os.path:join"), "join")

    def test_nested_qualname_preserved(self) -> None:
        self.assertEqual(self._qfn()("symbol:py:pkg.mod:Class.method"), "Class.method")

    def test_malformed_id_falls_back_to_trailing_segment(self) -> None:
        self.assertEqual(self._qfn()("symbol:unresolved:print"), "print")
        self.assertEqual(self._qfn()("bare"), "bare")


class MakeResolvedTargetNodeShapeTest(unittest.TestCase):
    """``make_resolved_target_node`` mints a well-formed, colon-free label."""

    def _node(self, target_id: str, origin: str = "external") -> dict:
        from weld.strategies._python_origin import make_resolved_target_node

        return make_resolved_target_node(target_id, origin)

    def test_label_and_qualname_are_bare(self) -> None:
        node = self._node("symbol:py:weld._git:commits_behind", "project")
        self.assertEqual(node["label"], "commits_behind")
        self.assertEqual(node["props"]["qualname"], "commits_behind")
        self.assertEqual(node["props"]["module"], "weld._git")
        self.assertNotIn(":", node["label"])
        self.assertEqual(node["props"]["origin"], "project")

    def test_nested_qualname_node(self) -> None:
        node = self._node("symbol:py:pkg.mod:Outer.inner")
        self.assertEqual(node["label"], "Outer.inner")
        self.assertEqual(node["props"]["qualname"], "Outer.inner")
        self.assertEqual(node["props"]["module"], "pkg.mod")


class ResolvedTargetLabelIntegrationTest(unittest.TestCase):
    """End-to-end: a resolved target the glob did not walk gets a clean label.

    Regression for the cross-glob node-clobber bug. ``main`` calls an
    imported stdlib (``os.path.join``) and third-party (``foo``) symbol the
    strategy never walks, so each target node is minted by
    ``make_resolved_target_node``. The label/qualname must be the bare
    ``<qualname>`` -- never the leaked ``<module>:<qualname>`` -- and
    ``props.module`` the dotted module, matching how same-glob definite
    nodes are shaped. ``props.file`` is intentionally not asserted: a
    cross-glob target's defining file is unknown at single-glob mint time
    and is restored by the orchestrator's post-merge reconciliation.
    """

    def _run(self) -> dict:
        td = Path(tempfile.mkdtemp(prefix="weld_resolved_label_"))
        (td / "pkg").mkdir()
        (td / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        (td / "pkg" / "a.py").write_text(
            textwrap.dedent(
                """
                from os.path import join
                from third_party_pkg import foo

                def main():
                    join("a", "b")
                    foo()
                """
            ).lstrip(),
            encoding="utf-8",
        )
        return pc.extract(td, {"glob": "pkg/**/*.py"}, {}).nodes

    def _assert_wellformed(
        self, nodes: dict, nid: str, module: str, qual: str
    ) -> None:
        self.assertIn(nid, nodes, f"resolved-target node not minted: {nid}")
        props = nodes[nid]["props"]
        self.assertEqual(
            nodes[nid]["label"], qual,
            f"{nid}: label leaked module:qualname instead of bare qualname",
        )
        self.assertEqual(
            props.get("qualname"), qual,
            f"{nid}: props.qualname leaked module:qualname",
        )
        self.assertEqual(props.get("module"), module, f"{nid}: wrong props.module")
        self.assertNotIn(
            ":", str(nodes[nid]["label"]),
            f"{nid}: label still contains a ':' (module:qualname leak)",
        )

    def test_stdlib_resolved_target_label_is_bare_qualname(self) -> None:
        self._assert_wellformed(
            self._run(), "symbol:py:os.path:join", "os.path", "join"
        )

    def test_external_resolved_target_label_is_bare_qualname(self) -> None:
        self._assert_wellformed(
            self._run(), "symbol:py:third_party_pkg:foo", "third_party_pkg", "foo"
        )


if __name__ == "__main__":
    unittest.main()
