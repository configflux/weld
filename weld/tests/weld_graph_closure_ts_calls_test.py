"""The closure's TypeScript call-binding rules, and every refusal (bd lrnx1.3).

Driven through :func:`weld.graph_closure.close_graph` rather than the pass in
isolation: the ordering inside that function is part of the contract (the
retarget has to land before the call-edge decoration reads an endpoint), and a
test that called the pass directly would keep passing if it were dropped from
the sequence.

Two rules, and the refusals are the larger half of the suite on purpose. A
resolver that binds everything it is asked about is not a resolver, it is a
guess with a node id attached -- ADR 0134's contract is that a sentinel which
genuinely cannot resolve stays, visibly, rather than being replaced by a
plausible answer.
"""

from __future__ import annotations

import json
import unittest

from weld.graph_closure import close_graph
from weld.tests._graph_closure_ts_calls_fixture import (
    call_edge,
    caller_id,
    edge_props,
    endpoint,
    graph,
    symbol_id,
)

BARREL = "packages/shared/index.ts"
MONEY = "packages/shared/money.ts"
IMPORTER = "apps/web/route.ts"
FORMAT_PRICE = symbol_id(MONEY, "formatPrice")


def _call_evidence(nodes: dict, edges: list) -> str:
    """Every ``calls`` edge plus the sentinels still standing, as one string."""
    return json.dumps(
        {
            "calls": [e for e in edges if e.get("type") == "calls"],
            "sentinels": sorted(
                n for n in nodes if n.startswith("symbol:unresolved:")
            ),
        },
        sort_keys=True,
    )


class ExactFileRuleTest(unittest.TestCase):
    def test_a_bound_file_that_defines_the_name_answers_directly(self) -> None:
        nodes, edges = graph(
            files=[IMPORTER, MONEY],
            symbols=[(MONEY, "formatPrice")],
            calls=[call_edge(
                IMPORTER, "formatPrice",
                specifier="@acme/shared/money", target=MONEY,
            )],
        )
        close_graph(nodes, edges)
        self.assertEqual(endpoint(edges, "formatPrice"), FORMAT_PRICE)

    def test_the_bound_edge_records_how_it_resolved(self) -> None:
        """``raw`` keeps the local spelling: it is what the call was written as."""
        nodes, edges = graph(
            files=[IMPORTER, MONEY],
            symbols=[(MONEY, "formatPrice")],
            calls=[call_edge(
                IMPORTER, "fp", name="formatPrice",
                specifier="@acme/shared/money", target=MONEY,
            )],
        )
        close_graph(nodes, edges)
        props = edge_props(edges, "fp")
        self.assertEqual(endpoint(edges, "fp"), FORMAT_PRICE)
        self.assertTrue(props["resolved"])
        self.assertEqual(props["confidence"], "definite")
        self.assertEqual(props["resolution"], "import")
        self.assertEqual(props["raw"], "fp")

    def test_a_relative_specifier_is_resolved_against_the_path_index(self) -> None:
        """No first-party target needed: the graph already knows the file exists."""
        nodes, edges = graph(
            files=["pkg/app.ts", "pkg/money.ts"],
            symbols=[("pkg/money.ts", "formatPrice")],
            calls=[call_edge("pkg/app.ts", "formatPrice", specifier="./money")],
        )
        close_graph(nodes, edges)
        self.assertEqual(
            endpoint(edges, "formatPrice"), symbol_id("pkg/money.ts", "formatPrice"),
        )

    def test_a_relative_directory_specifier_reaches_its_index_file(self) -> None:
        nodes, edges = graph(
            files=["pkg/app.ts", "pkg/lib/index.ts"],
            symbols=[("pkg/lib/index.ts", "helper")],
            calls=[call_edge("pkg/app.ts", "helper", specifier="./lib")],
        )
        close_graph(nodes, edges)
        self.assertEqual(
            endpoint(edges, "helper"), symbol_id("pkg/lib/index.ts", "helper"),
        )


