"""ADR 0122 coverage: decorator_list attribution as a ``decorates`` edge.

``_CallGraphVisitor`` used to walk only a ``FunctionDef``/``AsyncFunctionDef``
body for ``Call`` nodes, so a decorator never produced an edge anywhere in
the graph (bd vysw). This module pins decorator attribution: a distinct
``decorates`` edge (decorator's resolved target -> decorated symbol), never
``calls`` (applying a decorator does not call the decorated symbol), plus
the acceptance-critical fixture: a decorator-registered function must stop
reading as zero-inbound.

Companion to :mod:`weld.tests.weld_python_callgraph_scope_edges_test`
(module-level and class-body call sites -- the other two ADR 0122 classes)
and :mod:`weld.tests.weld_python_callgraph_forward_ref_test` (mirrors its
``_extract``/fixture style).
"""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path


from weld.strategies import python_callgraph as pc  # noqa: E402

UNRESOLVED = pc.UNRESOLVED_PREFIX


def _extract(source: str, *, module: str = "pkg/mod.py") -> tuple[dict, list]:
    """Run the strategy over a single synthetic module and return its output."""
    root = Path(tempfile.mkdtemp(prefix="weld_decorates_"))
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


def _edge(edges: list, from_id: str, to_id: str, edge_type: str = "calls") -> dict | None:
    matches = _edges(edges, from_id, to_id, edge_type)
    return matches[0] if matches else None


class DecoratorAttributionTest(unittest.TestCase):
    """decorator_list -> a ``decorates`` edge, never ``calls`` (ADR 0122)."""

    def test_call_shaped_decorator_resolves_and_decorates(self) -> None:
        _, edges = _extract(
            """
            from functools import lru_cache

            @lru_cache()
            def expensive():
                return 1
            """
        )
        match = _edge(
            edges, "symbol:py:functools:lru_cache",
            "symbol:py:pkg.mod:expensive", "decorates",
        )
        self.assertIsNotNone(
            match, f"@lru_cache() must produce a decorates edge: {edges}"
        )
        self.assertTrue(match["props"]["resolved"])
        self.assertEqual(match["props"]["confidence"], "definite")

    def test_bare_decorator_resolves_and_decorates(self) -> None:
        _, edges = _extract(
            """
            def retry(f):
                return f

            @retry
            def flaky():
                return 1
            """
        )
        match = _edge(
            edges, "symbol:py:pkg.mod:retry", "symbol:py:pkg.mod:flaky", "decorates",
        )
        self.assertIsNotNone(
            match,
            f"a bare (non-Call) decorator must still produce a decorates "
            f"edge, resolved the same way a bare-name call would be: {edges}",
        )
        self.assertEqual(match["props"]["resolution"], "local")

    def test_decorator_never_produces_a_calls_edge(self) -> None:
        """Ontology honesty: applying a decorator is not calling the
        decorated symbol -- ``lru_cache`` never gets a ``calls`` edge into
        ``expensive`` merely because it decorates it."""
        _, edges = _extract(
            """
            from functools import lru_cache

            @lru_cache()
            def expensive():
                return 1
            """
        )
        self.assertIsNone(
            _edge(edges, "symbol:py:functools:lru_cache",
                  "symbol:py:pkg.mod:expensive", "calls"),
            "a decorator must never be attributed a calls edge into what "
            "it decorates -- that would assert something false",
        )

    def test_forward_referenced_module_level_decorator_resolves(self) -> None:
        """Mirrors bd q6yd's forward-reference fix for calls: a decorator
        naming a module-level sibling declared LATER in the file must
        still resolve, via the same pre-scanned ``_module_level`` set."""
        _, edges = _extract(
            """
            @later_decorator
            def f():
                pass

            def later_decorator(fn):
                return fn
            """
        )
        match = _edge(
            edges, "symbol:py:pkg.mod:later_decorator",
            "symbol:py:pkg.mod:f", "decorates",
        )
        self.assertIsNotNone(
            match, f"a decorator declared later in the file must still "
            f"resolve: {edges}",
        )
        self.assertTrue(match["props"]["resolved"])
        self.assertEqual(match["props"]["resolution"], "local")

    def test_decorator_on_method_attributes_to_method(self) -> None:
        _, edges = _extract(
            """
            class Widget:
                @staticmethod
                def build():
                    return 1
            """
        )
        match = _edge(
            edges, "symbol:unresolved:staticmethod",
            "symbol:py:pkg.mod:Widget.build", "decorates",
        )
        self.assertIsNotNone(match, f"decorator on a method: {edges}")

    def test_decorator_on_nested_def_still_recorded(self) -> None:
        """A decorator inside another function's body is still attributed
        (no scope tracking needed for ``decorates`` -- see
        ``_record_decorators``, called unconditionally regardless of
        nesting depth). Unaffected by the ADR 0122 amendment / bd z0fh fix
        to that outer function's own call sweep (see
        ``weld_python_callgraph_scope_edges_test.NestedDefScopeTest``): a
        decorator never produces a ``calls`` edge either way, so bounding
        the sweep has nothing to change here."""
        _, edges = _extract(
            """
            def outer():
                @some_decorator
                def inner():
                    return 1
                return inner
            """
        )
        match = _edge(
            edges, "symbol:unresolved:some_decorator",
            "symbol:py:pkg.mod:outer.inner", "decorates",
        )
        self.assertIsNotNone(match, f"decorator on a nested def: {edges}")

    def test_flask_style_route_decorator(self) -> None:
        """The common ``app = Flask(__name__); @app.route(...)`` shape: the
        decorator attribute-chain resolves the same way an ordinary
        ``app.route()`` call would (unresolved, since ``app`` is a local
        variable, not an import) -- same fidelity as calls, not a new gap.
        """
        _, edges = _extract(
            """
            class Flask:
                def route(self, path):
                    def deco(f):
                        return f
                    return deco

            app = Flask()

            @app.route("/x")
            def view():
                return "ok"
            """
        )
        match = _edge(
            edges, "symbol:unresolved:route", "symbol:py:pkg.mod:view", "decorates",
        )
        self.assertIsNotNone(match, f"@app.route(...) decorates view: {edges}")


class DecoratedFunctionLivenessTest(unittest.TestCase):
    """Acceptance criterion: a decorator-registered function stops reading
    as zero-inbound (the false 'dead code' answer this issue reports)."""

    def test_decorated_view_gains_an_inbound_edge(self) -> None:
        nodes, edges = _extract(
            """
            class Flask:
                def route(self, path):
                    def deco(f):
                        return f
                    return deco

            app = Flask()

            @app.route("/x")
            def view():
                return "ok"
            """
        )
        view_id = "symbol:py:pkg.mod:view"
        self.assertIn(view_id, nodes)
        inbound = [e for e in edges if e["to"] == view_id]
        self.assertTrue(
            inbound,
            f"a decorator-registered function must have at least one "
            f"inbound edge -- it must not read as zero-inbound dead code: "
            f"{edges}",
        )
        self.assertEqual(inbound[0]["type"], "decorates")


if __name__ == "__main__":
    unittest.main()
