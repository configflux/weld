"""bd vrr4: AsyncFunctionDef coverage across the python_callgraph test family.

Before this file, ``grep -rln 'async def' weld/tests/weld_python_callgraph*.py``
returned zero matches anywhere in the callgraph test surface --
``_CallGraphVisitor.visit_AsyncFunctionDef`` delegates to the same
``_visit_function`` :meth:`visit_FunctionDef` uses, and
:func:`weld.strategies._python_scope_walk._calls_in_own_scope`'s own boundary
check already tests ``isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))``
symmetrically, so the code path was already async-aware by construction --
but nothing pinned that a future refactor which special-cased ``FunctionDef``
without noticing ``AsyncFunctionDef`` would regress silently. This module is
that pin, mirroring the fixture/assertion shapes of the three files it
completes the family with: :mod:`weld.tests.weld_python_callgraph_scope_edges_test`
(module/class-body call sites + shallow parameter defaults, ADR 0122),
:mod:`weld.tests.weld_python_callgraph_decorates_test` (decorator_list
attribution, ADR 0122), and :mod:`weld.tests.weld_python_callgraph_nested_scope_test`
(the bounded-walk, no-double-count property, ADR 0122 amendment / bd z0fh).
A sibling file rather than an extension of any of the three: each is
already within reach of the 400-line repo cap, and async coverage across
all three concerns would not fit the headroom any one of them has left.

Cases pinned: an async top-level function's own calls (symmetric with a
sync function); an ``await``-wrapped call (the ``Await`` node is fully
transparent to the shared ``ast.iter_child_nodes`` walk -- there is no
special-casing for it anywhere in the visitor, so a call reached only
through ``await`` is recorded exactly like a bare call); async-in-sync and
sync-in-async nesting (no double-count, the z0fh bounded-walk property
applied with the async/sync roles swapped both ways); an async def's own
parameter defaults at all three ADR 0122 scope levels (module-direct,
class-direct, function-nested); decorator_list attribution on a
module-direct and a nested async def; and async method kind classification
(a class-direct async def registers ``kind=method``, exactly like a sync
one) plus the class-body sweep boundary (an async method's own call, even
when reached through ``await``, is not swept into its class).
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
    root = Path(tempfile.mkdtemp(prefix="weld_async_"))
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


class AsyncTopLevelCallTest(unittest.TestCase):
    """An async top-level function's own body calls, symmetric with sync."""

    def test_async_def_call_produces_calls_edge(self) -> None:
        nodes, edges = _extract(
            """
            def helper():
                return 1

            async def outer():
                helper()
            """
        )
        self.assertIn(
            "symbol:py:pkg.mod:outer", nodes,
            "an AsyncFunctionDef must mint its own symbol node, exactly "
            "like a FunctionDef",
        )
        self.assertEqual(
            nodes["symbol:py:pkg.mod:outer"]["props"]["kind"], "function",
            "a top-level async def must register kind=function -- only a "
            "class-direct async def becomes kind=method",
        )
        self.assertIsNotNone(
            _edge(edges, "symbol:py:pkg.mod:outer",
                  "symbol:py:pkg.mod:helper", "calls"),
            f"an async def's own body call must produce a calls edge, "
            f"exactly like a sync def's: {edges}",
        )

    def test_await_wrapped_call_recorded_as_ordinary_calls_edge(self) -> None:
        """The ``Await`` node wrapping ``helper()`` is not a boundary of any
        kind to ``_calls_in_own_scope`` -- it is neither ``FunctionDef``,
        ``AsyncFunctionDef``, ``ClassDef``, nor ``Call``, so the generic
        ``ast.iter_child_nodes`` descent walks straight through it to the
        ``Call`` node underneath. Confirmed empirically: first written
        against the wrong expectation that ``await`` suppresses or alters
        call detection, which failed red against the actual output -- that
        failure is what earns this module the right to pin the opposite.
        An ``await``-wrapped call is indistinguishable, edge for edge, from
        a bare call.
        """
        _, edges = _extract(
            """
            async def helper():
                return 1

            async def outer():
                await helper()
            """
        )
        matches = _edges(
            edges, "symbol:py:pkg.mod:outer",
            "symbol:py:pkg.mod:helper", "calls",
        )
        self.assertEqual(
            len(matches), 1,
            f"await helper() must produce exactly one calls edge, not zero "
            f"(suppressed) or two (double-walked via Await.value): {edges}",
        )
        self.assertEqual(matches[0]["props"]["resolution"], "local")


