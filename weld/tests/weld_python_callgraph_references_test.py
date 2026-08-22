"""ADR 0127 coverage: same-module bare-name VALUE references (bd lid2).

``_CallGraphVisitor`` walked only ``Call`` nodes, so a class or function
named as a plain VALUE -- a keyword-argument value, a tuple/list element,
an assignment RHS -- and never invoked produced no edge anywhere in the
graph (bd lid2: ``weld.mcp_server:build_tools`` passing ``tool_cls=Tool``
in its own module). This module pins the fix: a distinct ``references``
edge (referencing symbol -> referenced symbol), never ``calls`` (nothing
is invoked), scoped to same-module hits only -- plus the acceptance-
critical fixture: a value-referenced class must stop reading as
zero-inbound.

Companion to :mod:`weld.tests.weld_python_callgraph_decorates_test`
(mirrors its ``_extract``/fixture style) and
:mod:`weld.tests.weld_python_callgraph_scope_edges_test` (the module-level
/ class-body ``calls`` sourcing this reuses).
"""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path


from weld.strategies import python_callgraph as pc  # noqa: E402


def _extract(source: str, *, module: str = "pkg/mod.py") -> tuple[dict, list]:
    """Run the strategy over a single synthetic module and return its output."""
    root = Path(tempfile.mkdtemp(prefix="weld_references_"))
    path = root / module
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    result = pc.extract(root, {"glob": f"{path.parent.name}/*.py"}, {})
    return result.nodes, result.edges


def _edges(edges: list, from_id: str, to_id: str, edge_type: str) -> list[dict]:
    return [
        e for e in edges
        if e["from"] == from_id and e["to"] == to_id and e["type"] == edge_type
    ]


def _edge(edges: list, from_id: str, to_id: str, edge_type: str) -> dict | None:
    matches = _edges(edges, from_id, to_id, edge_type)
    return matches[0] if matches else None


