"""Source-order independence for same-module call resolution.

Regression coverage for bd q6yd. ``_CallGraphVisitor`` resolved a bare-name
call against ``self.symbols``, which is filled *during* the walk, so a call
to a same-module top-level ``def`` declared **later** in the file found
nothing and fell through to ``symbol:unresolved:<name>``. The callee's real
symbol node then had no incoming ``calls`` edge and ``wd callers`` reported
a live function as having zero callers -- the answer that decides whether a
symbol is safe to delete.

Python binds every module-level name before any function body runs, so
source order is not part of name resolution. ADR 0004 and the
:mod:`weld.strategies.python_callgraph` docstring both promise
"same-module name lookup ... resolves to a sibling ``def foo`` defined in
the same module" with no ordering qualifier; this file pins that promise.

The bd q6yd report *suspected* argument position (``f(a, g(x).h())``) as
the cause. That was wrong -- ``ast.walk`` already descends into arguments --
so the repro shape is pinned here too, to keep the real fix from being
mistaken for the reported one and to stop the suspected mechanism from
regressing unnoticed.
"""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from weld.strategies import python_callgraph as pc

UNRESOLVED = pc.UNRESOLVED_PREFIX


def _extract(source: str, *, module: str = "pkg/mod.py") -> tuple[dict, list]:
    """Run the strategy over a single synthetic module and return its output."""
    root = Path(tempfile.mkdtemp(prefix="weld_fwd_ref_"))
    path = root / module
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    result = pc.extract(root, {"glob": f"{path.parent.name}/*.py"}, {})
    return result.nodes, result.edges


def _edge(edges: list, from_id: str, to_id: str) -> dict | None:
    return next(
        (
            e
            for e in edges
            if e["from"] == from_id and e["to"] == to_id and e["type"] == "calls"
        ),
        None,
    )


class ForwardReferenceResolutionTest(unittest.TestCase):
    """A callee declared below its caller still resolves to the local def."""

    def test_forward_reference_resolves_to_local_def(self) -> None:
        _, edges = _extract(
            """
            def caller():
                return callee()

            def callee():
                return 1
            """
        )
        match = _edge(
            edges, "symbol:py:pkg.mod:caller", "symbol:py:pkg.mod:callee"
        )
        self.assertIsNotNone(
            match,
            "a bare-name call to a same-module def declared LATER in the file "
            f"must resolve to the local def, not a sentinel: {edges}",
        )
        self.assertTrue(match["props"]["resolved"])
        self.assertEqual(match["props"]["resolution"], "local")
        self.assertEqual(match["props"]["confidence"], "definite")

    def test_forward_reference_mints_no_unresolved_sentinel(self) -> None:
        """The bogus ``symbol:unresolved:callee`` twin must be gone."""
        nodes, edges = _extract(
            """
            def caller():
                return callee()

            def callee():
                return 1
            """
        )
        self.assertNotIn(f"{UNRESOLVED}callee", nodes)
        self.assertIsNone(
            _edge(edges, "symbol:py:pkg.mod:caller", f"{UNRESOLVED}callee")
        )

    def test_backward_reference_unchanged(self) -> None:
        """The already-working direction keeps resolving identically."""
        _, edges = _extract(
            """
            def callee():
                return 1

            def caller():
                return callee()
            """
        )
        match = _edge(
            edges, "symbol:py:pkg.mod:caller", "symbol:py:pkg.mod:callee"
        )
        self.assertIsNotNone(match)
        self.assertEqual(match["props"]["resolution"], "local")

    def test_forward_reference_to_class_resolves(self) -> None:
        """A constructor call to a class declared later resolves too."""
        _, edges = _extract(
            """
            def build():
                return Widget()

            class Widget:
                pass
            """
        )
        self.assertIsNotNone(
            _edge(edges, "symbol:py:pkg.mod:build", "symbol:py:pkg.mod:Widget")
        )

    def test_mutual_recursion_resolves_both_directions(self) -> None:
        """Mutually recursive helpers each see the other."""
        _, edges = _extract(
            """
            def ping(n):
                return pong(n - 1)

            def pong(n):
                return ping(n - 1)
            """
        )
        self.assertIsNotNone(
            _edge(edges, "symbol:py:pkg.mod:ping", "symbol:py:pkg.mod:pong")
        )
        self.assertIsNotNone(
            _edge(edges, "symbol:py:pkg.mod:pong", "symbol:py:pkg.mod:ping")
        )


