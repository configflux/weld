"""Impact's bounded read envelope (ADR 0082 as amended by bd gfpl).

Split from ``weld_read_traversal_test.py`` alongside the module split: the
shaper moved to :mod:`weld._read_impact` because impact is the one bounded read
with *nested* droppable buckets and a contract about what may not shrink.

The load-bearing cases here are the safety ones. A size fix that let a payload
come back reporting a smaller blast radius or a lower risk than the change
carries would turn a size problem into a safety problem, so the counts and the
verdict are pinned independently of how hard the budget prunes.
"""

from __future__ import annotations

import json
import unittest

from weld._read_budget import envelope_bytes
from weld.read_traversal import shape_impact


def _node(node_id: str, *, origin: str = "project", filler: int = 40) -> dict:
    """A node dict fat enough that a handful of them blow a small budget."""
    return {
        "id": node_id,
        "label": node_id.rsplit(":", 1)[-1],
        "type": "symbol",
        "props": {"origin": origin, "blurb": "x" * filler},
    }


def _impact_envelope(n_direct: int = 6, n_transitive: int = 12) -> dict:
    direct = [{**_node(f"symbol:py:m:d{i}"), "hop": 1} for i in range(n_direct)]
    transitive = [
        {**_node(f"symbol:py:m:t{i}"), "hop": 2 + (i % 2)}
        for i in range(n_transitive)
    ]
    edges = [
        {"from": d["id"], "to": "file:seed", "type": "calls", "props": {}}
        for d in direct
    ] + [
        {"from": t["id"], "to": "file:seed", "type": "calls", "props": {}}
        for t in transitive
    ]
    return {
        "impact_version": 2,
        "target": {
            "input": "seed.py", "kind": "path",
            "resolved_nodes": ["file:seed"], "unresolved_inputs": [],
        },
        "depth": 3,
        "direct_dependents": direct,
        "transitive_dependents": transitive,
        "affected_surfaces": {"cli_commands": ["wd seed"], "mcp_tools": []},
        "risk_level": "HIGH",
        "edges": edges,
        "capabilities": {"languages": {}, "frameworks": {}},
        "warnings": {
            "unresolved_callsites": 0, "speculative_edges": 0,
            "stale_graph": None, "out_of_scope_inputs": [],
            "low_capability_inputs": [], "messages": [],
        },
    }




