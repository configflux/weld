"""Bounded read envelope for the traversal surfaces (ADR 0082).

Covers the shared byte-budget primitive (:mod:`weld._read_budget`) and the four
shapers in :mod:`weld.read_traversal`. The contract under test is the one
ADR 0082 states for ``query`` / ``context`` / ``brief`` and this change extends
to ``impact`` / ``callers`` / ``references`` / ``trace``:

* the shaped payload fits the byte budget;
* nothing is dropped silently -- every drop is counted in the payload;
* ``full_size`` returns the unbounded payload;
* the result is deterministic (ADR 0012);
* and for ``impact`` specifically, the *verdict* fields survive intact: a
  payload that was merely too big must never come back reporting a smaller
  blast radius than the change actually has.
"""

from __future__ import annotations

import unittest

from weld._read_budget import (
    DEFAULT_READ_BUDGET_BYTES,
    dedangle,
    envelope_bytes,
    fit_buckets,
    largest_fitting_prefix,
)
from weld.read_traversal import (
    shape_callers,
    shape_references,
    shape_trace,
)


def _node(node_id: str, *, origin: str = "project", filler: int = 40) -> dict:
    """A node dict fat enough that a handful of them blow a small budget."""
    return {
        "id": node_id,
        "label": node_id.rsplit(":", 1)[-1],
        "type": "symbol",
        "props": {"origin": origin, "blurb": "x" * filler},
    }


class ReadBudgetPrimitiveTest(unittest.TestCase):
    """The shared primitive: total order, honest reporting, self-accounting."""

    def test_largest_fitting_prefix_finds_the_boundary(self) -> None:
        self.assertEqual(largest_fitting_prefix(100, lambda k: k <= 37), 37)
        self.assertEqual(largest_fitting_prefix(100, lambda k: True), 100)
        self.assertEqual(largest_fitting_prefix(100, lambda k: False), 0)

    def test_dedangle_drops_edges_with_a_missing_endpoint(self) -> None:
        edges = [
            {"from": "a", "to": "b"}, {"from": "a", "to": "gone"},
            {"from": "gone", "to": "b"},
        ]
        self.assertEqual(dedangle(edges, {"a", "b"}), [{"from": "a", "to": "b"}])

    def test_protected_ids_are_derived_from_edges_when_not_given(self) -> None:
        """Endpoints outside the droppable buckets stay valid (impact seeds)."""
        envelope = {
            "items": [_node("symbol:py:m:a")],
            "edges": [{"from": "symbol:py:m:a", "to": "file:seed"}],
        }
        shaped, dropped, dropped_edges = fit_buckets(
            envelope, buckets=("items",), budget=DEFAULT_READ_BUDGET_BYTES,
            rank_key=lambda _b, n: (n["id"],),
        )
        self.assertEqual(dropped, {"items": 0})
        self.assertEqual(dropped_edges, 0)
        self.assertEqual(len(shaped["edges"]), 1)

    def test_report_is_counted_against_the_budget(self) -> None:
        """The annotation cannot push the answer back over the budget."""
        envelope = {"items": [_node(f"symbol:py:m:{i}") for i in range(40)],
                    "edges": []}

        def annotate(env: dict, dropped: dict, dropped_edges: int) -> dict:
            return {**env, "size_capped": {**dropped, "edges": dropped_edges}}

        shaped, _dropped, _edges = fit_buckets(
            envelope, buckets=("items",), budget=900,
            rank_key=lambda _b, n: (n["id"],), annotate=annotate,
        )
        self.assertLessEqual(envelope_bytes(shaped), 900)
        self.assertIn("size_capped", shaped)

    def test_survivors_keep_their_original_bucket_order(self) -> None:
        items = [_node(f"symbol:py:m:{i}") for i in (3, 1, 2)]
        envelope = {"items": items, "edges": []}
        shaped, _d, _e = fit_buckets(
            envelope, buckets=("items",), budget=DEFAULT_READ_BUDGET_BYTES,
            rank_key=lambda _b, n: (n["id"],),
        )
        self.assertEqual([i["id"] for i in shaped["items"]],
                         [i["id"] for i in items])

    def test_id_less_items_prune_one_at_a_time(self) -> None:
        """Selection is positional, so items without an id do not collide."""
        envelope = {"hits": [{"path": f"p{i}", "score": i} for i in range(10)]}
        shaped, dropped, _e = fit_buckets(
            envelope, buckets=("hits",), budget=200, edges_key=None,
            rank_key=lambda _b, h: (-h["score"], h["path"]),
        )
        self.assertGreater(dropped["hits"], 0)
        self.assertLess(len(shaped["hits"]), 10)
        self.assertLessEqual(envelope_bytes(shaped), 200)


