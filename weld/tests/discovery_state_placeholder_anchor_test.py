"""A placeholder anchored by an OUTBOUND edge is not dead (bd 5038-q4t3d).

Both zero-inbound purge rules over ``symbol`` placeholders --
:func:`weld._discover_unresolved_symbol_purge.emptied_unresolved_symbol_node_ids`
(bd oao53) and
:func:`weld._discover_resolved_stub_purge.emptied_resolved_stub_node_ids`
(bd n4nvt) -- accumulated ``edge["to"]`` and called that "still referenced".
ADR 0122's ``decorates`` runs decorator -> decorated, so a symbol referenced
only as a decorator is the edge's ``from`` endpoint and was read as dead by
both. On this repo's own full-discover graph that was seven live nodes
holding 203 ``decorates`` edges between them.

Every fixture below is producer-fed (ADR 0139 mechanism 1): one real
``python_callgraph.extract()`` over a real temp tree mints both placeholder
shapes at once -- ``@dataclass`` gives the ``symbol:py:<module>:<qual>``
resolved stub, ``@property``/``@staticmethod`` give ``symbol:unresolved:*``
sentinels -- together with the ``decorates`` edges that anchor them. Nothing
here writes a node or an edge by hand, so a change to what the minter stamps
reaches these assertions instead of passing them.

The unresolved-sentinel family's own file
(``discovery_state_unresolved_symbol_purge_test.py``) sits at the 400-line
cap and cannot absorb even an import, which is the second reason both rules'
outbound cases are here: they share one predicate
(:mod:`weld._discover_placeholder_anchor`) and belong beside each other
rather than split across two family files that would each pin half of it.
"""

from __future__ import annotations

import tempfile
import unittest
from functools import lru_cache
from pathlib import Path

from weld._discover_external_package_purge import emptied_placeholder_node_ids
from weld._discover_placeholder_anchor import edge_anchored_node_ids
from weld._discover_resolved_stub_purge import emptied_resolved_stub_node_ids
from weld._discover_unresolved_symbol_purge import (
    emptied_unresolved_symbol_node_ids,
)
from weld.discovery_state import purge_stale_nodes
from weld.strategies import python_callgraph

#: The resolved cross-glob stub shape (bd n4nvt): a real
#: ``symbol:py:<module>:<qual>`` id the batch never walked.
STUB_ID = "symbol:py:dataclasses:dataclass"

#: The unresolved-sentinel shape (bd oao53): a bare name nothing resolves.
SENTINEL_IDS = frozenset(
    {"symbol:unresolved:property", "symbol:unresolved:staticmethod"},
)

#: The decorated file, and one unrelated file so a purge round has something
#: ordinary to purge without touching the decorators.
_DECORATED = "pkg/decorated.py"
_SPARE = "pkg/spare.py"

_DECORATED_SOURCE = (
    "from dataclasses import dataclass\n"
    "\n"
    "\n"
    "@dataclass\n"
    "class Rec:\n"
    "    value: int\n"
    "\n"
    "\n"
    "class Holder:\n"
    "    @property\n"
    "    def size(self):\n"
    "        return 1\n"
    "\n"
    "    @staticmethod\n"
    "    def make():\n"
    "        return 2\n"
)

_SPARE_SOURCE = "def spare():\n    return 0\n"


@lru_cache(maxsize=None)
def _extracted() -> tuple[dict[str, dict], tuple[dict, ...]]:
    """One real extraction, shared by every case below.

    ``python_callgraph`` is a pure function of the tree it walks, so a single
    run serves every assertion here and the temp tree does not outlive it.
    """
    with tempfile.TemporaryDirectory(prefix="q4t3d-anchor-") as td:
        root = Path(td)
        (root / "pkg").mkdir()
        (root / _DECORATED).write_text(_DECORATED_SOURCE, encoding="utf-8")
        (root / _SPARE).write_text(_SPARE_SOURCE, encoding="utf-8")
        result = python_callgraph.extract(root, {"glob": "pkg/**/*.py"}, {})
    return result.nodes, tuple(result.edges)


def _graph() -> tuple[dict[str, dict], list[dict]]:
    nodes, edges = _extracted()
    return dict(nodes), [dict(e) for e in edges]


class ProducerFixtureTest(unittest.TestCase):
    """The fixture is only evidence if it really mints both shapes.

    A silent change in what ``python_callgraph`` emits for a decorator would
    otherwise leave every case below asserting over an empty set. This is the
    non-vacuity check, not a claim about the purge rules.
    """

    def test_both_placeholder_shapes_are_minted(self) -> None:
        nodes, _ = _graph()
        self.assertIn(STUB_ID, nodes)
        for sentinel in SENTINEL_IDS:
            self.assertIn(sentinel, nodes)

    def test_every_placeholder_is_anchored_outbound_only(self) -> None:
        _, edges = _graph()
        for placeholder in {STUB_ID, *SENTINEL_IDS}:
            outbound = [e for e in edges if e["from"] == placeholder]
            inbound = [e for e in edges if e["to"] == placeholder]
            self.assertEqual(
                [e["type"] for e in outbound], ["decorates"],
                f"{placeholder} must be anchored by exactly one decorates "
                "edge for these cases to exercise the reversed direction",
            )
            self.assertEqual(
                inbound, [],
                f"{placeholder} has an inbound edge, so a zero-inbound rule "
                "would already keep it and this fixture proves nothing",
            )


