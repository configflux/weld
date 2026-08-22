"""Transport reserve arithmetic + :func:`bound_dict_to_budget` (bd hwwo).

ADR 0082's amendment (2026-08-20) draws the line: the budget bounds the
bytes the *client* receives, not the handler's pre-stamp answer.
:data:`weld._read_budget.DEFAULT_READ_BUDGET_BYTES` is the contract; every
handler-side shaper (:mod:`weld.read`, :mod:`weld.read_traversal`,
:mod:`weld._read_impact`) now prunes to the smaller
:data:`weld._read_budget.EFFECTIVE_READ_BUDGET_BYTES` by default, reserving
room for the additive MCP transport stamps (``freshness``, ``children_status``)
applied after shaping. This module pins the arithmetic identity, that the
shapers actually wired the smaller default in, and the generic
:func:`weld._read_budget.bound_dict_to_budget` primitive that bounds
``children_status`` (unbounded in child count, so a fixed reserve alone
cannot bound it -- it needs its own fit-and-report loop).

The end-to-end "dispatched bytes fit the contract" proof lives in
:mod:`weld.tests.weld_dispatched_budget_test` (single-repo, all seven bounded
reads) and :mod:`weld.tests.weld_mcp_children_status_budget_test` (federated,
many children). This module is the unit layer under both.
"""

from __future__ import annotations

import unittest

from weld._read_budget import (
    CHILDREN_STATUS_RESERVE_BYTES,
    DEFAULT_READ_BUDGET_BYTES,
    EFFECTIVE_READ_BUDGET_BYTES,
    FRESHNESS_STAMP_RESERVE_BYTES,
    TRANSPORT_RESERVE_BYTES,
    bound_dict_to_budget,
    envelope_bytes,
)
from weld.read import shape_read_envelope
from weld.read_traversal import shape_callers


class ReserveArithmeticTest(unittest.TestCase):
    """The budget split is a closed identity, not independent constants."""

    def test_effective_plus_reserve_equals_the_contract(self) -> None:
        self.assertEqual(
            EFFECTIVE_READ_BUDGET_BYTES + TRANSPORT_RESERVE_BYTES,
            DEFAULT_READ_BUDGET_BYTES,
        )

    def test_reserve_splits_between_the_two_stamps(self) -> None:
        self.assertEqual(
            TRANSPORT_RESERVE_BYTES,
            FRESHNESS_STAMP_RESERVE_BYTES + CHILDREN_STATUS_RESERVE_BYTES,
        )

    def test_effective_budget_is_smaller_than_the_contract(self) -> None:
        # A reserve of zero would silently defeat the whole fix.
        self.assertLess(EFFECTIVE_READ_BUDGET_BYTES, DEFAULT_READ_BUDGET_BYTES)
        self.assertGreater(TRANSPORT_RESERVE_BYTES, 0)


class ShapersDefaultToTheEffectiveBudgetTest(unittest.TestCase):
    """The handler-side shapers actually prune to the smaller number.

    A synthetic envelope sized strictly between ``EFFECTIVE_READ_BUDGET_BYTES``
    and ``DEFAULT_READ_BUDGET_BYTES`` used to survive unpruned (the old
    default was the full contract number); post-fix it must be pruned, because
    the default budget a handler shapes to is now the smaller one.
    """

    @staticmethod
    def _neighbor(node_id: str, *, blob: int) -> dict:
        return {
            "id": node_id, "type": "symbol",
            "props": {"origin": "project", "blob": "x" * blob},
        }

    def _neighbor_sized_to(self, node_id: str, target_bytes: int) -> dict:
        """A single neighbor dict whose *own* serialized size is *target_bytes*.

        Calibrated by measuring the zero-blob overhead once, then padding --
        exact, not approximate, so the envelopes below land precisely in the
        (``EFFECTIVE_READ_BUDGET_BYTES``, ``DEFAULT_READ_BUDGET_BYTES``] gap
        the reserve carves out, regardless of key/id length elsewhere.
        """
        overhead = envelope_bytes(self._neighbor(node_id, blob=0))
        return self._neighbor(node_id, blob=max(0, target_bytes - overhead))

    def test_query_context_shaper_prunes_between_effective_and_default(self) -> None:
        # A midpoint target: past EFFECTIVE_READ_BUDGET_BYTES, comfortably
        # under DEFAULT_READ_BUDGET_BYTES -- exactly the zone the reserve
        # carves out of the old, unreserved default.
        midpoint = (EFFECTIVE_READ_BUDGET_BYTES + DEFAULT_READ_BUDGET_BYTES) // 2
        big = self._neighbor_sized_to("symbol:py:pkg:big", midpoint)
        env = {
            "matches": [{"id": "M", "type": "file", "props": {"origin": "project"}}],
            "neighbors": [big],
            "edges": [{"from": "M", "to": "symbol:py:pkg:big", "type": "calls"}],
        }
        raw_bytes = envelope_bytes(env)
        self.assertGreater(raw_bytes, EFFECTIVE_READ_BUDGET_BYTES)
        self.assertLessEqual(raw_bytes, DEFAULT_READ_BUDGET_BYTES)

        # Old behavior (explicit full-contract budget): nothing to prune.
        old_style = shape_read_envelope(dict(env), budget=DEFAULT_READ_BUDGET_BYTES)
        self.assertEqual(len(old_style["neighbors"]), 1)

        # New behavior (the default): this envelope no longer fits, so the
        # shaper must prune the one neighbor it has.
        out = shape_read_envelope(dict(env))
        self.assertEqual(out["neighbors"], [], "the oversized neighbor must be pruned")
        self.assertLessEqual(envelope_bytes(out), EFFECTIVE_READ_BUDGET_BYTES)

    def test_callers_shaper_prunes_between_effective_and_default(self) -> None:
        midpoint = (EFFECTIVE_READ_BUDGET_BYTES + DEFAULT_READ_BUDGET_BYTES) // 2
        big = self._neighbor_sized_to("symbol:py:pkg:big", midpoint)
        env = {
            "symbol": "symbol:py:pkg:target",
            "callers": [big],
            "edges": [
                {"from": "symbol:py:pkg:big", "to": "symbol:py:pkg:target",
                 "type": "calls"},
            ],
        }
        raw_bytes = envelope_bytes(env)
        self.assertGreater(raw_bytes, EFFECTIVE_READ_BUDGET_BYTES)
        self.assertLessEqual(raw_bytes, DEFAULT_READ_BUDGET_BYTES)

        old_style = shape_callers(dict(env), budget=DEFAULT_READ_BUDGET_BYTES)
        self.assertEqual(len(old_style["callers"]), 1)

        out = shape_callers(dict(env))
        self.assertEqual(out["callers"], [])
        self.assertEqual(out["size_capped"]["callers"], 1)
        self.assertLessEqual(envelope_bytes(out), EFFECTIVE_READ_BUDGET_BYTES)