class ShapeCallersTest(unittest.TestCase):

    def _envelope(self, n: int = 30) -> dict:
        callers = [_node(f"symbol:py:m:c{i}") for i in range(n)]
        return {
            "symbol": "symbol:py:m:helper", "depth": 2, "callers": callers,
            "edges": [
                {"from": c["id"], "to": "symbol:py:m:helper", "type": "calls"}
                for c in callers
            ],
        }

    def test_bounds_and_reports(self) -> None:
        shaped = shape_callers(self._envelope(), budget=1200)
        self.assertLessEqual(envelope_bytes(shaped), 1200)
        self.assertGreater(shaped["size_capped"]["callers"], 0)
        self.assertEqual(
            shaped["size_capped"]["callers"] + len(shaped["callers"]), 30,
        )

    def test_report_key_is_always_present(self) -> None:
        """A consumer never has to probe for the field (invariant key set)."""
        shaped = shape_callers(self._envelope(n=1))
        self.assertEqual(shaped["size_capped"], {"callers": 0, "edges": 0})

    def test_full_size_is_unbounded(self) -> None:
        raw = self._envelope()
        shaped = shape_callers(raw, budget=1200, full_size=True)
        self.assertEqual(shaped["callers"], raw["callers"])
        self.assertEqual(shaped["size_capped"], {"callers": 0, "edges": 0})

    def test_project_callers_outlive_stdlib_ones(self) -> None:
        envelope = {
            "symbol": "s", "depth": 1, "edges": [],
            "callers": [
                _node("symbol:py:std:a", origin="stdlib"),
                _node("symbol:py:m:b", origin="project"),
            ],
        }
        shaped = shape_callers(envelope, budget=430)
        self.assertEqual([c["id"] for c in shaped["callers"]], ["symbol:py:m:b"])

    def test_error_payload_passes_through_untouched(self) -> None:
        payload = {
            "symbol": "nope", "depth": 1, "callers": [], "edges": [],
            "error": "node not found: nope",
        }
        self.assertEqual(shape_callers(payload), payload)

    def test_seeds_and_targets_stay_within_budget(self) -> None:
        """bd jz65r: ``callers()``'s own ``seeds`` (top-level) and
        per-caller ``targets`` (depth 1) are new keys on an existing
        envelope/rows, not a new droppable bucket -- the same free-riding
        argument bd nyoks verified for ``references()``'s ``targets``
        (weld_read_traversal_test.ShapeReferencesTest
        .test_targets_field_on_callers_stays_within_budget). The byte
        budget must still fit and prune correctly with both present, and
        every caller that survives shaping must keep its ``targets``."""
        envelope = self._envelope(n=20)
        envelope["seeds"] = ["symbol:py:m:helper", "symbol:unresolved:helper"]
        for i, caller in enumerate(envelope["callers"]):
            seed = envelope["seeds"][i % 2]
            caller["targets"] = [seed]
        shaped = shape_callers(envelope, budget=1200)
        self.assertLessEqual(envelope_bytes(shaped), 1200)
        self.assertGreater(len(shaped["callers"]), 0)
        self.assertEqual(shaped["seeds"], envelope["seeds"])
        for caller in shaped["callers"]:
            self.assertIn("targets", caller)


