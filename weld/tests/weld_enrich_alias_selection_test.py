"""A legacy node id reaches enrichment through the alias index (ADR 0041).

``props.aliases`` keeps a pre-rename node id resolvable, so an id pasted
from an older transcript still names the node it named then. Enrichment
honoured that on exactly one of its four (surface x path) combinations:
the MCP provider-backed call rewrote the id at its own boundary, while
``wd enrich --node``, ``wd enrich --agent-direct --node`` and
``weld_enrich(agent_direct=true, node_id=...)`` handed the raw id to the
selection oracle and got back "node not found".

The rewrite now lives in that oracle -- :mod:`weld._enrich_selection`,
the one door a named node id passes through for both paths -- so the
four combinations cannot disagree again. This file pins the properties
at the oracle; the two surfaces are held byte-identical by
``weld_mcp_enrich_agent_direct_test.CliMcpEnrichParityTest`` and the MCP
provider path end-to-end by ``weld_alias_aware_mcp_test``.

Resolution is deliberately not a fuzzy match: it defers to
:func:`weld._alias_index.resolve_id`, so a canonical id outranks any
alias claiming it, and an unknown id still fails naming what the caller
typed. Enrichment *writes* to the node it selects, which is why the
shadow case below is a test rather than a comment.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld._enrich_agent_direct import build_agent_direct_plan
from weld._enrich_selection import selected_node_ids
from weld.enrich import run_enrichment
from weld.tests._enrich_agent_direct_test_helpers import (
    StubProvider,
    nodes,
    write_graph,
)

#: The fixture's legacy id and the node it names today.
LEGACY_ID = "file:main"
CANONICAL_ID = "file:app/main"


class AliasSelectionTest(unittest.TestCase):
    """A legacy id selects the node it used to name -- and nothing else does."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.graph = write_graph(self.root, nodes())

    def test_alias_selects_the_canonical_node(self) -> None:
        self.assertEqual(selected_node_ids(self.graph, LEGACY_ID), [CANONICAL_ID])

    def test_plan_by_alias_equals_plan_by_canonical_id(self) -> None:
        # Not merely "lists the right node": the whole plan has to be the
        # same run, or a caller batching by legacy id would read different
        # counts than one batching by canonical id and mistake the
        # difference for progress.
        self.assertEqual(
            build_agent_direct_plan(self.graph, node_id=LEGACY_ID),
            build_agent_direct_plan(self.graph, node_id=CANONICAL_ID),
        )

    def test_provider_loop_enriches_the_canonical_node(self) -> None:
        # The provider path reads the same oracle, so --node <legacy>
        # writes to the node the plan listed instead of failing.
        result = run_enrichment(
            write_graph(self.root, nodes()),
            provider=StubProvider(),
            provider_name="stub",
            node_id=LEGACY_ID,
            persist=False,
        )

        self.assertEqual(result["enriched"], [CANONICAL_ID])
        self.assertEqual(result["errors"], [])

    def test_unknown_id_reports_the_id_that_was_typed(self) -> None:
        # The failure mode for a genuinely unknown id is unchanged, and it
        # echoes the caller's string rather than some resolved form of it.
        with self.assertRaises(ValueError) as caught:
            selected_node_ids(self.graph, "file:nope")

        self.assertIn("file:nope", str(caught.exception))

    def test_a_shadowing_alias_never_redirects_a_canonical_id(self) -> None:
        # An alias naming a real node id is dropped when the index is built
        # (the canonical wins unconditionally). Worth pinning on this path
        # specifically: enrichment writes to whatever it selects, so a
        # redirect would put one node's description on another.
        node_map = nodes()
        node_map["entity:Cart"]["props"]["aliases"] = ["entity:Store"]
        graph = write_graph(self.root, node_map)

        self.assertEqual(selected_node_ids(graph, "entity:Store"), ["entity:Store"])


if __name__ == "__main__":
    unittest.main()
