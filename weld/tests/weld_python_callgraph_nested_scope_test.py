"""ADR 0122 amendment / bd z0fh: function-nested scope attribution.

``_visit_function`` used to walk a function's own body for ``Call`` nodes
with a plain, unbounded ``ast.walk(child)`` sweep per direct child
statement. Because ``ast.walk`` does not stop at a nested
``FunctionDef``/``AsyncFunctionDef``/``ClassDef``'s own boundary, that sweep
descended into a directly-nested def's *entire* subtree -- its body,
decorator_list, and parameter defaults alike -- and attributed every call
found there to the OUTER enclosing function, in addition to (not instead
of) the nested def's own correct attribution via the subsequent recursive
``visit()`` dispatch. Two symptoms, one root cause:

1. A call inside a directly-nested def's own body was double-attributed
   (to both the outer function and the nested def).
2. A directly-nested def's own call-shaped parameter default had nowhere
   correct to land -- ADR 0122 deferred this case entirely (Decision item
   4), since resolving "which enclosing function" needed exactly the same
   bounded, scope-respecting walk ADR 0122 had just introduced for
   module/class-body attribution
   (:func:`weld.strategies._python_scope_walk._calls_in_own_scope`), just
   not yet applied to ``_visit_function``.

This module pins the fix: ``_visit_function`` now calls
``_calls_in_own_scope`` directly (the identical helper ``visit_Module`` /
``visit_ClassDef`` already use), so a directly-nested def is a boundary at
every scope level uniformly -- its body is excluded (already correctly
handled by the nested def's own recursive visit), its parameter defaults
are collected (they evaluate in the ENCLOSING scope, at ``def``-statement
execution time), and its decorator_list stays excluded (decorator
attribution is the separate, scope-independent ``decorates`` relationship,
recorded unconditionally by ``_record_decorators`` regardless of nesting
depth).

Companion to :mod:`weld.tests.weld_python_callgraph_scope_edges_test`
(module-level and class-body call sites -- the two ADR 0122 classes that
were already in scope) and :mod:`weld.tests.weld_python_callgraph_decorates_test`
(decorator_list attribution, unaffected by this amendment). Mirrors their
``_extract``/fixture style.
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
    root = Path(tempfile.mkdtemp(prefix="weld_nested_scope_"))
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


class NestedDefScopeTest(unittest.TestCase):
    """The bounded walk applied to a directly-nested def, at every angle."""

    def test_nested_def_body_call_is_not_double_attributed_to_outer(self) -> None:
        """The exact repro from ADR 0122 / bd z0fh: ``helper()`` lives in
        ``inner``'s body, which executes only when ``inner`` is later
        called -- it is not part of ``outer``'s own execution at all."""
        _, edges = _extract(
            """
            def helper():
                pass

            def outer():
                @some_decorator
                def inner():
                    helper()
                return inner
            """
        )
        self.assertIsNone(
            _edge(edges, "symbol:py:pkg.mod:outer",
                  "symbol:py:pkg.mod:helper", "calls"),
            f"a nested def's own body call must not leak onto the "
            f"enclosing function: {edges}",
        )
        self.assertIsNotNone(
            _edge(edges, "symbol:py:pkg.mod:outer.inner",
                  "symbol:py:pkg.mod:helper", "calls"),
            f"the nested def's own correct attribution must be unaffected: "
            f"{edges}",
        )

    def test_bare_decorator_on_nested_def_never_becomes_a_calls_edge(self) -> None:
        """Bounding the sweep must not turn decorator resolution into a
        ``calls`` edge from the enclosing scope -- ``decorates`` is the
        only relationship a decorator ever produces (ADR 0122 Decision
        item 1), unconditionally, at any nesting depth."""
        _, edges = _extract(
            """
            def helper():
                pass

            def outer():
                @some_decorator
                def inner():
                    helper()
                return inner
            """
        )
        self.assertIsNone(
            _edge(edges, "symbol:py:pkg.mod:outer",
                  f"{UNRESOLVED}some_decorator", "calls"),
            f"a decorator must never produce a calls edge, from any scope: "
            f"{edges}",
        )
        self.assertIsNotNone(
            _edge(edges, f"{UNRESOLVED}some_decorator",
                  "symbol:py:pkg.mod:outer.inner", "decorates"),
            f"the decorates edge itself must be unaffected by the bounded "
            f"sweep: {edges}",
        )

    def test_function_nested_default_attributes_to_enclosing_function(self) -> None:
        """The other half of the fix: a function-nested def's own
        call-shaped parameter default now attributes to the enclosing
        FUNCTION (mirrors
        ``weld_python_callgraph_scope_edges_test.ModuleLevelParameterDefaultTest``
        /
        ``weld_python_callgraph_scope_edges_test.ClassBodyCallTest.test_shallow_parameter_default_attributes_to_class``,
        extended to a function-direct enclosing scope)."""
        _, edges = _extract(
            """
            def helper():
                return 1

            def outer():
                def inner(x=helper()):
                    return x
                return inner
            """
        )
        match = _edge(
            edges, "symbol:py:pkg.mod:outer", "symbol:py:pkg.mod:helper", "calls",
        )
        self.assertIsNotNone(
            match, f"a function-nested def's own parameter default must "
            f"attribute to the enclosing function: {edges}",
        )
        self.assertIsNone(
            _edge(edges, "symbol:py:pkg.mod:outer.inner",
                  "symbol:py:pkg.mod:helper", "calls"),
            "the default must not ALSO be attributed to inner itself -- "
            "it runs before inner is ever called",
        )

    def test_deeply_nested_def_has_no_double_or_triple_count(self) -> None:
        """Two levels of nesting: each scope's own bounded walk captures
        only its own direct boundary children, so the fix composes without
        special-casing depth."""
        _, edges = _extract(
            """
            def helper():
                pass

            def outer():
                def middle():
                    def innermost():
                        helper()
                    return innermost
                return middle
            """
        )
        for caller in (
            "symbol:py:pkg.mod:outer",
            "symbol:py:pkg.mod:outer.middle",
        ):
            self.assertIsNone(
                _edge(edges, caller, "symbol:py:pkg.mod:helper", "calls"),
                f"{caller} must not pick up a call belonging to a deeper "
                f"nested def: {edges}",
            )
        self.assertIsNotNone(
            _edge(edges, "symbol:py:pkg.mod:outer.middle.innermost",
                  "symbol:py:pkg.mod:helper", "calls"),
            f"innermost's own call must still be attributed correctly: "
            f"{edges}",
        )

    def test_class_nested_in_function_body_call_not_swept_into_function(self) -> None:
        """A class directly nested inside a function's body is a boundary
        too: its own body's calls belong to the class's own symbol (via
        the recursive ``visit_ClassDef`` dispatch, unchanged by this fix),
        never to the enclosing function."""
        _, edges = _extract(
            """
            def build():
                return {}

            def outer():
                class Registry:
                    ENTRIES = build()
                return Registry
            """
        )
        self.assertIsNone(
            _edge(edges, "symbol:py:pkg.mod:outer",
                  "symbol:py:pkg.mod:build", "calls"),
            f"a nested class's own body call must not leak onto the "
            f"enclosing function: {edges}",
        )
        self.assertIsNotNone(
            _edge(edges, "symbol:py:pkg.mod:outer.Registry",
                  "symbol:py:pkg.mod:build", "calls"),
            f"the nested class's own correct attribution must be "
            f"unaffected: {edges}",
        )


class NestedDefDeterminismTest(unittest.TestCase):
    """The nested-def bounded walk is a pure function of the parsed tree."""

    def test_repeated_extraction_is_identical(self) -> None:
        source = """
            def a():
                return 1

            def outer():
                @some_decorator
                def inner(y=a()):
                    return c()
                return inner
            """
        first_nodes, first_edges = _extract(source)
        second_nodes, second_edges = _extract(source)
        self.assertEqual(sorted(first_nodes), sorted(second_nodes))
        self.assertEqual(
            sorted((e["from"], e["to"], e["type"]) for e in first_edges),
            sorted((e["from"], e["to"], e["type"]) for e in second_edges),
        )
        # outer picks up inner's own default (y=a()) but not inner's body
        # call (c()), and never double-counts across repeated extraction.
        self.assertIsNotNone(
            _edge(first_edges, "symbol:py:pkg.mod:outer",
                  "symbol:py:pkg.mod:a", "calls"),
        )
        self.assertIsNone(
            _edge(first_edges, "symbol:py:pkg.mod:outer",
                  f"{UNRESOLVED}c", "calls"),
        )


if __name__ == "__main__":
    unittest.main()