class AsyncSyncNestingTest(unittest.TestCase):
    """The z0fh bounded-walk, no-double-count property with mixed sync/async
    nesting -- both directions."""

    def test_async_in_sync_nesting_no_double_count(self) -> None:
        """A sync outer, an async def nested directly inside it: the
        nested def's own body call attributes only to the nested def, never
        (also) to the sync outer -- mirrors
        ``weld_python_callgraph_nested_scope_test.NestedDefScopeTest``
        .``test_nested_def_body_call_is_not_double_attributed_to_outer``
        with the nested def's kind flipped from sync to async."""
        nodes, edges = _extract(
            """
            def helper():
                pass

            def outer():
                async def inner():
                    helper()
                return inner
            """
        )
        self.assertIn(
            "symbol:py:pkg.mod:outer.inner", nodes,
            "the nested AsyncFunctionDef must mint its own symbol node",
        )
        self.assertIsNone(
            _edge(edges, "symbol:py:pkg.mod:outer",
                  "symbol:py:pkg.mod:helper", "calls"),
            f"an async def nested in a sync def must not leak its own body "
            f"call onto the sync outer: {edges}",
        )
        self.assertIsNotNone(
            _edge(edges, "symbol:py:pkg.mod:outer.inner",
                  "symbol:py:pkg.mod:helper", "calls"),
            f"the async nested def's own correct attribution must be "
            f"unaffected: {edges}",
        )

    def test_sync_in_async_nesting_no_double_count(self) -> None:
        """The mirror image: an async outer, a sync def nested directly
        inside it -- the nested def's own body call attributes only to the
        nested def, never to the async outer."""
        nodes, edges = _extract(
            """
            def helper():
                pass

            async def outer():
                def inner():
                    helper()
                return inner
            """
        )
        self.assertIn(
            "symbol:py:pkg.mod:outer", nodes,
            "the async outer AsyncFunctionDef must mint its own symbol node",
        )
        self.assertIsNone(
            _edge(edges, "symbol:py:pkg.mod:outer",
                  "symbol:py:pkg.mod:helper", "calls"),
            f"a sync def nested in an async def must not leak its own body "
            f"call onto the async outer: {edges}",
        )
        self.assertIsNotNone(
            _edge(edges, "symbol:py:pkg.mod:outer.inner",
                  "symbol:py:pkg.mod:helper", "calls"),
            f"the sync nested def's own correct attribution must be "
            f"unaffected: {edges}",
        )


class AsyncParameterDefaultTest(unittest.TestCase):
    """An async def's own parameter defaults, at all three ADR 0122 scope
    levels -- module-direct, class-direct, and function-nested."""

    def test_module_direct_async_def_default_attributes_to_file(self) -> None:
        _, edges = _extract(
            """
            def helper():
                return 1

            async def f(x=helper()):
                return x
            """
        )
        match = _edge(edges, "file:pkg/mod", "symbol:py:pkg.mod:helper", "calls")
        self.assertIsNotNone(
            match, f"a module-level async def's own parameter default must "
            f"attribute to file:, exactly like a sync def's: {edges}",
        )
        self.assertIsNone(
            _edge(edges, "symbol:py:pkg.mod:f", "symbol:py:pkg.mod:helper", "calls"),
            "the default must not ALSO be attributed to f itself -- it "
            "runs before f is ever called",
        )

    def test_class_direct_async_def_default_attributes_to_class(self) -> None:
        _, edges = _extract(
            """
            def helper():
                return 1

            class Widget:
                async def build(self, x=helper()):
                    return x
            """
        )
        match = _edge(
            edges, "symbol:py:pkg.mod:Widget", "symbol:py:pkg.mod:helper", "calls",
        )
        self.assertIsNotNone(
            match, f"an async method's own parameter default must "
            f"attribute to the class, exactly like a sync method's: {edges}",
        )
        self.assertIsNone(
            _edge(edges, "symbol:py:pkg.mod:Widget.build",
                  "symbol:py:pkg.mod:helper", "calls"),
            "the default must not ALSO be attributed to the async method "
            "itself -- it runs before the method is ever called",
        )

    def test_function_nested_async_def_default_attributes_to_enclosing_function(
        self,
    ) -> None:
        """Mirrors ``NestedDefScopeTest``
        .``test_function_nested_default_attributes_to_enclosing_function``
        with the nested def's kind flipped to async: the default still
        evaluates in the sync outer's own scope, at ``def inner(...)``
        statement-execution time, regardless of which of the two defs is
        async."""
        _, edges = _extract(
            """
            def helper():
                return 1

            def outer():
                async def inner(x=helper()):
                    return x
                return inner
            """
        )
        match = _edge(
            edges, "symbol:py:pkg.mod:outer", "symbol:py:pkg.mod:helper", "calls",
        )
        self.assertIsNotNone(
            match, f"a function-nested async def's own parameter default "
            f"must attribute to the enclosing function: {edges}",
        )
        self.assertIsNone(
            _edge(edges, "symbol:py:pkg.mod:outer.inner",
                  "symbol:py:pkg.mod:helper", "calls"),
            "the default must not ALSO be attributed to the async inner "
            "def -- it runs before inner is ever called",
        )


