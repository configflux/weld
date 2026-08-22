"""``wd references`` must not answer "none" for a node it never looked up.

The defect (bd nywd): ``Graph.references`` resolved its argument only through
``weld._graph_match.resolve_symbol_name``, which skips every node whose type
is not ``symbol``. A ``build-target:``/``tool:``/``doc:`` node id therefore
matched nothing, and the method returned an empty envelope with **no**
``error`` -- so ``wd references build-target://weld:runtime`` printed "no
references" for a node ``wd context`` reported 47 neighbours for, 36 of them
inbound.

Two read paths disagreeing about one node is the bug. What made it a
*correctness* bug rather than a gap is that the wrong answer was spelled
exactly like a right one: "weld does not know this id" and "weld knows this
id and nothing points at it" were the same bytes, so nothing downstream --
no reader, no test, no lint -- could tell them apart.

These tests pin all three halves of the fix: node ids resolve, non-symbol
nodes report their real referrers, and symbol behaviour is unchanged (the
part a widening could quietly break).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld.graph import Graph

#: ADR 0050 requires every edge producer to stamp a confidence rank; an
#: unstamped fixture edge warns on every load and buries the test output.
_DEFINITE = {"confidence": "definite", "source_strategy": "test_fixture"}


def _graph(tmp: str) -> Graph:
    """A small graph with one symbol pair and one build target."""
    graph = Graph(Path(tmp))
    graph.load()
    graph.add_node(
        "symbol:py:pkg.mod:callee", "symbol", "callee",
        {"qualname": "pkg.mod.callee", "file": "pkg/mod.py"},
    )
    graph.add_node(
        "symbol:py:pkg.mod:caller", "symbol", "caller",
        {"qualname": "pkg.mod.caller", "file": "pkg/mod.py"},
    )
    graph.add_node("file:pkg/mod", "file", "mod", {"file": "pkg/mod.py"})
    graph.add_node("build-target://pkg:lib", "build-target", "lib", {})
    graph.add_node("test-target://pkg:lib_test", "test-target", "lib_test", {})
    graph.add_edge(
        "symbol:py:pkg.mod:caller", "symbol:py:pkg.mod:callee", "calls", _DEFINITE,
    )
    graph.add_edge("file:pkg/mod", "symbol:py:pkg.mod:callee", "contains", _DEFINITE)
    graph.add_edge("test-target://pkg:lib_test", "build-target://pkg:lib",
                   "depends_on", _DEFINITE)
    graph.add_edge("test-target://pkg:lib_test", "build-target://pkg:lib",
                   "tests", _DEFINITE)
    graph.add_edge("file:pkg/mod", "build-target://pkg:lib", "depends_on", _DEFINITE)
    return graph


class ReferencesAcceptsANodeIdTest(unittest.TestCase):
    """The resolution half: a full node id is a legal argument."""

    def test_non_symbol_node_id_resolves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            graph = _graph(tmp)
            result = graph.references("build-target://pkg:lib")
            self.assertEqual(
                ["build-target://pkg:lib"], [m["id"] for m in result["matches"]]
            )
            self.assertIsNone(result.get("error"))

    def test_symbol_node_id_resolves_to_the_same_answer_as_its_bare_name(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            graph = _graph(tmp)
            by_id = graph.references("symbol:py:pkg.mod:callee")
            by_name = graph.references("callee")
            self.assertEqual(
                [m["id"] for m in by_id["matches"]],
                [m["id"] for m in by_name["matches"]],
            )
            self.assertEqual(
                [c["id"] for c in by_id["callers"]],
                [c["id"] for c in by_name["callers"]],
            )


class ReferencesReportsRealReferrersTest(unittest.TestCase):
    """The answer half: what points at a non-symbol node."""

    def test_build_target_reports_its_inbound_edges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            graph = _graph(tmp)
            result = graph.references("build-target://pkg:lib")
            self.assertEqual(
                {"file:pkg/mod", "test-target://pkg:lib_test"},
                {c["id"] for c in result["callers"]},
            )
            self.assertEqual(3, len(result["edges"]))

    def test_it_agrees_with_context_about_inbound_neighbours(self) -> None:
        # The disagreement itself, stated as a property rather than as two
        # remembered numbers: every node ``context`` reports as an inbound
        # neighbour must appear in ``references``.
        with tempfile.TemporaryDirectory() as tmp:
            graph = _graph(tmp)
            node_id = "build-target://pkg:lib"
            inbound = {
                edge["from"]
                for edge in graph.context(node_id)["edges"]
                if edge["to"] == node_id and edge["from"] != node_id
            }
            self.assertTrue(inbound, "fixture has no inbound edges to compare")
            referrers = {c["id"] for c in graph.references(node_id)["callers"]}
            self.assertEqual(set(), inbound - referrers)

    def test_edge_type_is_not_filtered_for_non_symbol_nodes(self) -> None:
        # ``depends_on`` and ``tests`` both count. Restricting the walk to
        # ``calls`` is precisely how the answer came back empty: nothing
        # *calls* a build target.
        with tempfile.TemporaryDirectory() as tmp:
            graph = _graph(tmp)
            types = {
                edge["type"]
                for edge in graph.references("build-target://pkg:lib")["edges"]
            }
            self.assertEqual({"depends_on", "tests"}, types)


class ReferencesDistinguishesUnknownFromEmptyTest(unittest.TestCase):
    """The honesty half: silence must not double as a fact."""

    def test_unknown_name_carries_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            graph = _graph(tmp)
            result = graph.references("no-such-node")
            self.assertIn("error", result)
            self.assertEqual([], result["matches"])

    def test_known_node_with_no_referrers_carries_no_error(self) -> None:
        # The other side of the same coin, and the reason the error alone is
        # not enough: a real node nothing points at must answer emptily and
        # say so *without* an error.
        with tempfile.TemporaryDirectory() as tmp:
            graph = _graph(tmp)
            result = graph.references("test-target://pkg:lib_test")
            self.assertEqual(
                ["test-target://pkg:lib_test"],
                [m["id"] for m in result["matches"]],
            )
            self.assertEqual([], result["callers"])
            self.assertIsNone(result.get("error"))

    def test_callers_already_reported_an_error_and_still_does(self) -> None:
        # ``callers`` is where the "unknown vs none" distinction was already
        # right; ``references`` now matches it rather than the reverse.
        with tempfile.TemporaryDirectory() as tmp:
            graph = _graph(tmp)
            self.assertIn("error", graph.callers("no-such-node"))


class ReferencesSymbolBehaviourIsUnchangedTest(unittest.TestCase):
    """The regression guard on the half that was already correct."""

    def test_symbol_referrers_stay_call_edges_only(self) -> None:
        # ``file:pkg/mod`` *contains* the callee but does not call it. If
        # the non-symbol widening leaked onto symbols, this would gain a
        # containment referrer and every ``wd references`` on a function
        # would start reporting its own file.
        with tempfile.TemporaryDirectory() as tmp:
            graph = _graph(tmp)
            result = graph.references("callee")
            self.assertEqual(
                ["symbol:py:pkg.mod:caller"],
                [c["id"] for c in result["callers"]],
            )
            self.assertEqual(
                {"calls"}, {edge["type"] for edge in result["edges"]}
            )


class ReferencesAttributesCallersToTheirMatchTest(unittest.TestCase):
    """The conflation bug (bd nyoks): ``references`` on a bare name that
    resolves to N same-named symbols used to merge every match's callers
    into one flat list keyed only by referrer id, so the answer could not
    say which match a given caller actually depends on. Each caller entry
    now carries a ``targets`` list naming the match(es) it was found under.
    """

    def _two_module_graph(self, tmp: str) -> Graph:
        """Two unrelated symbols named ``helper``, each with its own caller."""
        graph = Graph(Path(tmp))
        graph.load()
        graph.add_node(
            "symbol:py:pkg_a.mod:helper", "symbol", "helper",
            {"qualname": "pkg_a.mod.helper", "file": "pkg_a/mod.py"},
        )
        graph.add_node(
            "symbol:py:pkg_b.mod:helper", "symbol", "helper",
            {"qualname": "pkg_b.mod.helper", "file": "pkg_b/mod.py"},
        )
        graph.add_node(
            "symbol:py:pkg_a.mod:caller_a", "symbol", "caller_a",
            {"qualname": "pkg_a.mod.caller_a", "file": "pkg_a/mod.py"},
        )
        graph.add_node(
            "symbol:py:pkg_b.mod:caller_b", "symbol", "caller_b",
            {"qualname": "pkg_b.mod.caller_b", "file": "pkg_b/mod.py"},
        )
        graph.add_edge(
            "symbol:py:pkg_a.mod:caller_a", "symbol:py:pkg_a.mod:helper",
            "calls", _DEFINITE,
        )
        graph.add_edge(
            "symbol:py:pkg_b.mod:caller_b", "symbol:py:pkg_b.mod:helper",
            "calls", _DEFINITE,
        )
        return graph

    def test_two_matches_with_disjoint_callers_each_name_their_own_match(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            graph = self._two_module_graph(tmp)
            result = graph.references("helper")
            self.assertEqual(
                {"symbol:py:pkg_a.mod:helper", "symbol:py:pkg_b.mod:helper"},
                {m["id"] for m in result["matches"]},
            )
            by_id = {c["id"]: c for c in result["callers"]}
            self.assertEqual(
                ["symbol:py:pkg_a.mod:helper"],
                by_id["symbol:py:pkg_a.mod:caller_a"]["targets"],
            )
            self.assertEqual(
                ["symbol:py:pkg_b.mod:helper"],
                by_id["symbol:py:pkg_b.mod:caller_b"]["targets"],
            )

    def test_a_caller_of_both_matches_names_both_without_duplicating_the_row(
        self,
    ) -> None:
        # The other half of the shape decision: a caller that genuinely
        # depends on every match gets one row with both target ids, not one
        # row per (caller, match) pair -- cardinality/order of ``callers``
        # for the common single-match case must not change.
        with tempfile.TemporaryDirectory() as tmp:
            graph = self._two_module_graph(tmp)
            graph.add_node(
                "symbol:py:pkg_c.mod:both_caller", "symbol", "both_caller",
                {"qualname": "pkg_c.mod.both_caller", "file": "pkg_c/mod.py"},
            )
            graph.add_edge(
                "symbol:py:pkg_c.mod:both_caller", "symbol:py:pkg_a.mod:helper",
                "calls", _DEFINITE,
            )
            graph.add_edge(
                "symbol:py:pkg_c.mod:both_caller", "symbol:py:pkg_b.mod:helper",
                "calls", _DEFINITE,
            )
            result = graph.references("helper")
            both = [
                c for c in result["callers"]
                if c["id"] == "symbol:py:pkg_c.mod:both_caller"
            ]
            self.assertEqual(1, len(both))
            self.assertEqual(
                {"symbol:py:pkg_a.mod:helper", "symbol:py:pkg_b.mod:helper"},
                set(both[0]["targets"]),
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
