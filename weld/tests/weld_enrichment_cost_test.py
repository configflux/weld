"""Peer tests for the static enrichment cost-estimation table (ADR 0052).

The first-run prompt cites a dollar range it derives from a static
per-provider rate table. This test pins the table's behaviour:

* Each registered provider produces a deterministic, finite range.
* The range scales linearly with node count (the back-of-the-envelope
  formula in :mod:`weld._enrichment_cost` makes this an invariant, not
  an accident).
* Unknown providers fall back to a conservative metered range so the
  prompt never under-quotes.
* The ``+/- 50%`` band stays centered on the formula's central
  estimate.
* The 2k-node auto-flow cap is enforced by :func:`auto_flow_within_cap`.
* Negative node counts are rejected.
* The human-readable formatter renders metered, local, and near-free
  ranges correctly.

Drift between the table and observed provider invoices is monitored at
release time per ADR 0052; this peer test guards the *internal* shape.
"""

from __future__ import annotations

import unittest


from weld._enrichment_cost import (  # noqa: E402
    AUTO_FLOW_NODE_CAP,
    CostEstimate,
    auto_flow_within_cap,
    estimate_enrichment_cost,
    format_cost_range,
)
from weld.providers import _PROVIDER_LOADERS  # noqa: E402


class EstimateEnrichmentCostTest(unittest.TestCase):
    def test_known_metered_providers_have_nonzero_range(self) -> None:
        for name in ("anthropic", "openai"):
            estimate = estimate_enrichment_cost(name, 100)
            self.assertTrue(
                estimate.metered, f"provider {name!r} must be marked metered"
            )
            self.assertGreater(estimate.high_usd, 0.0)
            self.assertGreaterEqual(estimate.low_usd, 0.0)
            self.assertLess(estimate.low_usd, estimate.high_usd)

    def test_local_providers_are_unmetered(self) -> None:
        for name in ("ollama", "copilot-cli"):
            estimate = estimate_enrichment_cost(name, 500)
            self.assertFalse(estimate.metered, f"{name!r} must be unmetered")
            self.assertEqual(estimate.low_usd, 0.0)
            self.assertEqual(estimate.high_usd, 0.0)

    def test_scales_linearly_with_node_count(self) -> None:
        small = estimate_enrichment_cost("anthropic", 100)
        big = estimate_enrichment_cost("anthropic", 1000)
        # 10x nodes -> approximately 10x cost. Rounding at 2 decimals
        # can shift the ratio by up to about 5% on small ranges so we
        # use a 0.5 delta -- enough to catch a broken formula but
        # tolerant of cent-level rounding.
        self.assertAlmostEqual(
            big.high_usd / max(small.high_usd, 1e-9), 10.0, delta=0.5
        )
        self.assertAlmostEqual(
            big.low_usd / max(small.low_usd, 1e-9), 10.0, delta=0.5
        )

    def test_zero_nodes_returns_zero_range(self) -> None:
        estimate = estimate_enrichment_cost("anthropic", 0)
        self.assertEqual(estimate.node_count, 0)
        self.assertEqual(estimate.low_usd, 0.0)
        self.assertEqual(estimate.high_usd, 0.0)

    def test_unknown_provider_falls_back_to_anthropic_rates(self) -> None:
        anthropic = estimate_enrichment_cost("anthropic", 200)
        unknown = estimate_enrichment_cost("weld-deterministic-stub", 200)
        # Fallback must be metered (we conservatively assume a cost)
        # and match the anthropic row dollar-for-dollar.
        self.assertTrue(unknown.metered)
        self.assertEqual(unknown.low_usd, anthropic.low_usd)
        self.assertEqual(unknown.high_usd, anthropic.high_usd)

    def test_band_is_centred_around_formula(self) -> None:
        # +/- 50% band: low = 0.5 * central, high = 1.5 * central, so
        # high should be approximately 3x low (within 2-decimal
        # rounding). The shape is part of ADR 0052's "honest range"
        # contract.
        estimate = estimate_enrichment_cost("anthropic", 10000)
        self.assertAlmostEqual(estimate.high_usd, estimate.low_usd * 3.0, delta=0.1)

    def test_negative_node_count_rejected(self) -> None:
        with self.assertRaises(ValueError):
            estimate_enrichment_cost("anthropic", -1)

    def test_every_registered_provider_is_in_table(self) -> None:
        # Reviewer guard: adding a new provider to _PROVIDER_LOADERS
        # without giving it a cost row would silently fall back to
        # anthropic rates. That's safe, but it's also a documentation
        # gap. Force the new-provider author to either add a row or
        # explicitly note that the anthropic fallback is intentional.
        from weld._enrichment_cost import _PROVIDER_RATES_USD_PER_1M

        for name in _PROVIDER_LOADERS:
            self.assertIn(
                name,
                _PROVIDER_RATES_USD_PER_1M,
                f"provider {name!r} missing from cost table",
            )


class FormatCostRangeTest(unittest.TestCase):
    def test_metered_range_formats_as_dollars(self) -> None:
        text = format_cost_range(
            CostEstimate(
                provider="anthropic",
                node_count=100,
                low_usd=0.3,
                high_usd=0.8,
                metered=True,
            )
        )
        self.assertIn("$0.30", text)
        self.assertIn("$0.80", text)

    def test_unmetered_range_is_subscription_message(self) -> None:
        text = format_cost_range(
            CostEstimate(
                provider="ollama",
                node_count=500,
                low_usd=0.0,
                high_usd=0.0,
                metered=False,
            )
        )
        self.assertIn("included", text.lower())
        self.assertNotIn("$", text)

    def test_sub_penny_range_collapses_to_threshold(self) -> None:
        text = format_cost_range(
            CostEstimate(
                provider="anthropic",
                node_count=1,
                low_usd=0.0,
                high_usd=0.005,
                metered=True,
            )
        )
        self.assertEqual(text, "<$0.01")


class AutoFlowCapTest(unittest.TestCase):
    def test_cap_is_2000(self) -> None:
        # ADR 0052 mandates 2k. If this is ever changed, ADR 0052 must
        # be updated in the same change.
        self.assertEqual(AUTO_FLOW_NODE_CAP, 2_000)

    def test_within_cap_is_inclusive(self) -> None:
        self.assertTrue(auto_flow_within_cap(0))
        self.assertTrue(auto_flow_within_cap(1_000))
        self.assertTrue(auto_flow_within_cap(AUTO_FLOW_NODE_CAP))

    def test_above_cap_returns_false(self) -> None:
        self.assertFalse(auto_flow_within_cap(AUTO_FLOW_NODE_CAP + 1))
        self.assertFalse(auto_flow_within_cap(10_000))

    def test_negative_count_rejected(self) -> None:
        with self.assertRaises(ValueError):
            auto_flow_within_cap(-1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
