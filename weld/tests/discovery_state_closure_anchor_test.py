"""A closure-derived edge does not anchor a placeholder (bd 5038-rwi34).

The unit half of that pin;
``incremental_closure_anchored_stub_equivalence_test`` is the end-to-end half,
and both read the one cast in :mod:`weld.tests._closure_anchor_fixture`.

:mod:`weld._discover_placeholder_anchor` answers "does any surviving edge
still reference this never-walked placeholder?", and bd 5038-q4t3d's
correction rests on an argument about *authorship*: a placeholder carries no
``props.file``, so it never authored an edge on its own behalf, and every edge
naming it is the other endpoint's file still referencing it. True of every
strategy-authored edge. False of one :mod:`weld.graph_closure` authored --
``_link_imports`` re-derives a ``depends_on`` each round by looking an
importer's ``imports_from`` name up in ``_module_index``, an index the
placeholder is itself a member of, so the edge is an echo of the placeholder
rather than evidence for it.

The cases below pin that on the producers' own output. Nothing here writes a
node or an edge: one real full ``_discover_single_repo`` over the fixture tree
supplies both the closure edge and the strategy-authored ``calls`` edge, and
the two are then handed to the predicate and to bd n4nvt's rule directly. A
sibling file (``discovery_state_placeholder_anchor_test``) pins the same
module's direction rule over a different cast; the two are separate because
each needs a tree the other's assertions would be vacuous on, not because the
predicate is.
"""

from __future__ import annotations

import tempfile
import unittest
from functools import lru_cache
from pathlib import Path

from weld._discover_external_package_purge import emptied_placeholder_node_ids
from weld._discover_placeholder_anchor import edge_anchored_node_ids
from weld._discover_resolved_stub_purge import emptied_resolved_stub_node_ids
from weld.discover import _discover_single_repo
from weld.tests._closure_anchor_fixture import (
    CLEAN_CONSUMER,
    SOLE_IMPORTER_SYMBOL,
    STUB_ID,
    commit,
    git_init,
    write,
)


@lru_cache(maxsize=None)
def _discovered() -> tuple[dict[str, dict], tuple[dict, ...]]:
    """One real full discover of the intact tree, shared by every case.

    A full discover is a pure function of the tree it walks, so a single run
    serves every assertion here and the temp tree does not outlive it.
    """
    with tempfile.TemporaryDirectory(prefix="rwi34-unit-") as td:
        root = Path(td)
        git_init(root)
        write(root)
        commit(root)
        graph = _discover_single_repo(root, incremental=False, write_graph=True)
    return graph["nodes"], tuple(graph["edges"])


def _graph() -> tuple[dict[str, dict], list[dict]]:
    nodes, edges = _discovered()
    return dict(nodes), [dict(e) for e in edges]


def _naming_the_stub(edges: list[dict], strategy: str) -> list[dict]:
    """Every *strategy*-authored edge naming the stub, in graph order."""
    return [
        e for e in edges
        if STUB_ID in (e["from"], e["to"])
        and (e.get("props") or {}).get("source_strategy") == strategy
    ]


def _without_strategy_anchors(edges: list[dict]) -> list[dict]:
    """The real edge set with every strategy-authored anchor of the stub gone.

    The condition the rule is being asked about, reached by subtraction from
    the producer's own output rather than by re-implementing the purge: what
    survives is a real graph in which the only thing naming the stub is
    closure-authored. The end-to-end half runs the actual deletion round.

    Subtracted by identity, not by value: two edges can carry identical content
    and dropping the wrong one would quietly change what the cases below are
    asking about.
    """
    dropped = {id(e) for e in _naming_the_stub(edges, "python_callgraph")}
    return [e for e in edges if id(e) not in dropped]