class OpaqueEntryRuleTest(unittest.TestCase):
    """The barrel case: the entry file contributes nothing to reach through."""

    def _barrel_graph(self, extra_symbols=(), foreign=None):
        return graph(
            files=[IMPORTER, BARREL, MONEY],
            symbols=[(MONEY, "formatPrice"), *extra_symbols],
            calls=[call_edge(
                IMPORTER, "formatPrice",
                specifier="@acme/shared", target=BARREL,
            )],
            foreign=foreign,
        )

    def test_the_package_directory_answers_when_the_entry_defines_nothing(self) -> None:
        nodes, edges = self._barrel_graph()
        close_graph(nodes, edges)
        self.assertEqual(endpoint(edges, "formatPrice"), FORMAT_PRICE)

    def test_two_definitions_in_one_package_are_an_ambiguity_not_a_pick(self) -> None:
        nodes, edges = self._barrel_graph(
            extra_symbols=[("packages/shared/legacy.ts", "formatPrice")],
        )
        close_graph(nodes, edges)
        self.assertEqual(
            endpoint(edges, "formatPrice"), "symbol:unresolved:formatPrice",
        )

    def test_a_definition_outside_the_package_is_not_a_candidate(self) -> None:
        """The bound entry scopes the search; a same-named export elsewhere is
        a different function that happens to share a word."""
        nodes, edges = graph(
            files=[IMPORTER, BARREL, "packages/other/money.ts"],
            symbols=[("packages/other/money.ts", "formatPrice")],
            calls=[call_edge(
                IMPORTER, "formatPrice",
                specifier="@acme/shared", target=BARREL,
            )],
        )
        close_graph(nodes, edges)
        self.assertEqual(
            endpoint(edges, "formatPrice"), "symbol:unresolved:formatPrice",
        )

    def test_a_same_named_definition_in_another_language_is_not_a_candidate(self) -> None:
        nodes, edges = graph(
            files=[IMPORTER, BARREL],
            symbols=[],
            calls=[call_edge(
                IMPORTER, "formatPrice",
                specifier="@acme/shared", target=BARREL,
            )],
            foreign=[("packages/shared/money.py", "formatPrice", "python")],
        )
        close_graph(nodes, edges)
        self.assertEqual(
            endpoint(edges, "formatPrice"), "symbol:unresolved:formatPrice",
        )


class RefusalTest(unittest.TestCase):
    def test_an_entry_that_was_read_is_not_reached_past(self) -> None:
        """It defines names, just not this one -- so the name came from elsewhere."""
        nodes, edges = graph(
            files=[IMPORTER, BARREL, MONEY],
            symbols=[(BARREL, "Money"), (MONEY, "formatPrice")],
            calls=[call_edge(
                IMPORTER, "formatPrice",
                specifier="@acme/shared", target=BARREL,
            )],
        )
        close_graph(nodes, edges)
        self.assertEqual(
            endpoint(edges, "formatPrice"), "symbol:unresolved:formatPrice",
        )

    def test_a_module_this_graph_does_not_hold_binds_nothing(self) -> None:
        """Not indexed is not the same as opaque.

        The package defines the name once, so the barrel rule below would
        answer -- but the module the import actually named is absent, and it
        may be the one that defines it. That is the reading the exact-file
        rule owns, and guessing past it is how a resolver answers with the
        wrong definition rather than with nothing.
        """
        nodes, edges = graph(
            files=[IMPORTER, MONEY],
            symbols=[(MONEY, "formatPrice")],
            calls=[call_edge(
                IMPORTER, "formatPrice",
                specifier="@acme/shared", target=BARREL,
            )],
        )
        close_graph(nodes, edges)
        self.assertEqual(
            endpoint(edges, "formatPrice"), "symbol:unresolved:formatPrice",
        )

    def test_a_third_party_specifier_binds_nothing(self) -> None:
        nodes, edges = graph(
            files=[IMPORTER, MONEY],
            symbols=[(MONEY, "formatPrice")],
            calls=[call_edge(IMPORTER, "formatPrice", specifier="lodash")],
        )
        close_graph(nodes, edges)
        self.assertEqual(
            endpoint(edges, "formatPrice"), "symbol:unresolved:formatPrice",
        )

    def test_an_unhinted_call_is_left_exactly_where_it_was(self) -> None:
        """A member call (``res.json()``) has no import evidence and never gains any."""
        nodes, edges = graph(
            files=[IMPORTER, MONEY],
            symbols=[(MONEY, "json")],
            calls=[call_edge(IMPORTER, "json", hinted=False)],
        )
        close_graph(nodes, edges)
        props = edge_props(edges, "json")
        self.assertEqual(endpoint(edges, "json"), "symbol:unresolved:json")
        self.assertFalse(props["resolved"])
        self.assertEqual(props["resolution"], "unresolved")