class ReportedReproShapeTest(unittest.TestCase):
    """The bd q6yd repro: a call in argument position, callee declared later.

    ``stale_payload(root, load_single_repo_for_read(root).stale())`` -- the
    callee sits in an argument *and* carries a trailing attribute call on its
    result. Argument position was never the defect, but the reported shape
    only produced an edge when the callee was declared first, so the whole
    shape is pinned rather than just the ordering half.
    """

    SOURCE = """
        from pkg.shaping import stale_payload


        def stale_for_root(root_path):
            return stale_payload(
                root_path, load_single_repo_for_read(root_path).stale()
            )


        def load_single_repo_for_read(root):
            return _Graph(root)
        """

    def test_call_in_argument_position_emits_edge(self) -> None:
        _, edges = _extract(self.SOURCE)
        match = _edge(
            edges,
            "symbol:py:pkg.mod:stale_for_root",
            "symbol:py:pkg.mod:load_single_repo_for_read",
        )
        self.assertIsNotNone(
            match,
            "the exact bd q6yd repro shape must emit a calls edge so "
            f"weld_callers can answer 'who calls this': {edges}",
        )
        self.assertTrue(match["props"]["resolved"])
        self.assertEqual(match["props"]["resolution"], "local")

    def test_enclosing_call_and_import_target_still_resolve(self) -> None:
        """The outer call keeps resolving through the import table."""
        _, edges = _extract(self.SOURCE)
        self.assertIsNotNone(
            _edge(
                edges,
                "symbol:py:pkg.mod:stale_for_root",
                "symbol:py:pkg.shaping:stale_payload",
            )
        )

    def test_attribute_call_on_call_result_stays_unresolved(self) -> None:
        """``f(x).stale()`` keeps its documented best-effort sentinel.

        ADR 0004 resolves attribute calls only through the import table; a
        receiver that is itself a call expression has no static type, so the
        trailing ``.stale()`` is a sentinel by design. Pinned so the fix to
        the *receiver* is not mistaken for a promise about the attribute.
        """
        _, edges = _extract(self.SOURCE)
        match = _edge(
            edges, "symbol:py:pkg.mod:stale_for_root", f"{UNRESOLVED}stale"
        )
        self.assertIsNotNone(match)
        self.assertFalse(match["props"]["resolved"])