class AsyncDecoratorTest(unittest.TestCase):
    """decorator_list attribution on an async def (ADR 0122)."""

    def test_decorator_on_module_direct_async_def_resolves_and_decorates(self) -> None:
        _, edges = _extract(
            """
            def some_decorator(f):
                return f

            @some_decorator
            async def flaky():
                return 1
            """
        )
        match = _edge(
            edges, "symbol:py:pkg.mod:some_decorator",
            "symbol:py:pkg.mod:flaky", "decorates",
        )
        self.assertIsNotNone(
            match, f"a decorator on an async def must produce a decorates "
            f"edge, exactly like on a sync def: {edges}",
        )
        self.assertIsNone(
            _edge(edges, "symbol:py:pkg.mod:some_decorator",
                  "symbol:py:pkg.mod:flaky", "calls"),
            "decorating an async def must never ALSO produce a calls edge",
        )

    def test_decorator_on_nested_async_def_still_recorded(self) -> None:
        """No scope tracking needed for ``decorates`` at any nesting depth
        or sync/async kind -- mirrors ``DecoratorAttributionTest``
        .``test_decorator_on_nested_def_still_recorded``."""
        _, edges = _extract(
            """
            async def outer():
                @some_decorator
                async def inner():
                    return 1
                return inner
            """
        )
        match = _edge(
            edges, f"{UNRESOLVED}some_decorator",
            "symbol:py:pkg.mod:outer.inner", "decorates",
        )
        self.assertIsNotNone(match, f"decorator on a nested async def: {edges}")


class AsyncClassMethodTest(unittest.TestCase):
    """Async method kind classification and the class-body sweep boundary."""

    def test_async_method_registers_as_method_kind(self) -> None:
        nodes, _ = _extract(
            """
            class Widget:
                async def build(self):
                    return 1
            """
        )
        self.assertIn(
            "symbol:py:pkg.mod:Widget.build", nodes,
            "a class-direct async def must mint its own symbol node",
        )
        self.assertEqual(
            nodes["symbol:py:pkg.mod:Widget.build"]["props"]["kind"], "method",
            "a class-direct async def must register kind=method, exactly "
            "like a sync method -- only a def nested inside another def's "
            "own body should ever fall back to kind=function",
        )

    def test_async_method_own_call_not_swept_into_class(self) -> None:
        """An async def directly inside a class body: its own body call
        (reached through ``await``) attributes to the method, never to the
        enclosing class -- mirrors ``ClassBodyCallTest``
        .``test_method_call_not_swept_into_class_attribution`` with an
        async, ``await``-wrapped call."""
        _, edges = _extract(
            """
            async def helper():
                return 1

            class Widget:
                async def build(self):
                    return await helper()
            """
        )
        self.assertIsNone(
            _edge(edges, "symbol:py:pkg.mod:Widget",
                  "symbol:py:pkg.mod:helper", "calls"),
            f"an async method's own call must not leak onto its class: {edges}",
        )
        self.assertIsNotNone(
            _edge(edges, "symbol:py:pkg.mod:Widget.build",
                  "symbol:py:pkg.mod:helper", "calls"),
        )


if __name__ == "__main__":
    unittest.main()
