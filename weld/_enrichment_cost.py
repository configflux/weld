"""Static enrichment cost-estimation table (ADR 0052).

The first-run enrichment prompt shows a cost-honest range *before* the
user spends provider credits. The estimate is computed from a static
per-provider table: average input and output tokens per node, plus the
list-price input/output rates. Tokens-per-node is a back-of-the-envelope
figure from observed enrichment runs; the regression test in
``weld_enrichment_cost_test.py`` keeps the table from silently drifting
into nonsense.

Why static rather than dynamic:

* The user must see a concrete number *before* the first byte ships.
  Probing the provider for a real-time quote would itself cost money
  and require credentials -- defeating the point of an honest preview.
* List rates change rarely (quarter, year). When they do, this file is
  the single edit site. ADR 0052 mandates a release-time regression
  test against actual invoices on a fixture repo with <2x error.
* A single in-tree table means ``wd doctor`` and the discover prompt
  agree on the number. Two estimators in two places drift.

Returns are dollar ranges (``low_usd``, ``high_usd``) because the model
mix and per-node variance make a single point estimate misleading. The
range is ``+/- 50%`` around the central estimate to give the user a
realistic floor and ceiling.

This module is intentionally credential-free: it reads no API keys,
opens no network sockets, and never instantiates a provider. It is safe
to import from any context, including ``wd doctor``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# Per-node token budgets (input + output), aggregated across all node
# types. The first-run flow does not split by node class today: it sees
# only the total count from discover. If per-class accuracy is ever
# needed, the table can be split without changing the public surface.
_INPUT_TOKENS_PER_NODE: Final[int] = 1_400
_OUTPUT_TOKENS_PER_NODE: Final[int] = 180

# Provider list rates in USD per 1M tokens, current as of 2026-05.
# Format: (input_rate, output_rate). Kept here, not in each provider
# module, to keep this estimator a single readable table. A provider
# missing from this table falls back to the conservative anthropic
# row -- the prompt is allowed to be slightly over, never under.
_PROVIDER_RATES_USD_PER_1M: Final[dict[str, tuple[float, float]]] = {
    "anthropic": (3.0, 15.0),  # claude-sonnet-4-5 (default model)
    "openai": (0.25, 2.0),  # gpt-5.4-mini (default model)
    "ollama": (0.0, 0.0),  # local compute, no list price
    "copilot-cli": (0.0, 0.0),  # subscription-included, not metered
}

# Cap on the auto-flow node count. Repos above this run the explicit
# ``wd enrich --batch=N`` path (no cap, no prompt). The cap exists to
# prevent a five-figure surprise on a large monorepo; the user can
# always override by invoking ``wd enrich`` directly.
AUTO_FLOW_NODE_CAP: Final[int] = 2_000


@dataclass(frozen=True)
class CostEstimate:
    """A cost-honest range for an enrichment run.

    Attributes:
        provider: Canonical provider name (e.g. ``"anthropic"``).
        node_count: Number of nodes the run will iterate over.
        low_usd: Lower bound of the cost range, dollars.
        high_usd: Upper bound of the cost range, dollars.
        metered: ``True`` when the provider has a list price; ``False``
            for local/subscription providers where the dollar cost is
            zero from weld's perspective.
    """

    provider: str
    node_count: int
    low_usd: float
    high_usd: float
    metered: bool


def estimate_enrichment_cost(provider: str, node_count: int) -> CostEstimate:
    """Return a cost range for enriching *node_count* nodes via *provider*.

    The estimate uses the static table at the top of this module:
    average input/output tokens per node multiplied by the provider's
    list rate, then expanded to a ``+/- 50%`` band around the central
    estimate to communicate variance.

    Unknown providers fall back to the anthropic row (the most
    expensive registered provider) so the displayed range is never
    deceptively low for a name we have not characterised yet.

    A *node_count* of zero returns a zero-width range so callers can
    detect "no nodes to enrich" without special-casing.
    """
    if node_count < 0:
        raise ValueError("node_count must be >= 0")
    rates = _PROVIDER_RATES_USD_PER_1M.get(
        provider.strip().lower(),
        _PROVIDER_RATES_USD_PER_1M["anthropic"],
    )
    input_rate, output_rate = rates
    metered = (input_rate + output_rate) > 0.0
    if node_count == 0:
        return CostEstimate(
            provider=provider,
            node_count=0,
            low_usd=0.0,
            high_usd=0.0,
            metered=metered,
        )
    input_cost = node_count * _INPUT_TOKENS_PER_NODE / 1_000_000.0 * input_rate
    output_cost = node_count * _OUTPUT_TOKENS_PER_NODE / 1_000_000.0 * output_rate
    central = input_cost + output_cost
    return CostEstimate(
        provider=provider,
        node_count=node_count,
        low_usd=round(central * 0.5, 2),
        high_usd=round(central * 1.5, 2),
        metered=metered,
    )


def format_cost_range(estimate: CostEstimate) -> str:
    """Return a human-readable money string for *estimate*.

    Two shapes:

    * Local/subscription providers (``metered=False``): ``"included in
      your local/subscription cost"`` -- honest about the lack of a
      list price.
    * Metered providers: ``"$0.30 - $0.80"`` -- two-decimal dollar
      figures with a hyphen separator that copies and pastes cleanly.

    A near-free range (sub-$0.01) collapses to ``"<$0.01"`` so the user
    is not told ``"$0.00 - $0.00"`` and assume zero cost on a small
    repo when the real total is just below the rounding floor.
    """
    if not estimate.metered:
        return "included in your local/subscription cost"
    if estimate.high_usd < 0.01:
        return "<$0.01"
    return f"${estimate.low_usd:.2f} - ${estimate.high_usd:.2f}"


def auto_flow_within_cap(node_count: int) -> bool:
    """Return ``True`` when *node_count* fits the auto-flow cap.

    Above the cap (:data:`AUTO_FLOW_NODE_CAP`), the first-run prompt is
    suppressed and the user must run ``wd enrich --batch=N`` directly.
    The cap is intentionally soft: it gates only the prompt, not the
    enrichment itself.
    """
    if node_count < 0:
        raise ValueError("node_count must be >= 0")
    return node_count <= AUTO_FLOW_NODE_CAP
