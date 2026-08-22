"""ADR 0122 coverage: module-level and class-body statement call sites.

``_CallGraphVisitor`` used to walk only a ``FunctionDef``/``AsyncFunctionDef``
body for ``Call`` nodes, so a module-level statement or a class-body
statement never produced a ``calls`` edge anywhere in the graph (bd vysw).
This module pins module-level statement calls (sourced at the ``file:``
node -- no symbol node represents "the module itself") and class-body
statement calls (sourced at the class's own symbol -- a class body
genuinely executes once, at class-definition time), including a module- or
class-direct ``def``'s own shallow parameter defaults, which evaluate in
that same enclosing scope.

Companion to :mod:`weld.tests.weld_python_callgraph_decorates_test`
(decorator_list attribution -- the other ADR 0122 class),
:mod:`weld.tests.weld_python_callgraph_forward_ref_test` (mirrors its
``_extract``/fixture style), and
:mod:`weld.tests.weld_python_callgraph_nested_scope_test` (the ADR 0122
amendment / bd z0fh: the same bounded walk applied to a directly-nested
def's own body, fixing a pre-existing double-count and resolving the
function-nested parameter-default deferral this module's
``test_module_level_default_of_nested_def_not_captured_at_file`` pins the
module-scope half of).
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
    root = Path(tempfile.mkdtemp(prefix="weld_scope_edges_"))
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


class ModuleLevelCallTest(unittest.TestCase):
    """Module-level statement calls -- sourced at ``file:``, not a new node."""

    def test_module_level_call_sourced_at_file_node(self) -> None:
        _, edges = _extract(
            """
            def load_config():
                return {}

            CONFIG = load_config()
            """
        )
        match = _edge(
            edges, "file:pkg/mod", "symbol:py:pkg.mod:load_config", "calls",
        )
        self.assertIsNotNone(
            match, f"module-level statement must produce a calls edge "
            f"sourced at file:pkg/mod: {edges}",
        )
        self.assertEqual(match["props"]["provenance"]["file"], "pkg/mod.py")

    def test_module_level_call_mints_file_anchor_when_absent(self) -> None:
        """python_callgraph alone (no python_module in this glob) must still
        keep the graph referentially closed -- a defensive file: stub."""
        nodes, _ = _extract(
            """
            def load_config():
                return {}

            CONFIG = load_config()
            """
        )
        self.assertIn("file:pkg/mod", nodes)
        self.assertEqual(nodes["file:pkg/mod"]["type"], "file")

    def test_module_level_call_deduplicated(self) -> None:
        _, edges = _extract(
            """
            def f():
                return 1

            A = f()
            B = f()
            """
        )
        matches = _edges(edges, "file:pkg/mod", "symbol:py:pkg.mod:f", "calls")
        self.assertEqual(
            len(matches), 1,
            f"two module-level calls to the same target must dedup to one "
            f"edge, matching the symbol-sourced calls loop: {edges}",
        )

    def test_module_level_default_of_nested_def_not_captured_at_file(self) -> None:
        """A def nested inside another function's own body binds a
        DIFFERENT enclosing scope than the module -- its default must not
        leak onto ``file:``. (Formerly deferred entirely per ADR 0122 item
        4; the ADR 0122 amendment / bd z0fh resolves the deferral by
        attributing this default to ``outer`` instead -- see
        ``NestedDefScopeTest.test_function_nested_default_attributes_to_enclosing_function``
        in this module.)
        """
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
        self.assertIsNone(
            _edge(edges, "file:pkg/mod", "symbol:py:pkg.mod:helper", "calls"),
            "a function-nested def's own parameter default belongs to its "
            "enclosing FUNCTION's scope, not the module's",
        )


class ClassBodyCallTest(unittest.TestCase):
    """Class-body statement calls -- sourced at the class's own symbol."""

    def test_class_body_call_sourced_at_class_symbol(self) -> None:
        _, edges = _extract(
            """
            def build():
                return {}

            class Registry:
                ENTRIES = build()
            """
        )
        match = _edge(
            edges, "symbol:py:pkg.mod:Registry", "symbol:py:pkg.mod:build", "calls",
        )
        self.assertIsNotNone(
            match, f"class-body statement must produce a calls edge "
            f"sourced at the class symbol: {edges}",
        )

    def test_method_call_not_swept_into_class_attribution(self) -> None:
        """Bounded walk: a method's own body call is attributed to the
        method, not (also) to the enclosing class -- the class-body sweep
        must stop at the nested FunctionDef boundary."""
        _, edges = _extract(
            """
            def helper():
                return 1

            class Widget:
                def build(self):
                    return helper()
            """
        )
        self.assertIsNone(
            _edge(edges, "symbol:py:pkg.mod:Widget",
                  "symbol:py:pkg.mod:helper", "calls"),
            "a method's call must not leak onto its class",
        )
        self.assertIsNotNone(
            _edge(edges, "symbol:py:pkg.mod:Widget.build",
                  "symbol:py:pkg.mod:helper", "calls"),
        )

    def test_shallow_parameter_default_attributes_to_class(self) -> None:
        """A class-direct method's own parameter default executes when the
        class body runs (building the method object), not when the method
        is later called -- so it attributes to the class, same scope as
        any other class-body statement."""
        _, edges = _extract(
            """
            def helper():
                return 1

            class Widget:
                def build(self, x=helper()):
                    return x
            """
        )
        match = _edge(
            edges, "symbol:py:pkg.mod:Widget", "symbol:py:pkg.mod:helper", "calls",
        )
        self.assertIsNotNone(
            match, f"a method's own parameter default must attribute to "
            f"the class: {edges}",
        )
        self.assertIsNone(
            _edge(edges, "symbol:py:pkg.mod:Widget.build",
                  "symbol:py:pkg.mod:helper", "calls"),
            "the default must not ALSO be attributed to the method itself "
            "-- it runs before the method is ever called",
        )