class ReferenceAttributionTest(unittest.TestCase):
    """A bare-name VALUE reference -> a ``references`` edge, never ``calls``."""

    def test_keyword_argument_value_produces_references_edge(self) -> None:
        """The exact bd lid2 repro shape: ``tool_cls=Tool``."""
        _, edges = _extract(
            """
            class Tool:
                pass


            def other(tool_cls):
                return tool_cls


            def build_tools():
                return other(tool_cls=Tool)
            """
        )
        match = _edge(
            edges, "symbol:py:pkg.mod:build_tools", "symbol:py:pkg.mod:Tool",
            "references",
        )
        self.assertIsNotNone(
            match, f"tool_cls=Tool must produce a references edge: {edges}"
        )
        self.assertTrue(match["props"]["resolved"])
        self.assertEqual(match["props"]["resolution"], "local")
        self.assertEqual(match["props"]["confidence"], "definite")

    def test_reference_never_produces_a_calls_edge(self) -> None:
        """Ontology honesty: Tool is passed, never invoked."""
        _, edges = _extract(
            """
            class Tool:
                pass


            def other(tool_cls):
                return tool_cls


            def build_tools():
                return other(tool_cls=Tool)
            """
        )
        self.assertIsNone(
            _edge(edges, "symbol:py:pkg.mod:build_tools",
                  "symbol:py:pkg.mod:Tool", "calls"),
            "a bare-name value reference must never be attributed a calls "
            "edge -- Tool is never invoked",
        )

    def test_call_target_is_not_also_a_reference(self) -> None:
        """Mutual exclusivity: ``Tool()`` is already a calls edge -- the
        same occurrence must not ALSO produce a references edge."""
        _, edges = _extract(
            """
            class Tool:
                pass


            def build_tools():
                return Tool()
            """
        )
        self.assertIsNone(
            _edge(edges, "symbol:py:pkg.mod:build_tools",
                  "symbol:py:pkg.mod:Tool", "references"),
            f"Tool() is a call; it must not also be a references edge: {edges}",
        )
        self.assertIsNotNone(
            _edge(edges, "symbol:py:pkg.mod:build_tools",
                  "symbol:py:pkg.mod:Tool", "calls"),
        )

    def test_module_level_tuple_reference_sourced_at_file(self) -> None:
        _, edges = _extract(
            """
            class Foo:
                pass


            class Bar:
                pass


            REGISTRY = (Foo, Bar)
            """
        )
        self.assertIsNotNone(
            _edge(edges, "file:pkg/mod", "symbol:py:pkg.mod:Foo", "references"),
            f"module-level tuple entry Foo: {edges}",
        )
        self.assertIsNotNone(
            _edge(edges, "file:pkg/mod", "symbol:py:pkg.mod:Bar", "references"),
            f"module-level tuple entry Bar: {edges}",
        )

    def test_class_body_reference_sourced_at_class_symbol(self) -> None:
        _, edges = _extract(
            """
            class Foo:
                pass


            class Widget:
                DEFAULT = Foo
            """
        )
        self.assertIsNotNone(
            _edge(edges, "symbol:py:pkg.mod:Widget", "symbol:py:pkg.mod:Foo",
                  "references"),
            f"class-body reference: {edges}",
        )

    def test_attribute_access_is_not_a_reference(self) -> None:
        """Navigating THROUGH a name (``Foo.BAR``) is not a reference to
        ``Foo`` -- attribute-shaped access is out of ADR 0127's scope even
        when the base is same-module."""
        _, edges = _extract(
            """
            class Foo:
                BAR = 1


            def read_bar():
                return Foo.BAR
            """
        )
        self.assertIsNone(
            _edge(edges, "symbol:py:pkg.mod:read_bar", "symbol:py:pkg.mod:Foo",
                  "references"),
            f"Foo.BAR must not produce a references edge to Foo: {edges}",
        )

    def test_cross_module_import_reference_is_not_recorded(self) -> None:
        """The second bd lid2 comment's shape (a cross-module bare-name
        value reference via the import table, e.g. a predicate tuple) is
        deliberately out of THIS deliverable's scope -- see ADR 0127
        'Alternatives considered' and the growth-bound measurement."""
        root = Path(tempfile.mkdtemp(prefix="weld_references_xmod_"))
        (root / "pkg").mkdir(parents=True, exist_ok=True)
        (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        (root / "pkg" / "alpha.py").write_text(
            "def predicate():\n    return True\n", encoding="utf-8",
        )
        (root / "pkg" / "beta.py").write_text(
            "from pkg.alpha import predicate\n\n"
            "PREDICATES = (predicate,)\n",
            encoding="utf-8",
        )
        result = pc.extract(root, {"glob": "pkg/*.py"}, {})
        matches = [
            e for e in result.edges
            if e["type"] == "references"
            and e["to"] == "symbol:py:pkg.alpha:predicate"
        ]
        self.assertEqual(
            matches, [],
            f"a cross-module (import-table) bare-name value reference must "
            f"not produce a references edge in this scope: {matches}",
        )

    def test_unresolved_reference_is_not_recorded(self) -> None:
        _, edges = _extract(
            """
            def build_tools(unknown_name):
                return {"x": unknown_name}
            """
        )
        self.assertFalse(
            [e for e in edges if e["type"] == "references"],
            f"a bare name that resolves to nothing must not produce a "
            f"references edge or a sentinel node: {edges}",
        )

    def test_duplicate_reference_in_same_scope_dedups(self) -> None:
        _, edges = _extract(
            """
            class Tool:
                pass


            def build_tools():
                a = Tool
                b = Tool
                return a, b
            """
        )
        matches = _edges(
            edges, "symbol:py:pkg.mod:build_tools", "symbol:py:pkg.mod:Tool",
            "references",
        )
        self.assertEqual(
            len(matches), 1,
            f"referencing Tool twice from the same scope must be one edge, "
            f"not two: {matches}",
        )


class ReferencedSymbolLivenessTest(unittest.TestCase):
    """Acceptance criterion (bd lid2): a class named as a keyword-argument
    value in its own module stops reading as zero-inbound."""

    def test_referenced_class_gains_an_inbound_edge(self) -> None:
        nodes, edges = _extract(
            """
            class Tool:
                pass


            def _build_tools_impl(tool_cls):
                return tool_cls


            def build_tools():
                return _build_tools_impl(tool_cls=Tool)
            """
        )
        tool_id = "symbol:py:pkg.mod:Tool"
        self.assertIn(tool_id, nodes)
        inbound = [e for e in edges if e["to"] == tool_id]
        self.assertTrue(
            inbound,
            f"a same-module value-referenced class must have at least one "
            f"inbound edge -- it must not read as zero-inbound dead code: "
            f"{edges}",
        )
        self.assertEqual(inbound[0]["type"], "references")


class BoundedScopeRefactorRegressionTest(unittest.TestCase):
    """Guards the ``_bounded_scope_nodes`` shared-walk refactor this issue
    introduced (``_python_scope_walk.py``): excluding a ``Call``'s own
    callee from reference-collection must not also exclude it from CALL
    discovery -- a chained call (``foo().bar()``) must still surface the
    inner call the way the pre-refactor unbounded ``iter_child_nodes``
    fallthrough did."""

    def test_chained_call_still_discovers_the_inner_call(self) -> None:
        _, edges = _extract(
            """
            def foo():
                return None


            def caller():
                return foo().bar()
            """
        )
        self.assertIsNotNone(
            _edge(edges, "symbol:py:pkg.mod:caller", "symbol:py:pkg.mod:foo",
                  "calls"),
            f"foo() nested inside foo().bar() must still be discovered: {edges}",
        )


if __name__ == "__main__":
    unittest.main()