class ReDerivationTest(unittest.TestCase):
    def test_a_stale_retarget_is_undone_when_its_definition_is_gone(self) -> None:
        """The incremental contract: a clean caller is not re-walked, so the
        endpoint an earlier round moved has to be re-derived here or it is
        inherited forever. The sentinel node is re-minted too -- an earlier
        round dropped it once nothing named it."""
        nodes, edges = graph(
            files=[IMPORTER, BARREL],
            symbols=[],
            calls=[call_edge(
                IMPORTER, "formatPrice",
                specifier="@acme/shared", target=BARREL,
            )],
        )
        # What the previous round left behind: the edge on a definition this
        # graph no longer holds, and no sentinel node at all.
        edges[0]["to"] = FORMAT_PRICE
        edges[0]["props"].update(
            {"resolved": True, "confidence": "definite", "resolution": "import"},
        )
        nodes.pop("symbol:unresolved:formatPrice")

        close_graph(nodes, edges)
        self.assertEqual(
            endpoint(edges, "formatPrice"), "symbol:unresolved:formatPrice",
        )
        self.assertIn("symbol:unresolved:formatPrice", nodes)
        self.assertFalse(edge_props(edges, "formatPrice")["resolved"])

    def test_re_closing_leaves_every_call_edge_byte_identical(self) -> None:
        """Scoped to the call evidence, which is what this pass owns.

        ``close_graph`` as a whole is not idempotent -- it appends containment
        and dependency edges on every run -- so a whole-graph comparison here
        would assert somebody else's contract and fail for a reason that has
        nothing to do with call binding.
        """
        nodes, edges = graph(
            files=[IMPORTER, BARREL, MONEY],
            symbols=[(MONEY, "formatPrice")],
            calls=[
                call_edge(
                    IMPORTER, "formatPrice",
                    specifier="@acme/shared", target=BARREL,
                ),
                call_edge(IMPORTER, "json", hinted=False),
            ],
        )
        close_graph(nodes, edges)
        once = _call_evidence(nodes, edges)
        close_graph(nodes, edges)
        self.assertEqual(_call_evidence(nodes, edges), once)


class SentinelLifetimeTest(unittest.TestCase):
    def test_a_sentinel_nothing_names_any_more_is_dropped(self) -> None:
        nodes, edges = graph(
            files=[IMPORTER, MONEY],
            symbols=[(MONEY, "formatPrice")],
            calls=[call_edge(
                IMPORTER, "formatPrice",
                specifier="@acme/shared/money", target=MONEY,
            )],
        )
        close_graph(nodes, edges)
        self.assertNotIn("symbol:unresolved:formatPrice", nodes)

    def test_a_sentinel_another_call_still_names_survives(self) -> None:
        """The id is a bare-name namespace: one caller resolving it does not
        make another caller's unresolved call any less unresolved."""
        second = "apps/web/other.ts"
        nodes, edges = graph(
            files=[IMPORTER, second, MONEY],
            symbols=[(MONEY, "formatPrice")],
            calls=[
                call_edge(
                    IMPORTER, "formatPrice",
                    specifier="@acme/shared/money", target=MONEY,
                ),
                call_edge(second, "formatPrice", specifier="some-package"),
            ],
        )
        close_graph(nodes, edges)
        self.assertIn("symbol:unresolved:formatPrice", nodes)
        moved = [
            str(edge["to"])
            for edge in edges
            if edge.get("type") == "calls" and edge.get("from") == caller_id(IMPORTER)
        ]
        self.assertEqual(moved, [FORMAT_PRICE])


if __name__ == "__main__":
    unittest.main()
