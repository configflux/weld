"""What the re-export walk must refuse to do.

The retarget in ``weld_graph_closure_reexport_test`` reads a facade's
``imports_from`` -- a list of module names, not the name-level import table --
so it infers which import carried the symbol. These are the bounds that make
that inference safer than the stub it replaces rather than a worse defect: it
must not guess between two modules that both define the name, must not walk an
import cycle forever or an arbitrarily long chain, must never touch a stub whose
module is stdlib or external (where "the facade re-exports it" is not a
statement this graph can make), and must not fall over on the malformed props a
strategy plugin or a hand-edited graph can hand it.

The other half of the pass -- the undo that lets a retargeted edge be re-derived
rather than inherited, and the collision repair that comes with it -- is bounded
in ``weld_graph_closure_reexport_edges_test``, mirroring the split between
``weld._graph_closure_reexport`` and ``weld._graph_closure_reexport_edges``.
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


class AmbiguousFacadeTest(unittest.TestCase):
    """Two imported modules define the name; the facade re-exported one of them.

    ``imports_from`` cannot say which, so the walk refuses rather than picking
    the alphabetically-first module and stating it as fact. A stub is a visible
    "not resolved"; a confidently wrong edge is not.
    """

    def setUp(self) -> None:
        nodes, edges = facade_graph(
            facade_imports=["pkg.definer", "pkg.rival"],
            extra_nodes={
                "file:pkg/rival": file_node("pkg/rival.py"),
                "symbol:py:pkg.rival:widget": symbol_node(
                    "pkg.rival", "widget", "pkg/rival.py"
                ),
            },
        )
        self.nodes, self.edges = close(nodes, edges)

    def test_the_stub_survives(self) -> None:
        self.assertIn(FACADE_STUB, self.nodes)

    def test_the_edge_is_untouched(self) -> None:
        self.assertEqual(targets(self.edges, CALLER), [FACADE_STUB])
        edge = next(e for e in self.edges if e["type"] == "calls")
        self.assertNotIn("reexport_to", edge["props"])


class NearerHopWinsTest(unittest.TestCase):
    """Ambiguity is judged per level, not across the whole reachable set.

    A facade that imports the definer directly *and* imports some unrelated
    module which itself re-exports the same name is not ambiguous: Python binds
    the name from the facade's own import, and the deeper reading only exists
    because the walk went looking. Refusing here would lose the common case to
    a hypothetical one.
    """

    def setUp(self) -> None:
        nodes, edges = facade_graph(
            facade_imports=["pkg.definer", "pkg.middle"],
            extra_nodes={
                "file:pkg/middle": file_node("pkg/middle.py", ["pkg.rival"]),
                "file:pkg/rival": file_node("pkg/rival.py"),
                "symbol:py:pkg.rival:widget": symbol_node(
                    "pkg.rival", "widget", "pkg/rival.py"
                ),
            },
        )
        self.nodes, self.edges = close(nodes, edges)

    def test_the_direct_import_wins(self) -> None:
        self.assertEqual(targets(self.edges, CALLER), [DEFINER])


class ImportCycleTest(unittest.TestCase):
    """A cycle in the import graph terminates instead of walking forever."""

    def setUp(self) -> None:
        nodes, edges = facade_graph(
            facade_imports=["pkg.middle"],
            extra_nodes={
                "file:pkg/middle": file_node("pkg/middle.py", ["pkg.facade"]),
            },
        )
        self.nodes, self.edges = close(nodes, edges)

    def test_the_walk_terminates_and_resolves_nothing(self) -> None:
        self.assertIn(FACADE_STUB, self.nodes)
        self.assertEqual(targets(self.edges, CALLER), [FACADE_STUB])

    def test_a_cycle_that_reaches_the_definer_still_resolves(self) -> None:
        nodes, edges = facade_graph(
            facade_imports=["pkg.middle"],
            extra_nodes={
                "file:pkg/middle": file_node(
                    "pkg/middle.py", ["pkg.facade", "pkg.definer"]
                ),
            },
        )
        _nodes, edges = close(nodes, edges)
        self.assertEqual(targets(edges, CALLER), [DEFINER])


class ChainDepthTest(unittest.TestCase):
    """The walk is bounded, and the bound is a real refusal, not a crash.

    Each extra hop is another inference stacked on the last, so the walk stops
    well short of "anything transitively importable" -- on this repo every real
    facade resolves at the first hop, and the allowance beyond that is for the
    package-``__init__`` chains, not for a search.
    """

    @staticmethod
    def _chain(length: int) -> tuple[dict, list]:
        """A facade in front of *length* - 1 relays in front of the definer."""
        hops = [f"pkg.hop{i}" for i in range(1, length)]
        extra: dict[str, dict] = {}
        for index, hop in enumerate(hops):
            nxt = hops[index + 1] if index + 1 < len(hops) else "pkg.definer"
            extra[f"file:pkg/hop{index + 1}"] = file_node(
                f"pkg/hop{index + 1}.py", [nxt]
            )
        return facade_graph(
            facade_imports=[hops[0]] if hops else ["pkg.definer"],
            extra_nodes=extra,
        )

    def test_a_chain_at_the_bound_resolves(self) -> None:
        _nodes, edges = close(*self._chain(3))
        self.assertEqual(targets(edges, CALLER), [DEFINER])

    def test_a_chain_past_the_bound_is_refused(self) -> None:
        nodes, edges = close(*self._chain(4))
        self.assertIn(FACADE_STUB, nodes)
        self.assertEqual(targets(edges, CALLER), [FACADE_STUB])


class NonFirstPartyTest(unittest.TestCase):
    """Only a module the graph holds as a file node can be read as a facade.

    A stdlib or third-party stub is the shape ``make_resolved_target_node``
    exists for, and this graph holds no import table for those modules -- so
    there is nothing to follow and nothing to infer from.
    """

    def test_a_stdlib_stub_is_untouched(self) -> None:
        nodes, edges = facade_graph(
            extra_nodes={"symbol:py:json:dumps": stub_node("json", "dumps", "stdlib")},
        )
        edges.append(call_edge(CALLER, "symbol:py:json:dumps"))
        nodes, edges = close(nodes, edges)
        self.assertIn("symbol:py:json:dumps", nodes)
        self.assertIn("symbol:py:json:dumps", targets(edges, CALLER))

    def test_a_facade_importing_only_stdlib_resolves_nothing(self) -> None:
        nodes, edges = close(*facade_graph(facade_imports=["json", "re"]))
        self.assertIn(FACADE_STUB, nodes)
        self.assertEqual(targets(edges, CALLER), [FACADE_STUB])


class WalkedModuleTest(unittest.TestCase):
    """A module that really defines the name is never a stub to begin with.

    The fingerprint is the whole guard here: a walked symbol carries
    ``props.file`` and ``confidence: definite``, so a module that defines what
    it is being asked for cannot be mistaken for a facade re-exporting it.
    """

    def test_a_definite_target_is_left_where_it_is(self) -> None:
        nodes, edges = facade_graph(
            extra_nodes={
                FACADE_STUB: symbol_node("pkg.facade", "widget", "pkg/facade.py"),
            },
        )
        nodes, edges = close(nodes, edges)
        self.assertIn(FACADE_STUB, nodes)
        self.assertEqual(targets(edges, CALLER), [FACADE_STUB])


class HostileShapeTest(unittest.TestCase):
    """Malformed nodes and edges are stepped over, not crashed on.

    ``props`` reaches the closure from strategy plugins, including project-local
    overrides under ``.weld/strategies/``, and the prior graph reaches it from a
    file on disk. Both are untrusted *shape*. The isinstance guards through this
    pass are only worth having if they are executed, so they are, rather than
    read.
    """

    def test_a_graph_of_wrong_shapes_still_retargets(self) -> None:
        nodes, edges = facade_graph()
        nodes["symbol:py:junk:a"] = {"type": "symbol", "props": None}
        nodes["symbol:py:junk:b"] = {"type": "symbol", "props": {
            "module": 7, "qualname": ["x"], "confidence": "speculative",
            "authority": "derived", "source_strategy": "python_callgraph",
        }}
        nodes["file:junk"] = {"type": "file", "props": {
            "file": "junk/mod.py", "imports_from": "not-a-list",
        }}
        nodes["file:junk2"] = {"type": "file", "props": {
            "file": "junk2/mod.py", "imports_from": [None, 3, "  ", "pkg.definer"],
        }}
        edges.append({"from": CALLER, "to": FACADE_STUB, "type": "calls"})
        edges.append({"from": CALLER, "to": FACADE_STUB, "type": "calls", "props": []})
        nodes, edges = close(nodes, edges)
        self.assertEqual(targets(edges, CALLER), [DEFINER])
        self.assertNotIn(FACADE_STUB, nodes)

    def test_a_dot_dot_file_prop_resolves_to_nothing(self) -> None:
        """``props.file`` is a string to derive a module name from, never a path to open."""
        nodes, edges = facade_graph(facade_imports=["../../etc/passwd"])
        nodes, edges = close(nodes, edges)
        self.assertIn(FACADE_STUB, nodes)
        self.assertEqual(targets(edges, CALLER), [FACADE_STUB])




if __name__ == "__main__":
    unittest.main()
