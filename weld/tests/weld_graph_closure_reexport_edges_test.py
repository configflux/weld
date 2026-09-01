"""The undo that keeps a retargeted edge re-derivable, and its collision repair.

``weld._graph_closure_reexport_edges`` is the only part of ``close_graph`` that
mutates a RETAINED edge. Every other rule re-derives from node props each round
and is self-correcting for free; a moved endpoint is simply inherited, however
the facade or the definition has changed since. So the pass strips its own prior
output -- ``props.reexport_to`` / ``props.reexport_from`` -- and re-mints the
stub before the walk re-derives, the same discipline ``link_producers_consumers``
takes in the same post-processing run.

Which incremental round that is actually load-bearing for is measured over the
real discover path in ``incremental_reexport_equivalence_test``. Here the
mechanism is exercised on its own terms, in isolation from the incremental
machinery, because it has to be right whatever else happens to re-run: the
round trip must land exactly where it started, a chain that stops resolving must
end at the state a full discover produces rather than a third state of its own,
and a recorded id that this pass could not have written must be refused rather
than minted as a node.

What the walk in front of it must refuse to infer is next door, in
``weld_graph_closure_reexport_guards_test``.
"""

from __future__ import annotations

import copy
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


def _triples(edges: list[dict]) -> set[tuple[str, str, str]]:
    """The deduplicated ``(from, to, type)`` set the graph is finally written as."""
    return {(str(e["from"]), str(e["to"]), str(e["type"])) for e in edges}


class IdempotenceTest(unittest.TestCase):
    """Re-running the closure over its own output retargets nothing further.

    Compared on the deduplicated triples the graph is finally written as, not
    on the raw list: ``close_graph`` as a whole re-appends its ``contains`` and
    ``depends_on`` edges on a second call and leaves the duplicates for the
    dedup sweep in ``post_process``. That is pre-existing and orthogonal. What
    has to hold here is that the undo-then-re-derive round trip lands exactly
    where it started -- same nodes, same endpoints, same bookkeeping.
    """

    def setUp(self) -> None:
        self.nodes, self.edges = close(*facade_graph())
        self.before_nodes = copy.deepcopy(self.nodes)
        self.before_edge = copy.deepcopy(
            next(e for e in self.edges if e["type"] == "calls")
        )
        self.before_triples = _triples(self.edges)
        close(self.nodes, self.edges)

    def test_the_node_set_is_unchanged(self) -> None:
        self.assertEqual(self.nodes, self.before_nodes)

    def test_the_edge_endpoints_are_unchanged(self) -> None:
        self.assertEqual(_triples(self.edges), self.before_triples)

    def test_the_retargeted_edge_is_byte_identical(self) -> None:
        after = next(e for e in self.edges if e["type"] == "calls")
        self.assertEqual(after, self.before_edge)


class RestoreTest(unittest.TestCase):
    """When the chain stops resolving, the retarget is undone, not stranded.

    A retargeted edge that outlives the reason it was retargeted is the whole
    hazard: nothing else in ``close_graph`` reads it, so without ``reexport_to``
    there is no way to tell it from an edge the strategy minted straight at the
    definition. The undo reproduces exactly what a full discover of the same
    graph emits -- the stub, with the edge still on it.
    """

    def setUp(self) -> None:
        self.nodes, self.edges = close(*facade_graph())
        del self.nodes[DEFINER]
        del self.nodes["file:pkg/definer"]
        self.nodes, self.edges = close(self.nodes, self.edges)

    def test_the_edge_goes_back_to_the_stub(self) -> None:
        self.assertEqual(targets(self.edges, CALLER), [FACADE_STUB])

    def test_the_stub_node_is_re_minted(self) -> None:
        self.assertEqual(self.nodes[FACADE_STUB], stub_node("pkg.facade", "widget"))

    def test_the_bookkeeping_prop_is_dropped(self) -> None:
        edge = next(e for e in self.edges if e["type"] == "calls")
        self.assertNotIn("reexport_to", edge["props"])


