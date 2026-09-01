"""Contract for :func:`weld.tests._graph_invariants.assert_no_orphan_stubs`.

ADR 0139 mechanism 6 (bd 5038-ekohj). The ADR does not
merely permit deriving this invariant from
:func:`weld._discover_external_package_purge.emptied_placeholder_node_ids`, it
*requires* it: "the orphan-stub invariant is a thin assertion over the
production union ..., not a sixth hand-written predicate ... a future sixth rule
joins the invariant for free". So the load-bearing test in this file is not any
one placeholder shape -- it is
:meth:`DerivedFromTheProductionUnionTest.test_a_sixth_rule_joins_for_free`,
which is the only one that fails if someone re-implements the predicate here.
The shape tests below it exist to keep the wiring honest in the other
direction: a delegating helper that never actually reads the graph would pass
the derivation test alone.

Everything the purge rules key on comes from the real producers -- the
placeholder from :func:`weld.graph_closure._ensure_package_node`, its anchoring
edge from :func:`weld.graph_closure._add_edge` -- rather than from
``{"type": "package", ...}`` / ``{"from": ..., "to": ...}`` literals. That is
ADR 0139 mechanism 1, and the concrete reason for it here: the rule keys on
``props.source_strategy`` and ``props.authority``, so a literal that drifted
from what the producer stamps would leave this file green while the invariant
matched nothing in a real graph. Only the on-wire envelope is hand-built, at one
exempted site -- see :func:`_graph` for why obtaining that from a producer would
delete the coverage it carries.
"""

from __future__ import annotations

import unittest
from unittest import mock

from weld.graph_closure import _add_edge, _ensure_package_node
from weld.tests import _graph_invariants
from weld.tests._graph_invariants import assert_no_orphan_stubs


def _placeholder(
    name: str = "strings", language: str = "go",
) -> tuple[str, dict[str, dict]]:
    """Mint one edge-anchored external package placeholder, via its producer."""
    nodes: dict[str, dict] = {}
    node_id = _ensure_package_node(nodes, name, language)
    return node_id, nodes


def _importer_edges(from_id: str, to_id: str) -> list[dict]:
    """One ``depends_on`` edge, appended by the producer that appends the real ones."""
    edges: list[dict] = []
    _add_edge(edges, from_id, to_id, "depends_on", {
        "source_strategy": "graph_closure", "confidence": "inferred",
    })
    return edges


def _importer_file(nodes: dict[str, dict], rel_path: str = "app/main.go") -> str:
    """Scaffolding for the edge's ``from`` endpoint. No purge rule reads it."""
    node_id = f"file:{rel_path.rsplit('.', 1)[0]}"
    nodes[node_id] = {"type": "file", "props": {"file": rel_path}}
    return node_id


def _graph(nodes: dict[str, dict], edges: object = ()) -> dict:
    """The on-wire envelope the invariant parses.

    Hand-built deliberately, and the only construction site in this file so the
    exemption is one line rather than six: the envelope is what
    ``_graph_invariants.graph_nodes`` / ``graph_edges`` normalise, and the two
    :class:`OnWireShapeTest` cases exist precisely to feed it in *both* legal
    spellings. No producer emits the same graph twice in two shapes, so
    obtaining it from one would delete the coverage. Everything the purge rules
    actually key on -- the placeholder node, the anchoring edge -- comes from
    ``weld.graph_closure``'s own minters above.
    """
    return {  # test-hygiene: allow hand-built-payload
        "nodes": nodes,
        "edges": edges,
    }


