"""The deferred-attribute-call retarget undoes its own output before re-deriving.

Every other rule in ``close_graph`` re-derives from node props each round and is
self-correcting for free. This one moves an endpoint on a *retained* edge, and
an incremental refresh never re-walks a clean caller -- so a move made in an
earlier round would simply be inherited, however the module it was justified by
has changed since. Deleting ``lib/inner.py`` has to degrade both discover paths
to the sentinel, and on the incremental path nothing dangles to force a re-walk:
the endpoint may still name a stub that is present, or the re-export walk may
have carried it on to a definition in a third module that is still there.

Unlike the re-export retarget, this one records no bookkeeping key. The endpoint
it replaced is ``symbol:unresolved:<attr>``, a pure function of the hint the
edge still carries, so the whole pass is a function of that hint plus the
current graph -- which is what makes a second run a no-op and a changed graph a
re-derivation rather than an inheritance.

The rounds here are written as a *retained* edge -- one that already carries the
previous round's answer -- fed to a graph that has since changed, because that
is the only state the incremental path can be in that a full discover cannot.
"""

from __future__ import annotations

import unittest

from weld.strategies._python_import_attr import IMPORT_ATTR_PROP
from weld.tests._graph_closure_import_attr_fixture import (
    CALLER,
    RESOLVED,
    SENTINEL,
    close,
    cross_glob_graph,
    deferred_edge,
    one,
    sentinel_node,
    targets,
)


def _retained_edge(dst: str = RESOLVED, **kwargs) -> dict:
    """The edge as an earlier round left it.

    Already moved onto the submodule symbol, still carrying the hint that
    justified the move -- the one state the incremental path can be in that a
    full discover cannot.
    """
    return deferred_edge(dst=dst, resolved=True, **kwargs)


class RerunIsANoOpTest(unittest.TestCase):
    """A round whose graph has not changed reaches the same place."""

    def test_a_retained_edge_re_derives_to_the_same_target(self) -> None:
        nodes, _edges = cross_glob_graph()
        nodes, edges = close(nodes, [_retained_edge()])
        self.assertEqual(targets(edges, CALLER), [RESOLVED])
        self.assertTrue(one(edges, CALLER)["props"]["resolved"])

    def test_closing_twice_changes_nothing(self) -> None:
        nodes, edges = close(*cross_glob_graph())
        first = one(edges, CALLER)["props"].copy()
        nodes, edges = close(nodes, edges)
        self.assertEqual(one(edges, CALLER)["props"], first)


class DefinerDeletedTest(unittest.TestCase):
    """The round the undo exists for: the module is gone, the edge is not."""

    def setUp(self) -> None:
        nodes, _edges = cross_glob_graph(definer=False)
        self.nodes, self.edges = close(nodes, [_retained_edge()])

    def test_the_endpoint_returns_to_the_sentinel(self) -> None:
        self.assertEqual(targets(self.edges, CALLER), [SENTINEL])

    def test_the_edge_props_return_with_it(self) -> None:
        """A restored endpoint that still read ``resolved`` would be a third state."""
        props = one(self.edges, CALLER)["props"]
        self.assertFalse(props["resolved"])
        self.assertEqual(props["confidence"], "speculative")
        self.assertEqual(props["resolution"], "unresolved")

    def test_the_sentinel_node_is_re_minted(self) -> None:
        """Exactly what the strategy would emit, not a third shape."""
        self.assertEqual(self.nodes[SENTINEL], sentinel_node("work"))

    def test_the_stale_target_is_not_left_behind(self) -> None:
        self.assertNotIn(RESOLVED, self.nodes)


class BuiltinAttrTest(unittest.TestCase):
    """A restored sentinel for a builtin name keeps the strategy's own tags."""

    def test_a_builtin_named_attr_restores_as_builtin(self) -> None:
        nodes, _edges = cross_glob_graph(definer=False)
        nodes.pop(SENTINEL)
        nodes, edges = close(
            nodes, [_retained_edge(attr="open", dst="symbol:py:lib.inner:open")]
        )
        props = one(edges, CALLER)["props"]
        self.assertEqual(props["resolution"], "builtin")
        self.assertEqual(
            nodes["symbol:unresolved:open"], sentinel_node("open", "builtin")
        )