class EdgeAnchoredNodeIdsTest(unittest.TestCase):
    """The shared predicate the two rules now consume."""

    def test_both_endpoints_of_every_edge_are_anchored(self) -> None:
        """Derivation, not enumeration: the answer is read off the edge's two
        endpoints, so it carries no list of edge types to keep current and a
        future reversed-direction edge kind anchors for free (bd 5038-q4t3d).
        """
        _, edges = _graph()
        anchored = edge_anchored_node_ids(edges)
        for edge in edges:
            self.assertIn(edge["from"], anchored)
            self.assertIn(edge["to"], anchored)

    def test_no_edges_anchors_nothing(self) -> None:
        self.assertEqual(edge_anchored_node_ids([]), set())

    def test_non_string_endpoints_are_skipped_defensively(self) -> None:
        """Strategy-authored shape is untrusted -- a project-local strategy
        under ``.weld/strategies/`` can hand back anything -- so a malformed
        endpoint must not raise. Mirrors the sibling purge modules' posture
        (bd oao53, bd n4nvt).
        """
        _, edges = _graph()
        edges[0]["from"] = 42
        anchored = edge_anchored_node_ids(edges)
        self.assertNotIn(42, anchored)
        self.assertIn(edges[0]["to"], anchored)


class UnresolvedSentinelOutboundAnchorTest(unittest.TestCase):
    """bd oao53's rule, on the direction it could not see."""

    def test_outbound_decorates_keeps_the_sentinel(self) -> None:
        nodes, edges = _graph()
        self.assertEqual(emptied_unresolved_symbol_node_ids(nodes, edges), set())

    def test_losing_that_edge_still_purges_it(self) -> None:
        """The other direction of the same rule: this widens what counts as an
        anchor, it does not stop the rule purging. Pins bd oao53's original
        contract as still live (ADR 0139: an invariant is never weakened so
        the existing state passes).
        """
        nodes, _ = _graph()
        self.assertEqual(
            emptied_unresolved_symbol_node_ids(nodes, []), set(SENTINEL_IDS),
        )


class ResolvedStubOutboundAnchorTest(unittest.TestCase):
    """bd n4nvt's rule, on the direction it could not see.

    Its docstring already listed ``decorates`` among the edge types it counts
    -- as an inbound type. This is the same edge, read from the endpoint ADR
    0122 actually puts the stub on.
    """

    def test_outbound_decorates_keeps_the_stub(self) -> None:
        nodes, edges = _graph()
        self.assertEqual(emptied_resolved_stub_node_ids(nodes, edges), set())

    def test_losing_that_edge_still_purges_it(self) -> None:
        nodes, _ = _graph()
        self.assertEqual(emptied_resolved_stub_node_ids(nodes, []), {STUB_ID})


class PlaceholderUnionTest(unittest.TestCase):
    """The entry point :mod:`weld.discovery_state` calls, and the invariant
    :func:`weld.tests._graph_invariants.assert_no_orphan_stubs` derives from
    (bd 5038-ekohj) -- the two readers that saw the wrong verdict.
    """

    def test_union_claims_no_outbound_anchored_placeholder(self) -> None:
        nodes, edges = _graph()
        self.assertEqual(emptied_placeholder_node_ids(nodes, edges), set())

    def test_union_still_claims_them_once_unanchored(self) -> None:
        nodes, _ = _graph()
        self.assertEqual(
            emptied_placeholder_node_ids(nodes, []), {STUB_ID, *SENTINEL_IDS},
        )


class PurgeStaleNodesTest(unittest.TestCase):
    """The integrated call site, over one producer-minted graph.

    ``purge_stale_nodes`` is where the wrong verdict had teeth: it removed
    the live placeholders on any incremental round that purged anything at
    all, leaving their ``decorates`` edges dangling for ADR 0074's fourth
    amendment to repair with a second whole merge pass.
    """

    def test_an_unrelated_stale_file_leaves_the_placeholders_alone(self) -> None:
        nodes, edges = _graph()
        surviving_nodes, surviving_edges = purge_stale_nodes(
            nodes, edges, {_SPARE},
        )
        self.assertNotIn("symbol:py:pkg.spare:spare", surviving_nodes)
        for placeholder in {STUB_ID, *SENTINEL_IDS}:
            self.assertIn(
                placeholder, surviving_nodes,
                f"{placeholder} was purged by a round that touched only "
                f"{_SPARE} -- nothing about its anchor changed",
            )
        self.assertEqual(
            [e for e in surviving_edges if e["type"] == "decorates"],
            [e for e in edges if e["type"] == "decorates"],
            "the decorates edges must survive with the nodes they anchor",
        )

    def test_the_decorator_file_going_stale_purges_them(self) -> None:
        """Non-vacuity for the round that SHOULD purge: with the only file
        that decorates anything stale, the edges go by provenance (ADR 0074)
        and the placeholders go with them, exactly as bd oao53 and bd n4nvt
        specified.
        """
        nodes, edges = _graph()
        surviving_nodes, surviving_edges = purge_stale_nodes(
            nodes, edges, {_DECORATED},
        )
        for placeholder in {STUB_ID, *SENTINEL_IDS}:
            self.assertNotIn(placeholder, surviving_nodes)
        self.assertEqual(
            [e for e in surviving_edges if e["type"] == "decorates"], [],
        )


if __name__ == "__main__":
    unittest.main()
