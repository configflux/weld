"""A call reached through a re-export facade lands on the module that defines it.

The reported gap: ``weld/contract.py`` re-exports ``validate_graph`` from
``weld/_graph_doc_validators.py``, every real consumer imports it from the
facade because that is the documented public path, and
``wd callers symbol:py:weld._graph_doc_validators:validate_graph`` answered
"no callers". A control one line away in the same function -- a symbol imported
straight from its defining module -- resolved correctly, which is what isolated
the cause to the facade rather than to staleness or to ranking.

``python_callgraph`` resolves a call against the *calling* module's import
table (rule 2 in its own docstring), so ``from weld.contract import
validate_graph`` yields ``symbol:py:weld.contract:validate_graph``. That module
defines no such name, so nothing ever walked it and the id is minted as a
speculative ``make_resolved_target_node`` stub. The blast radius of a change to
a re-exported symbol therefore reads as empty -- for weld's own contract
module, and for any package that publishes a facade, which is an ordinary
Python layout rather than a weld peculiarity.

The fix is a closure pass, and closure time is the only place it can live: the
strategy walks one glob at a time and, incrementally, only the dirty files, so
it cannot see the facade's import table when it resolves the caller. What the
retarget must refuse to do -- and why re-running it is a no-op -- is next door
in ``weld_graph_closure_reexport_guards_test``.
"""

from __future__ import annotations

import unittest

from weld.tests._graph_closure_reexport_fixture import (
    call_edge,
    close,
    facade_graph,
    file_node,
    stub_node,
    symbol_node,
    targets,
)

DEFINER = "symbol:py:pkg.definer:widget"
FACADE_STUB = "symbol:py:pkg.facade:widget"
CALLER = "symbol:py:pkg.caller:run"


class FacadeRetargetTest(unittest.TestCase):
    """The reported shape, reduced to three files."""

    def setUp(self) -> None:
        self.nodes, self.edges = close(*facade_graph())

    def test_the_call_lands_on_the_defining_module(self) -> None:
        self.assertEqual(targets(self.edges, CALLER), [DEFINER])

    def test_the_facade_stub_no_longer_exists(self) -> None:
        """The issue's own acceptance check: the bogus id is gone, not merely bypassed.

        Leaving it would keep a symbol node claiming ``pkg.facade`` defines
        ``widget``, which is exactly the false statement the edge was making.
        """
        self.assertNotIn(FACADE_STUB, self.nodes)

    def test_the_definer_keeps_its_own_definite_node(self) -> None:
        """The retarget must not disturb the node it points at."""
        props = self.nodes[DEFINER]["props"]
        self.assertEqual(props["confidence"], "definite")
        self.assertEqual(props["file"], "pkg/definer.py")

    def test_the_edge_records_the_stub_it_replaced(self) -> None:
        """``reexport_to`` is what lets the next round undo and re-derive this.

        Without it a retargeted edge is indistinguishable from one the strategy
        minted straight at the definer, and the pass could not tell a still-valid
        retarget from one whose facade has since stopped re-exporting the name.
        """
        edge = next(e for e in self.edges if e["type"] == "calls")
        self.assertEqual(edge["props"]["reexport_to"], FACADE_STUB)

    def test_the_original_edge_props_survive(self) -> None:
        """A retarget moves an endpoint; it is not a re-mint."""
        edge = next(e for e in self.edges if e["type"] == "calls")
        self.assertEqual(edge["props"]["provenance"]["file"], "pkg/caller.py")
        self.assertEqual(edge["props"]["source_strategy"], "python_callgraph")


class FacadeChainTest(unittest.TestCase):
    """Two facades in front of one definer.

    A package ``__init__.py`` re-exporting from a module that itself re-exports
    is an ordinary layout (this repo has ``weld.impact`` in front of
    ``weld.impact_core``), so the walk follows a chain rather than one hop.
    """

    def setUp(self) -> None:
        nodes, edges = facade_graph(
            facade_imports=["pkg.middle"],
            extra_nodes={
                "file:pkg/middle": file_node("pkg/middle.py", ["pkg.definer"]),
            },
        )
        self.nodes, self.edges = close(nodes, edges)

    def test_the_chain_resolves_to_the_definer(self) -> None:
        self.assertEqual(targets(self.edges, CALLER), [DEFINER])

    def test_no_intermediate_stub_is_minted(self) -> None:
        """The walk reads import tables; it never invents a node for a hop."""
        self.assertNotIn("symbol:py:pkg.middle:widget", self.nodes)