class ShapeReferencesTest(unittest.TestCase):

    def _envelope(self) -> dict:
        matches = [_node("symbol:py:m:helper")]
        callers = [_node(f"symbol:py:m:c{i}") for i in range(20)]
        return {
            "symbol": "helper",
            "matches": matches,
            "callers": callers,
            "edges": [
                {"from": c["id"], "to": "symbol:py:m:helper", "type": "calls"}
                for c in callers
            ],
            "files": [
                {"path": f"src/f{i}.py", "tokens": ["helper"], "score": i}
                for i in range(20)
            ],
        }

    def test_bounds_and_reports_every_bucket(self) -> None:
        shaped = shape_references(self._envelope(), budget=1500)
        self.assertLessEqual(envelope_bytes(shaped), 1500)
        self.assertEqual(
            set(shaped["size_capped"]), {"matches", "callers", "files", "edges"},
        )

    def test_matches_are_the_last_thing_dropped(self) -> None:
        """The resolution answer outranks the textual hits that flood it."""
        shaped = shape_references(self._envelope(), budget=1500)
        self.assertEqual(shaped["size_capped"]["matches"], 0)
        self.assertEqual(len(shaped["matches"]), 1)
        self.assertGreater(shaped["size_capped"]["files"], 0)

    def test_missing_files_bucket_does_not_shift_priorities(self) -> None:
        envelope = self._envelope()
        del envelope["files"]
        shaped = shape_references(envelope, budget=1500)
        self.assertEqual(set(shaped["size_capped"]), {"matches", "callers", "edges"})
        self.assertEqual(shaped["size_capped"]["matches"], 0)

    def test_full_size_is_unbounded(self) -> None:
        raw = self._envelope()
        shaped = shape_references(raw, budget=1500, full_size=True)
        self.assertEqual(shaped["callers"], raw["callers"])
        self.assertEqual(shaped["files"], raw["files"])

    def test_targets_field_on_callers_stays_within_budget(self) -> None:
        """A caller's per-match ``targets`` list (bd nyoks) is one more key
        on an existing row, not a new row -- the byte budget must still fit
        and prune correctly with it present, and the field must survive
        shaping on every caller that fits."""
        envelope = self._envelope()
        for i, caller in enumerate(envelope["callers"]):
            caller["targets"] = [f"symbol:py:m:match{i % 3}"]
        shaped = shape_references(envelope, budget=1500)
        self.assertLessEqual(envelope_bytes(shaped), 1500)
        self.assertGreater(len(shaped["callers"]), 0)
        for caller in shaped["callers"]:
            self.assertIn("targets", caller)


class ShapeTraceTest(unittest.TestCase):

    def _envelope(self, n: int = 20) -> dict:
        services = [_node(f"service:s{i}") for i in range(n)]
        return {
            "trace_version": 1,
            "anchor": {"kind": "term", "term": "checkout"},
            "services": services, "interfaces": [], "contracts": [],
            "boundaries": [], "verifications": [],
            "edges": [
                {"from": s["id"], "to": "service:s0", "type": "calls"}
                for s in services
            ],
            "provenance": {"graph_sha": None, "updated_at": None},
            "warnings": [],
        }

    def test_a_slice_under_budget_is_returned_untouched(self) -> None:
        raw = self._envelope(n=2)
        self.assertEqual(shape_trace(raw), raw)

    def test_bounds_and_warns_when_over_budget(self) -> None:
        shaped = shape_trace(self._envelope(), budget=1200)
        self.assertLessEqual(envelope_bytes(shaped), 1200)
        self.assertTrue(any("read-budget" in w for w in shaped["warnings"]))
        self.assertLess(len(shaped["services"]), 20)

    def test_full_size_is_unbounded(self) -> None:
        raw = self._envelope()
        self.assertEqual(shape_trace(raw, budget=1200, full_size=True), raw)


if __name__ == "__main__":
    unittest.main()