class ModuleLevelParameterDefaultTest(unittest.TestCase):
    """A module-direct def's own parameter default attributes to file:."""

    def test_shallow_parameter_default_attributes_to_file(self) -> None:
        _, edges = _extract(
            """
            def helper():
                return 1

            def f(x=helper()):
                return x
            """
        )
        match = _edge(edges, "file:pkg/mod", "symbol:py:pkg.mod:helper", "calls")
        self.assertIsNotNone(
            match, f"a module-level def's own parameter default must "
            f"attribute to file:: {edges}",
        )
        self.assertIsNone(
            _edge(edges, "symbol:py:pkg.mod:f", "symbol:py:pkg.mod:helper", "calls"),
            "the default must not ALSO be attributed to f itself -- it "
            "runs before f is ever called",
        )


class DeterminismTest(unittest.TestCase):
    """The new edge populations are a pure function of the parsed tree."""

    def test_repeated_extraction_is_identical(self) -> None:
        source = """
            from functools import lru_cache

            @lru_cache()
            def a():
                return b()

            CONFIG = a()

            class Registry:
                ENTRIES = a()
                def method(self, x=a()):
                    return x
            """
        first_nodes, first_edges = _extract(source)
        second_nodes, second_edges = _extract(source)
        self.assertEqual(sorted(first_nodes), sorted(second_nodes))
        self.assertEqual(
            sorted((e["from"], e["to"], e["type"]) for e in first_edges),
            sorted((e["from"], e["to"], e["type"]) for e in second_edges),
        )
        # At least one of each new edge kind is actually exercised, so this
        # determinism pin cannot silently degrade to a no-op.
        types = {e["type"] for e in first_edges}
        self.assertIn("decorates", types)
        self.assertIn("calls", types)


if __name__ == "__main__":
    unittest.main()