class StaleReexportBookkeepingTest(unittest.TestCase):
    """A re-export record sitting on top of this pass's move is void once it moves.

    The re-export walk only ever retargets a speculative stub, so a record on a
    hinted edge can only describe a chain that started here. Left in place, it
    would put the endpoint back on a stub -- and re-mint that stub -- on a round
    where this rule has already said the module is gone, which is a state no
    full discover produces.
    """

    def test_the_record_is_dropped_when_the_endpoint_moves(self) -> None:
        nodes, _edges = cross_glob_graph(definer=False)
        edge = _retained_edge(dst="symbol:py:lib.core:work")
        edge["props"]["reexport_to"] = RESOLVED
        nodes, edges = close(nodes, [edge])
        self.assertEqual(targets(edges, CALLER), [SENTINEL])
        self.assertNotIn("reexport_to", one(edges, CALLER)["props"])
        self.assertNotIn(RESOLVED, nodes)


class SentinelDropIsReferenceCountedTest(unittest.TestCase):
    """The sentinel id is a bare-name namespace shared across every strategy."""

    def test_another_referrer_keeps_the_sentinel_alive(self) -> None:
        nodes, edges = cross_glob_graph()
        nodes["symbol:py:tools.go:other"] = dict(nodes[CALLER])
        edges.append(
            {
                "from": "symbol:py:tools.go:other",
                "to": SENTINEL,
                "type": "calls",
                "props": {"source_strategy": "python_callgraph"},
            }
        )
        nodes, edges = close(nodes, edges)
        self.assertEqual(targets(edges, CALLER), [RESOLVED])
        self.assertIn(SENTINEL, nodes)


class CollisionCollapseTest(unittest.TestCase):
    """Two ways to reach one target must collapse the same way on both paths.

    A caller that reaches ``lib.inner.work`` both through the submodule import
    and through a direct one ends up with two identical ``(from, to, type)``
    triples once this pass moves the first. The dedup downstream keeps whichever
    comes first in list order, and a retained edge and a freshly emitted one do
    not arrive in the same order on the two discover paths -- so the choice is
    made here, on content, and the member carrying the hint wins so the next
    round can still undo.
    """

    def _closed(self, reverse: bool) -> list[dict]:
        nodes, _edges = cross_glob_graph()
        direct = {
            "from": CALLER,
            "to": RESOLVED,
            "type": "calls",
            "props": {
                "source_strategy": "python_callgraph",
                "confidence": "definite",
                "resolved": True,
                "raw": "work",
                "resolution": "import",
                "provenance": {"file": "tools/go.py", "line": 9},
            },
        }
        pair = [deferred_edge(), direct]
        if reverse:
            pair.reverse()
        _nodes, edges = close(nodes, pair)
        return [e for e in edges if e["type"] == "calls" and e["from"] == CALLER]

    def test_one_edge_survives_whatever_the_input_order(self) -> None:
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                self.assertEqual(len(self._closed(reverse)), 1)

    def test_the_survivor_keeps_the_hint(self) -> None:
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                self.assertIn(IMPORT_ATTR_PROP, self._closed(reverse)[0]["props"])

    def test_both_orders_agree_on_the_survivor(self) -> None:
        self.assertEqual(self._closed(False), self._closed(True))


class DecoratesSideTest(unittest.TestCase):
    """A ``decorates`` edge runs decorator -> decorated, so its target is ``from``."""

    def test_the_from_endpoint_is_the_one_that_moves(self) -> None:
        nodes, _edges = cross_glob_graph()
        edge = deferred_edge(
            src=SENTINEL, dst=CALLER, side="from", edge_type="decorates"
        )
        _nodes, edges = close(nodes, [edge])
        moved = [e for e in edges if e["type"] == "decorates"]
        self.assertEqual([e["from"] for e in moved], [RESOLVED])
        self.assertEqual([e["to"] for e in moved], [CALLER])


if __name__ == "__main__":
    unittest.main()