class ShapeImpactTest(unittest.TestCase):

    def test_bounds_the_payload_and_reports_the_drop(self) -> None:
        raw = _impact_envelope()
        shaped = shape_impact(raw, budget=1500)
        self.assertLessEqual(envelope_bytes(shaped), 1500)
        report = shaped["warnings"]["size_capped"]
        dropped = report["direct_dependents"] + report["transitive_dependents"]
        self.assertGreater(dropped, 0)
        kept = len(shaped["direct_dependents"]) + len(shaped["transitive_dependents"])
        self.assertEqual(kept + dropped, 6 + 12)

    def test_risk_and_surface_counts_survive_the_cap(self) -> None:
        """The lists shrink; the verdict does not (blast radius stays honest).

        bd gfpl narrowed what "the verdict" means. ``affected_surfaces`` used to
        be exempt from pruning wholesale, which is what let a 129 KB envelope
        past a 65 KB budget. Its *members* are prunable now; its per-bucket
        *counts* are not, and neither is ``risk_level``. That keeps the property
        ADR 0082 actually cared about -- a payload that was merely too big can
        never come back reporting a smaller radius or a lower risk.
        """
        raw = _impact_envelope()
        shaped = shape_impact(raw, budget=1500)
        self.assertEqual(shaped["risk_level"], raw["risk_level"])
        self.assertEqual(shaped["target"], raw["target"])
        self.assertEqual(
            shaped["affected_surface_counts"],
            {name: len(items) for name, items in raw["affected_surfaces"].items()},
        )

    def test_surface_counts_stay_full_even_when_every_member_is_dropped(self) -> None:
        """The count is the fact; the member list is only the detail."""
        raw = _impact_envelope()
        raw["affected_surfaces"] = {
            "cli_commands": [],
            "tests": [
                {"id": f"file:t{i}", "type": "file", "file": f"t{i}.py", "hop": 3}
                for i in range(40)
            ],
        }
        shaped = shape_impact(raw, budget=1500)
        self.assertLessEqual(envelope_bytes(shaped), 1500)
        self.assertEqual(shaped["affected_surfaces"]["tests"], [])
        self.assertEqual(shaped["affected_surface_counts"]["tests"], 40)
        self.assertEqual(
            shaped["warnings"]["size_capped"]["affected_surfaces"]["tests"], 40,
        )

    def test_surfaces_and_dependents_prune_in_one_pass(self) -> None:
        """Near dependents outlive far surface members, not the other way round.

        The defect bd gfpl found was not only that surfaces were unbounded, it
        was that ranking them separately emptied the dependents list to reclaim
        a few KB while leaving a much larger pile of far-hop surface entries
        standing. One ranking pass is what makes the surviving answer the
        *useful* one.
        """
        raw = _impact_envelope()
        raw["affected_surfaces"] = {
            "tests": [
                {"id": f"file:t{i}", "type": "file", "file": f"t{i}.py", "hop": 9}
                for i in range(60)
            ],
        }
        # Asserted as an ordering invariant across a range of budgets rather
        # than as one magic count: the contract is "hop 9 goes before hop 1",
        # and which exact budget still fits six dependents is an artifact of
        # the fixture's byte sizes, not something worth pinning.
        for budget in (2000, 2500, 3000, 4000):
            with self.subTest(budget=budget):
                shaped = shape_impact(raw, budget=budget)
                self.assertLessEqual(envelope_bytes(shaped), budget)
                report = shaped["warnings"]["size_capped"]
                self.assertGreater(report["affected_surfaces"]["tests"], 0)
                if report["direct_dependents"]:
                    self.assertEqual(
                        len(shaped["affected_surfaces"]["tests"]), 0,
                        "a hop-9 surface member outlived a hop-1 dependent",
                    )

    def test_over_budget_floor_is_reported_not_silent(self) -> None:
        """When pruning everything is not enough, the envelope says so.

        The budget is best-effort: a target header, the risk verdict and the
        surface counts have no lower bound the prune loop controls. Before bd
        gfpl that floor was silent, so an over-cap answer looked exactly like a
        comfortable one.
        """
        shaped = shape_impact(_impact_envelope(), budget=200)
        self.assertGreater(envelope_bytes(shaped), 200)
        self.assertIs(shaped["warnings"]["budget_exceeded"], True)
        self.assertTrue(
            any("STILL" in m for m in shaped["warnings"]["messages"]),
        )
        self.assertEqual(shaped["risk_level"], "HIGH")

    def test_budget_exceeded_is_false_when_the_payload_fits(self) -> None:
        """Always present, so a consumer never has to probe for it."""
        self.assertIs(
            shape_impact(_impact_envelope())["warnings"]["budget_exceeded"], False,
        )
        self.assertIs(
            shape_impact(_impact_envelope(), budget=1500)
            ["warnings"]["budget_exceeded"],
            False,
        )

    def test_size_capped_stays_a_map_of_counts(self) -> None:
        """No booleans among the counts: a consumer totals them (bd gfpl).

        ``budget_exceeded`` is a sibling of the report rather than a member of
        it. The first draft of this fix buried it inside, and the existing
        parity test -- which sums the report's values -- went silently wrong by
        one instead of failing loudly.
        """
        report = shape_impact(_impact_envelope(), budget=1500)["warnings"]["size_capped"]
        for key, value in report.items():
            if key == "affected_surfaces":
                self.assertTrue(all(isinstance(v, int) for v in value.values()))
                continue
            self.assertIsInstance(value, int)
            self.assertNotIsInstance(value, bool)

    def test_direct_dependents_outlive_transitive_ones(self) -> None:
        """Proximity is the priority: a 1-hop dependent is the last to go."""
        raw = _impact_envelope()
        shaped = shape_impact(raw, budget=3500)
        self.assertEqual(shaped["warnings"]["size_capped"]["direct_dependents"], 0)
        self.assertEqual(len(shaped["direct_dependents"]), 6)
        self.assertGreater(
            shaped["warnings"]["size_capped"]["transitive_dependents"], 0,
        )

    def test_farther_hops_are_dropped_before_nearer_ones(self) -> None:
        raw = _impact_envelope()
        shaped = shape_impact(raw, budget=3600)
        kept_hops = [n["hop"] for n in shaped["transitive_dependents"]]
        dropped_hops = [
            n["hop"] for n in raw["transitive_dependents"]
            if n["id"] not in {k["id"] for k in shaped["transitive_dependents"]}
        ]
        self.assertTrue(kept_hops)
        self.assertLessEqual(max(kept_hops), min(dropped_hops))

    def test_human_message_announces_the_cap(self) -> None:
        shaped = shape_impact(_impact_envelope(), budget=1500)
        self.assertTrue(
            any("read-budget" in m for m in shaped["warnings"]["messages"]),
        )

    def test_report_present_and_zeroed_when_nothing_is_dropped(self) -> None:
        shaped = shape_impact(_impact_envelope())
        self.assertEqual(
            shaped["warnings"]["size_capped"],
            {
                "direct_dependents": 0, "transitive_dependents": 0, "edges": 0,
                "affected_surfaces": {"cli_commands": 0, "mcp_tools": 0},
            },
        )
        self.assertEqual(shaped["warnings"]["messages"], [])

    def test_full_size_keeps_everything_and_still_reports(self) -> None:
        raw = _impact_envelope()
        shaped = shape_impact(raw, budget=1500, full_size=True)
        self.assertEqual(shaped["direct_dependents"], raw["direct_dependents"])
        self.assertEqual(shaped["transitive_dependents"], raw["transitive_dependents"])
        self.assertEqual(shaped["edges"], raw["edges"])
        self.assertEqual(shaped["affected_surfaces"], raw["affected_surfaces"])
        self.assertEqual(
            shaped["warnings"]["size_capped"],
            {
                "direct_dependents": 0, "transitive_dependents": 0, "edges": 0,
                "affected_surfaces": {"cli_commands": 0, "mcp_tools": 0},
            },
        )
        self.assertIs(shaped["warnings"]["budget_exceeded"], False)

    def test_dropped_dependents_take_their_edges_with_them(self) -> None:
        raw = _impact_envelope()
        shaped = shape_impact(raw, budget=1500)
        kept = {n["id"] for n in shaped["direct_dependents"]}
        kept |= {n["id"] for n in shaped["transitive_dependents"]}
        for edge in shaped["edges"]:
            self.assertIn(edge["from"], kept | {"file:seed"})
        self.assertEqual(
            shaped["warnings"]["size_capped"]["edges"],
            len(raw["edges"]) - len(shaped["edges"]),
        )

    def test_error_payload_passes_through_untouched(self) -> None:
        payload = {"error": "no such target"}
        self.assertEqual(shape_impact(payload), payload)

    def test_deterministic(self) -> None:
        a = shape_impact(_impact_envelope(), budget=1500)
        b = shape_impact(_impact_envelope(), budget=1500)
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
