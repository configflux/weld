"""Unit tests for the product read command (:mod:`weld.read`, ADR 0082).

Layered coverage for the byte budget that sits on top of ADR 0078's neighbor
diet and for the brief edge-de-dangle + budget:

* query/context -- ``shape_read_envelope``: the ``size_capped`` annotation
  (appended in fixed order after the four diet reasons), deterministic byte
  pruning in ``neighbor_cap_sort_key`` order, dangling-edge drop, id-sorted
  survivors, the ``full`` (raw) and ``full_size`` (diet-only) escape hatches,
  matches never pruned, error / no-neighbor passthrough, determinism;
* brief -- ``shape_brief``: edge de-dangle to emitted bucket nodes, the byte
  budget dropping lowest-priority nodes with a visible ``warnings`` entry, the
  escape hatches, an unchanged key set, determinism.
"""

from __future__ import annotations

import json
import unittest

from weld._envelope_diet import OMISSION_REASONS
from weld.read import (
    DEFAULT_READ_BUDGET_BYTES,
    SIZE_CAPPED_REASON,
    _envelope_bytes,
    shape_brief,
    shape_read_envelope,
)


def _neighbor(node_id: str, origin: str = "project", *, blob: int = 0) -> dict:
    node = {"id": node_id, "type": "symbol", "props": {"origin": origin}}
    if blob:
        node["props"]["blob"] = "x" * blob
    return node


def _query_envelope(n_neighbors: int, *, blob: int = 0) -> dict:
    neighbors = [
        _neighbor(f"symbol:py:pkg:n{i:03d}", blob=blob) for i in range(n_neighbors)
    ]
    edges = [
        {"from": "M", "to": nb["id"], "type": "calls"} for nb in neighbors
    ]
    return {
        "query": "x",
        "matches": [_neighbor("M")],
        "neighbors": neighbors,
        "edges": edges,
    }


class ShapeReadEnvelopeContractTest(unittest.TestCase):
    """The ``size_capped`` annotation and generous-budget identity."""

    def test_size_capped_appended_after_diet_reasons_fixed_order(self) -> None:
        out = shape_read_envelope(_query_envelope(3))
        self.assertEqual(
            tuple(out["omitted_neighbors"].keys()),
            OMISSION_REASONS + (SIZE_CAPPED_REASON,),
        )

    def test_generous_budget_keeps_all_neighbors_size_capped_zero(self) -> None:
        env = _query_envelope(5)
        out = shape_read_envelope(env, budget=10_000_000)
        self.assertEqual(len(out["neighbors"]), 5)
        self.assertEqual(out["omitted_neighbors"][SIZE_CAPPED_REASON], 0)
        self.assertTrue(out["neighbors_filtered"])

    def test_default_budget_is_generous_constant(self) -> None:
        self.assertGreaterEqual(DEFAULT_READ_BUDGET_BYTES, 32_768)