class DecoratesSourceEndpointTest(unittest.TestCase):
    """``decorates`` puts the resolved target on the ``from`` side.

    ``make_resolved_target_node``'s five callers do not all point the same way:
    a decorator's resolved target is the edge SOURCE (target decorates the
    decorated symbol). A retarget keyed only on ``to`` would leave every
    decorator reached through a facade pointing at a stub.
    """

    def setUp(self) -> None:
        nodes, edges = facade_graph()
        edges.append(call_edge(FACADE_STUB, CALLER, "decorates"))
        self.nodes, self.edges = close(nodes, edges)

    def test_the_decorator_is_sourced_at_the_definer(self) -> None:
        sources = sorted(
            str(e["from"]) for e in self.edges if e["type"] == "decorates"
        )
        self.assertEqual(sources, [DEFINER])

    def test_the_edge_records_the_stub_on_the_from_side(self) -> None:
        edge = next(e for e in self.edges if e["type"] == "decorates")
        self.assertEqual(edge["props"]["reexport_from"], FACADE_STUB)
        self.assertNotIn("reexport_to", edge["props"])


class OtherEdgeKindsTest(unittest.TestCase):
    """Every edge kind that can reach the same stub is retargeted with it.

    The stub id is shared across the five python_* emitters, so retargeting
    only ``calls`` would delete a node that ``inherits`` and ``references``
    edges still point at -- and the dangling-edge sweep would then drop them
    outright, turning a wrong edge into no edge.
    """

    def setUp(self) -> None:
        nodes, edges = facade_graph()
        edges.append(call_edge(CALLER, FACADE_STUB, "inherits"))
        edges.append(call_edge(CALLER, FACADE_STUB, "references"))
        self.nodes, self.edges = close(nodes, edges)

    def test_inherits_and_references_follow_the_calls_edge(self) -> None:
        for kind in ("calls", "inherits", "references"):
            with self.subTest(edge_type=kind):
                self.assertEqual(targets(self.edges, CALLER, kind), [DEFINER])

    def test_the_stub_is_removed_only_once_every_edge_moved(self) -> None:
        self.assertNotIn(FACADE_STUB, self.nodes)
        self.assertFalse([e for e in self.edges if FACADE_STUB in (e["from"], e["to"])])


class FileAnchoredCallerTest(unittest.TestCase):
    """A module-scope call is sourced at the caller's ``file:`` node.

    ADR 0122 sources a module-level statement's ``calls`` edge at the file
    rather than at a symbol, so the retarget must not assume a symbol on the
    other end of the edge it moves.
    """

    def setUp(self) -> None:
        nodes, edges = facade_graph()
        edges.append(call_edge("file:pkg/caller", FACADE_STUB))
        self.nodes, self.edges = close(nodes, edges)

    def test_the_module_scope_call_lands_on_the_definer(self) -> None:
        self.assertEqual(targets(self.edges, "file:pkg/caller"), [DEFINER])


class ClassQualnameTest(unittest.TestCase):
    """A re-exported method target keeps its dotted qualname intact.

    ``symbol:py:<module>:<qualname>`` splits at most three times precisely so a
    ``Class.method`` qualname survives; a retarget that rebuilt the id by
    splitting on the last colon would silently rewrite the qualname to
    ``method`` and land on a different node.
    """

    def setUp(self) -> None:
        nodes, edges = facade_graph(
            extra_nodes={
                "symbol:py:pkg.definer:Widget.build": symbol_node(
                    "pkg.definer", "Widget.build", "pkg/definer.py"
                ),
                "symbol:py:pkg.facade:Widget.build": stub_node(
                    "pkg.facade", "Widget.build"
                ),
            },
        )
        edges.append(call_edge(CALLER, "symbol:py:pkg.facade:Widget.build"))
        self.nodes, self.edges = close(nodes, edges)

    def test_the_dotted_qualname_resolves(self) -> None:
        self.assertEqual(
            targets(self.edges, CALLER),
            ["symbol:py:pkg.definer:Widget.build", DEFINER],
        )


if __name__ == "__main__":
    unittest.main()