class FreshnessReserveSanityTest(unittest.TestCase):
    """The freshness object's realistic byte cost stays inside its reserve.

    ``freshness_for`` (:mod:`weld._mcp_read`) returns exactly these three
    keys; this pins the *shape*, not the live git plumbing, so it stays
    hermetic. A regression here (a new key added to the freshness object
    without revisiting the reserve) fails loudly instead of silently eating
    into the headroom :func:`ShapersDefaultToTheEffectiveBudgetTest` above
    relies on.
    """

    def _stamped_cost(self, branch: str | None, commits_behind: int) -> int:
        base = {"matches": []}
        stamped = {
            **base,
            "freshness": {
                "stale": True, "commits_behind": commits_behind, "branch": branch,
            },
        }
        return envelope_bytes(stamped) - envelope_bytes(base)

    def test_typical_branch_name_fits_the_reserve(self) -> None:
        cost = self._stamped_cost("worktree-agent-a9f4d407bdb4426cd", 3)
        self.assertLess(cost, FRESHNESS_STAMP_RESERVE_BYTES)

    def test_long_realistic_branch_name_fits_the_reserve(self) -> None:
        # Longer than any branch name in this repo's own history, and still
        # short of the ~170-character ceiling the reserve was sized against.
        cost = self._stamped_cost(
            "feature/JIRA-12345-a-fairly-long-descriptive-branch-name-2026", 123456,
        )
        self.assertLess(cost, FRESHNESS_STAMP_RESERVE_BYTES)

    def test_no_branch_fits_the_reserve(self) -> None:
        self.assertLess(self._stamped_cost(None, 0), FRESHNESS_STAMP_RESERVE_BYTES)


class BoundDictToBudgetTest(unittest.TestCase):
    """The generic mapping-fit primitive :func:`bound_dict_to_budget` uses."""

    @staticmethod
    def _items(n: int, *, blob: int = 20) -> dict:
        return {
            f"child-{i:03d}": {"status": "present", "graph_path": "x" * blob}
            for i in range(n)
        }

    def test_empty_mapping_is_untouched(self) -> None:
        kept, omitted = bound_dict_to_budget({}, 1_000, key="children_status")
        self.assertEqual(kept, {})
        self.assertEqual(omitted, 0)

    def test_everything_fits_under_a_generous_budget(self) -> None:
        items = self._items(10)
        kept, omitted = bound_dict_to_budget(items, 1_000_000, key="children_status")
        self.assertEqual(kept, items)
        self.assertEqual(omitted, 0)

    def test_a_tight_budget_drops_from_the_end_and_reports_the_count(self) -> None:
        items = self._items(50)
        kept, omitted = bound_dict_to_budget(items, 800, key="children_status")
        self.assertGreater(omitted, 0)
        self.assertLess(len(kept), 50)
        self.assertEqual(len(kept) + omitted, 50)
        # Deterministic order (ADR 0012): the surviving prefix is the first
        # N keys in the caller's own order, never a reordering.
        self.assertEqual(list(kept), list(items)[: len(kept)])

    def test_result_never_breaches_the_budget_once_wrapped_under_its_key(self) -> None:
        items = self._items(50)
        budget = 900
        kept, _omitted = bound_dict_to_budget(items, budget, key="children_status")
        self.assertLessEqual(
            envelope_bytes({"children_status": kept}), budget,
        )

    def test_deterministic(self) -> None:
        items = self._items(40)
        first = bound_dict_to_budget(items, 600, key="children_status")
        second = bound_dict_to_budget(items, 600, key="children_status")
        self.assertEqual(first, second)

    def test_nothing_fits_returns_empty_and_full_omitted_count(self) -> None:
        items = self._items(5, blob=500)
        kept, omitted = bound_dict_to_budget(items, 10, key="children_status")
        self.assertEqual(kept, {})
        self.assertEqual(omitted, 5)


if __name__ == "__main__":
    unittest.main()