class ProducerFixtureTest(unittest.TestCase):
    """The cast is only evidence if it really mints both kinds of edge.

    A silent change in what the closure or ``python_callgraph`` emits here
    would otherwise leave every case below asserting over an empty list. This
    is the non-vacuity check, not a claim about the anchor rule.
    """

    def test_the_stub_is_named_by_edges_of_both_authorships(self) -> None:
        """One ``calls`` edge from the minter, and a closure ``depends_on``
        from each importer of the module name -- including the clean consumer
        that never mentioned ``fn_alpha``, which is the whole finding.
        """
        _, edges = _graph()
        self.assertEqual(
            sorted((e["from"], e["type"])
                   for e in _naming_the_stub(edges, "graph_closure")),
            [(CLEAN_CONSUMER, "depends_on"), ("file:beta/use", "depends_on")],
        )
        self.assertEqual(
            sorted((e["from"], e["type"])
                   for e in _naming_the_stub(edges, "python_callgraph")),
            [(SOLE_IMPORTER_SYMBOL, "calls")],
        )


class ClosureEdgeDoesNotAnchorTest(unittest.TestCase):
    """The predicate itself, over the two authorships side by side."""

    def test_a_strategy_authored_edge_still_anchors(self) -> None:
        """The rule narrowed by authorship, not by anything else: the
        ``calls`` edge alone is enough to keep the stub, exactly as bd n4nvt
        and bd 5038-q4t3d require.
        """
        _, edges = _graph()
        anchored = edge_anchored_node_ids(_naming_the_stub(edges, "python_callgraph"))
        self.assertIn(STUB_ID, anchored)

    def test_a_closure_authored_edge_alone_anchors_nothing(self) -> None:
        _, edges = _graph()
        closure = _naming_the_stub(edges, "graph_closure")
        self.assertEqual(edge_anchored_node_ids(closure), set())

    def test_an_edge_of_unknown_authorship_still_anchors(self) -> None:
        """Defensive, and on the safe side: an edge whose ``props`` a
        project-local strategy under ``.weld/strategies/`` left missing or
        malformed keeps anchoring, which retains a node rather than purging
        one. Mirrors the sibling purge modules' posture (bd oao53, bd n4nvt).

        The second loop is the one whose cases would otherwise *raise* rather
        than answer: these props are read back off ``.weld/graph.json``, which
        ADR 0115 treats as unvetted repo text, and an unhashable value there
        would blow up the membership test on the way past.
        """
        _, edges = _graph()
        closure = _naming_the_stub(edges, "graph_closure")[0]
        for props in (None, [], "graph_closure", {}):
            with self.subTest(props=props):
                edge = dict(closure, props=props)
                self.assertIn(STUB_ID, edge_anchored_node_ids([edge]))
        for strategy in ([], ["graph_closure"], {"graph_closure": 1}, 7, None):
            with self.subTest(source_strategy=strategy):
                edge = dict(closure, props={"source_strategy": strategy})
                self.assertIn(STUB_ID, edge_anchored_node_ids([edge]))


class ResolvedStubPurgeUsesTheNarrowedAnchorTest(unittest.TestCase):
    """bd n4nvt's rule, on the state the incremental round actually reaches.

    Deleting the sole importer leaves exactly the closure edge behind, which
    is the input reproduced here -- the rule must now call the stub emptied,
    where before it read that edge as a live reference and kept it.
    """

    def test_the_stub_is_emptied_when_only_closure_edges_remain(self) -> None:
        nodes, edges = _graph()
        self.assertEqual(
            emptied_resolved_stub_node_ids(nodes, _without_strategy_anchors(edges)),
            {STUB_ID},
        )

    def test_the_stub_survives_while_its_calls_edge_does(self) -> None:
        """The other direction of the same rule: this narrows what counts as
        an anchor, it does not make the rule purge unconditionally. Pins
        bd n4nvt's original contract as still live (ADR 0139: an invariant is
        never weakened so the new state passes).
        """
        nodes, edges = _graph()
        self.assertEqual(emptied_resolved_stub_node_ids(nodes, edges), set())

    def test_the_union_entry_point_agrees_on_both_states(self) -> None:
        """:mod:`weld.discovery_state` calls the union, not either rule, so
        the verdict that actually reaches ``purge_stale_nodes`` is asserted
        rather than inferred from the rule in isolation.
        """
        nodes, edges = _graph()
        self.assertEqual(emptied_placeholder_node_ids(nodes, edges), set())
        self.assertEqual(
            emptied_placeholder_node_ids(nodes, _without_strategy_anchors(edges)),
            {STUB_ID},
        )


if __name__ == "__main__":
    unittest.main()