class ShapeReadEnvelopeBudgetTest(unittest.TestCase):
    """The deterministic byte budget prunes and reports."""

    def test_budget_prunes_neighbors_and_counts_size_capped(self) -> None:
        env = _query_envelope(10, blob=1_000)
        full = shape_read_envelope(env, budget=10_000_000)
        budget = _envelope_bytes(full) // 2
        out = shape_read_envelope(env, budget=budget)
        kept = len(out["neighbors"])
        self.assertGreater(kept, 0)
        self.assertLess(kept, 10)
        self.assertEqual(out["omitted_neighbors"][SIZE_CAPPED_REASON], 10 - kept)
        self.assertLessEqual(_envelope_bytes(out), budget)

    def test_pruned_survivors_are_highest_priority_and_id_sorted(self) -> None:
        # All-project neighbors tie on origin/authority/confidence, so the cap
        # key falls to id ascending: the lowest ids survive, id-sorted.
        env = _query_envelope(10, blob=1_000)
        full = shape_read_envelope(env, budget=10_000_000)
        out = shape_read_envelope(env, budget=_envelope_bytes(full) // 2)
        ids = [n["id"] for n in out["neighbors"]]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(ids, [f"symbol:py:pkg:n{i:03d}" for i in range(len(ids))])

    def test_dangling_edges_dropped_with_pruned_neighbors(self) -> None:
        env = _query_envelope(10, blob=1_000)
        full = shape_read_envelope(env, budget=10_000_000)
        out = shape_read_envelope(env, budget=_envelope_bytes(full) // 2)
        kept_ids = {"M"} | {n["id"] for n in out["neighbors"]}
        for edge in out["edges"]:
            self.assertIn(edge["from"], kept_ids)
            self.assertIn(edge["to"], kept_ids)

    def test_matches_are_never_size_pruned(self) -> None:
        env = _query_envelope(6, blob=1_000)
        out = shape_read_envelope(env, budget=80)
        self.assertEqual(out["neighbors"], [])
        self.assertEqual([m["id"] for m in out["matches"]], ["M"])
        self.assertEqual(out["omitted_neighbors"][SIZE_CAPPED_REASON], 6)

    def test_budget_prune_is_deterministic(self) -> None:
        env = _query_envelope(10, blob=500)
        budget = _envelope_bytes(shape_read_envelope(env, budget=10_000_000)) // 2
        first = json.dumps(shape_read_envelope(env, budget=budget))
        second = json.dumps(shape_read_envelope(env, budget=budget))
        self.assertEqual(first, second)


class ShapeReadEnvelopeEscapeHatchTest(unittest.TestCase):
    """``full`` (raw) and ``full_size`` (diet-only) hatches, passthrough."""

    def test_full_returns_raw_envelope_unchanged(self) -> None:
        env = _query_envelope(3)
        env["neighbors"].append(_neighbor("STD", "stdlib"))
        out = shape_read_envelope(env, full=True, budget=10)
        self.assertIs(out, env)
        self.assertNotIn("neighbors_filtered", out)

    def test_full_size_diets_but_skips_byte_budget(self) -> None:
        env = _query_envelope(10, blob=1_000)
        out = shape_read_envelope(env, full_size=True, budget=80)
        self.assertEqual(len(out["neighbors"]), 10)
        self.assertEqual(out["omitted_neighbors"][SIZE_CAPPED_REASON], 0)
        self.assertTrue(out["neighbors_filtered"])

    def test_error_payload_passes_through(self) -> None:
        err = {"error": "node not found: nope"}
        self.assertIs(shape_read_envelope(err, budget=10), err)

    def test_no_neighbor_payload_passes_through(self) -> None:
        payload = {"path": None}
        self.assertIs(shape_read_envelope(payload, budget=10), payload)


class ShapeReadEnvelopeContextTest(unittest.TestCase):
    """A context envelope's focal ``node`` is an anchor, never pruned."""

    def test_focal_node_kept_under_tight_budget(self) -> None:
        env = {
            "node": {"id": "F", "type": "file", "props": {"origin": "project"}},
            "neighbors": [_neighbor(f"symbol:py:pkg:n{i}", blob=1_000) for i in range(5)],
            "edges": [
                {"from": "F", "to": f"symbol:py:pkg:n{i}", "type": "calls"}
                for i in range(5)
            ],
        }
        out = shape_read_envelope(env, budget=120)
        self.assertEqual(out["node"]["id"], "F")
        self.assertEqual(out["neighbors"], [])


def _brief_envelope(n_primary: int, *, blob: int = 0, extra_edges: bool = True) -> dict:
    primary = [_neighbor(f"entity:P{i:03d}", blob=blob) for i in range(n_primary)]
    edges = [
        {"from": "entity:P000", "to": p["id"], "type": "relates"}
        for p in primary[1:]
    ]
    if extra_edges:
        # Edges pointing at nodes NOT in any bucket -- the brief overflow class.
        edges += [
            {"from": "entity:P000", "to": f"symbol:ghost:{i}", "type": "calls"}
            for i in range(20)
        ]
    return {
        "brief_version": 2,
        "query": "x",
        "primary": primary,
        "interfaces": [],
        "docs": [],
        "build": [],
        "boundaries": [],
        "edges": edges,
        "provenance": {"graph_sha": None, "updated_at": None},
        "warnings": [],
    }


class ShapeBriefTest(unittest.TestCase):
    """Brief edge de-dangle + byte budget."""

    def test_dedangles_edges_to_emitted_bucket_nodes(self) -> None:
        out = shape_brief(_brief_envelope(5), budget=10_000_000)
        node_ids = {p["id"] for p in out["primary"]}
        for edge in out["edges"]:
            self.assertIn(edge["from"], node_ids)
            self.assertIn(edge["to"], node_ids)
        # The 20 ghost edges are gone; the 4 real intra-bucket edges remain.
        self.assertEqual(len(out["edges"]), 4)

    def test_key_set_unchanged(self) -> None:
        env = _brief_envelope(5)
        out = shape_brief(env, budget=10_000_000)
        self.assertEqual(set(out.keys()), set(env.keys()))

    def test_full_returns_raw_brief(self) -> None:
        env = _brief_envelope(5)
        self.assertIs(shape_brief(env, full=True, budget=10), env)

    def test_full_size_dedangles_but_keeps_all_nodes(self) -> None:
        out = shape_brief(_brief_envelope(8, blob=1_000), full_size=True, budget=80)
        self.assertEqual(len(out["primary"]), 8)
        # ghost edges still de-dangled even in full_size
        self.assertEqual(len(out["edges"]), 7)

    def test_budget_prunes_nodes_and_appends_warning(self) -> None:
        env = _brief_envelope(10, blob=1_000)
        full = shape_brief(env, budget=10_000_000)
        budget = _envelope_bytes(full) // 2
        out = shape_brief(env, budget=budget)
        self.assertLess(len(out["primary"]), 10)
        self.assertLessEqual(_envelope_bytes(out), budget)
        self.assertTrue(
            any("size_capped" in w for w in out["warnings"]),
            f"expected a size_capped warning in {out['warnings']}",
        )

    def test_shape_brief_is_deterministic(self) -> None:
        env = _brief_envelope(10, blob=500)
        budget = _envelope_bytes(shape_brief(env, budget=10_000_000)) // 2
        first = json.dumps(shape_brief(env, budget=budget))
        second = json.dumps(shape_brief(env, budget=budget))
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