class OrphanStubInvariantTest(unittest.TestCase):
    """Both verdicts and their selectivity, on producer-minted placeholders."""

    def test_anchored_placeholder_passes(self) -> None:
        """A placeholder with a surviving importer is not an orphan.

        The non-vacuity half: an invariant that flagged every placeholder would
        also be "green on the goldens" the moment nobody ran it on a real graph.
        """
        node_id, nodes = _placeholder()
        importer = _importer_file(nodes)
        assert_no_orphan_stubs(_graph(nodes, _importer_edges(importer, node_id)))

    def test_emptied_placeholder_fails_and_names_the_node(self) -> None:
        node_id, nodes = _placeholder()
        with self.assertRaises(AssertionError) as caught:
            assert_no_orphan_stubs(_graph(nodes))
        message = str(caught.exception)
        self.assertIn(node_id, message)
        self.assertIn("graph_closure", message)

    def test_report_names_only_the_unanchored_placeholder(self) -> None:
        """Selectivity: an anchored placeholder beside an orphan is not reported.

        Neither test above can tell "flags the orphan" from "flags every
        placeholder it sees" -- a graph holding exactly one placeholder makes
        those indistinguishable, and a whole-class invariant that over-reports
        is as useless as one that under-reports.
        """
        anchored_id, nodes = _placeholder("strings", "go")
        orphan_id = _ensure_package_node(nodes, "encoding/json", "go")
        self.assertNotEqual(anchored_id, orphan_id)
        importer = _importer_file(nodes)

        with self.assertRaises(AssertionError) as caught:
            assert_no_orphan_stubs(
                _graph(nodes, _importer_edges(importer, anchored_id)),
            )
        message = str(caught.exception)
        self.assertIn(orphan_id, message)
        self.assertNotIn(anchored_id, message)


class OnWireShapeTest(unittest.TestCase):
    """Both legal on-wire shapes reach the same verdict.

    ``.weld/graph.json`` is read straight off disk by the evaluator probes and
    the golden loaders, and both spellings are legal there -- so an invariant
    that only understood one would silently pass on half the callers by
    normalising their graph to zero nodes.
    """

    def test_list_shaped_payload_matches_dict_shaped(self) -> None:
        node_id, nodes = _placeholder()
        as_list = [dict(node, id=nid) for nid, node in nodes.items()]
        for payload in (_graph(nodes), _graph(as_list)):
            with self.subTest(shape=type(payload["nodes"]).__name__):
                with self.assertRaises(AssertionError) as caught:
                    assert_no_orphan_stubs(payload)
                self.assertIn(node_id, str(caught.exception))

    def test_dict_keyed_edges_are_read(self) -> None:
        """An edge map keyed by id anchors the placeholder just as a list does."""
        node_id, nodes = _placeholder()
        importer = _importer_file(nodes)
        keyed = dict(enumerate(_importer_edges(importer, node_id)))
        assert_no_orphan_stubs(_graph(nodes, keyed))


class DerivedFromTheProductionUnionTest(unittest.TestCase):
    """ADR 0139: the verdict comes from the production union, not from here.

    This is the mechanism the ADR actually asked for, so it is asserted rather
    than assumed. Patching the union is what makes "derived" observable: a
    hand-rolled predicate would ignore the patch and keep answering from its own
    logic, which is precisely the desync mechanism 4 forbids.
    """

    def test_a_sixth_rule_joins_for_free(self) -> None:
        """A hypothetical sixth purge rule reaches the invariant with no edit here."""
        sixth_rule_hit = "symbol:hypothetical:sixth-rule-shape"
        graph = _graph({sixth_rule_hit: {"type": "symbol"}})

        assert_no_orphan_stubs(graph)  # today's five rules do not claim it

        with mock.patch.object(
            _graph_invariants,
            "emptied_placeholder_node_ids",
            return_value={sixth_rule_hit},
        ):
            with self.assertRaises(AssertionError) as caught:
                assert_no_orphan_stubs(graph)
        self.assertIn(sixth_rule_hit, str(caught.exception))

    def test_the_union_decides_a_pass_too(self) -> None:
        """Not just the failure path: an empty union verdict is accepted as-is."""
        _, nodes = _placeholder()  # an orphan by the real rules
        with mock.patch.object(
            _graph_invariants, "emptied_placeholder_node_ids", return_value=set(),
        ):
            assert_no_orphan_stubs(_graph(nodes))


if __name__ == "__main__":
    unittest.main()