class LocalResolutionScopeTest(unittest.TestCase):
    """Only module-level names resolve locally -- nested qualnames must not."""

    def test_method_name_does_not_resolve_as_top_level_symbol(self) -> None:
        """A bare call matching only a *method* name stays unresolved.

        ``symbol:py:<module>:<name>`` is a top-level id shape. A class method
        ``Holder.run`` is not reachable as a bare ``run()``, so minting
        ``symbol:py:pkg.mod:run`` would invent a symbol that does not exist.
        """
        nodes, edges = _extract(
            """
            def caller():
                return run()

            class Holder:
                def run(self):
                    return 1
            """
        )
        self.assertIsNone(
            _edge(edges, "symbol:py:pkg.mod:caller", "symbol:py:pkg.mod:run")
        )
        self.assertIsNotNone(
            _edge(edges, "symbol:py:pkg.mod:caller", f"{UNRESOLVED}run")
        )
        self.assertNotIn("symbol:py:pkg.mod:run", nodes)

    def test_nested_closure_name_does_not_resolve_as_top_level(self) -> None:
        """A ``def`` nested inside another ``def`` is not a module-level name."""
        _, edges = _extract(
            """
            def caller():
                return helper()

            def outer():
                def helper():
                    return 1
                return helper
            """
        )
        self.assertIsNone(
            _edge(edges, "symbol:py:pkg.mod:caller", "symbol:py:pkg.mod:helper")
        )
        self.assertIsNotNone(
            _edge(edges, "symbol:py:pkg.mod:caller", f"{UNRESOLVED}helper")
        )

    def test_def_inside_module_level_if_binds_at_module_scope(self) -> None:
        """A ``def`` under a module-level ``if`` is still a module-level name.

        The pre-collection has to descend through module-level compound
        statements: ``if TYPE_CHECKING:`` / ``try:`` / ``with`` bodies do not
        open a scope, so a ``def`` inside one binds exactly where a top-level
        ``def`` does -- and the walk already records it with a bare qualname.
        A flat scan of ``module.body`` would miss it and re-open the bug for
        every conditionally-defined helper.
        """
        _, edges = _extract(
            """
            import typing


            def caller():
                return shim()


            if typing.TYPE_CHECKING:
                def shim():
                    return 1
            """
        )
        self.assertIsNotNone(
            _edge(edges, "symbol:py:pkg.mod:caller", "symbol:py:pkg.mod:shim"),
            f"a def under a module-level `if` must resolve locally: {edges}",
        )

    def test_def_inside_module_level_try_binds_at_module_scope(self) -> None:
        """The import-fallback shim shape resolves to the local fallback def."""
        _, edges = _extract(
            """
            def caller():
                return loads('{}')


            try:
                from fast.json import loads
            except ImportError:
                def loads(text):
                    return {}
            """
        )
        self.assertIsNotNone(
            _edge(edges, "symbol:py:pkg.mod:caller", "symbol:py:pkg.mod:loads"),
            f"a def under a module-level `try` must resolve locally: {edges}",
        )

    def test_local_def_shadows_import_regardless_of_order(self) -> None:
        """A module-level ``def`` wins over a same-named import, either order.

        ``from x import shape`` followed by ``def shape()`` rebinds the name:
        at call time the local definition is what runs. Local-wins was already
        the precedence for a def declared *above* the call; this pins that the
        answer no longer depends on where the def sits.
        """
        _, edges = _extract(
            """
            from pkg.other import shape


            def caller():
                return shape()


            def shape():
                return 1
            """
        )
        self.assertIsNotNone(
            _edge(edges, "symbol:py:pkg.mod:caller", "symbol:py:pkg.mod:shape")
        )
        self.assertIsNone(
            _edge(edges, "symbol:py:pkg.mod:caller", "symbol:py:pkg.other:shape")
        )

    def test_unrelated_import_call_unchanged(self) -> None:
        """A name with no local def still resolves through the import table."""
        _, edges = _extract(
            """
            from pkg.other import shape


            def caller():
                return shape()
            """
        )
        match = _edge(
            edges, "symbol:py:pkg.mod:caller", "symbol:py:pkg.other:shape"
        )
        self.assertIsNotNone(match)
        self.assertEqual(match["props"]["resolution"], "import")


class DeterminismTest(unittest.TestCase):
    """Resolution must not depend on walk order between two runs."""

    def test_repeated_extraction_is_identical(self) -> None:
        source = """
            def a():
                return b()

            def b():
                return c()

            def c():
                return a()
            """
        first_nodes, first_edges = _extract(source)
        second_nodes, second_edges = _extract(source)
        self.assertEqual(sorted(first_nodes), sorted(second_nodes))
        self.assertEqual(
            [(e["from"], e["to"], e["type"]) for e in first_edges],
            [(e["from"], e["to"], e["type"]) for e in second_edges],
        )


if __name__ == "__main__":
    unittest.main()