class RederiveTest(unittest.TestCase):
    """A moved definition is followed, not left pointing at the old module."""

    def setUp(self) -> None:
        self.nodes, self.edges = close(*facade_graph())
        del self.nodes[DEFINER]
        del self.nodes["file:pkg/definer"]
        self.nodes["file:pkg/facade"]["props"]["imports_from"] = ["pkg.rival"]
        self.nodes["file:pkg/rival"] = file_node("pkg/rival.py")
        self.nodes["symbol:py:pkg.rival:widget"] = symbol_node(
            "pkg.rival", "widget", "pkg/rival.py"
        )
        self.nodes, self.edges = close(self.nodes, self.edges)

    def test_the_edge_follows_the_definition(self) -> None:
        self.assertEqual(targets(self.edges, CALLER), ["symbol:py:pkg.rival:widget"])

    def test_the_recorded_stub_is_unchanged(self) -> None:
        """The prop names the facade, not the destination, so it survives a move."""
        edge = next(e for e in self.edges if e["type"] == "calls")
        self.assertEqual(edge["props"]["reexport_to"], FACADE_STUB)


class MalformedBookkeepingTest(unittest.TestCase):
    """A recorded stub id that is not one this pass could have written is dropped.

    ``reexport_to`` is read back off ``.weld/graph.json``, a plain file on disk
    that a hand edit, a partial write, or a bug can leave anything in. Restoring
    an endpoint onto whatever it says would mint a node under an arbitrary id --
    the one thing a closure pass must never do, since every later rule reads the
    node set as fact. Dropping the bookkeeping instead degrades to "this edge
    has no undo", which the next full discover repairs.
    """

    def _closed(self, recorded: str) -> tuple[dict, list]:
        nodes, edges = facade_graph()
        edges[0]["props"]["reexport_to"] = recorded
        return close(nodes, edges)

    def test_a_junk_id_is_not_minted_as_a_node(self) -> None:
        for recorded in ("", "not-an-id", "symbol:py:", "symbol:py:mod", "file:pkg/x"):
            with self.subTest(recorded=recorded):
                nodes, _edges = self._closed(recorded)
                self.assertNotIn(recorded, nodes)

    def test_the_junk_bookkeeping_does_not_survive(self) -> None:
        """Left in place it would be re-read, and re-rejected, every round.

        What replaces it here is the real stub id, because this fixture's facade
        still resolves and the edge is retargeted for real in the same pass --
        which is the point: rejecting a bad record clears it without taking the
        edge out of service.
        """
        _nodes, edges = self._closed("not-an-id")
        edge = next(e for e in edges if e["type"] == "calls")
        self.assertEqual(edge["props"]["reexport_to"], FACADE_STUB)

    def test_the_real_retarget_still_happens(self) -> None:
        """Rejecting the bookkeeping must not disable the pass for that edge."""
        _nodes, edges = self._closed("not-an-id")
        self.assertEqual(targets(edges, CALLER), [DEFINER])


class DuplicateCollapseTest(unittest.TestCase):
    """A retarget that collides with an existing edge collapses deterministically.

    A caller that reaches the same function both through the facade and
    directly ends up with two identical ``(from, to, type)`` triples carrying
    different provenance. The generic dedup downstream keeps whichever comes
    first in list order, and full and incremental discovery do not agree on
    that order -- so the collapse is resolved here, on edge content, before it
    can become a byte difference between the two paths.
    """

    @staticmethod
    def _collapsed(reverse: bool) -> list[dict]:
        nodes, edges = facade_graph()
        direct = call_edge(CALLER, DEFINER)
        direct["props"]["provenance"] = {"file": "pkg/caller.py", "line": 11}
        edges.append(direct)
        if reverse:
            edges.reverse()
        _nodes, edges = close(nodes, edges)
        return [e for e in edges if e["type"] == "calls"]

    def test_one_edge_survives(self) -> None:
        self.assertEqual(len(self._collapsed(reverse=False)), 1)

    def test_the_survivor_does_not_depend_on_input_order(self) -> None:
        self.assertEqual(self._collapsed(reverse=False), self._collapsed(reverse=True))


if __name__ == "__main__":
    unittest.main()
