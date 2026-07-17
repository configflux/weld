"""Render helpers for the first-run enrichment prompt (ADR 0052).

Split from :mod:`weld._first_run_enrich` to keep that module's
line-count well under the 400-line cap and to give the prompt text a
single, reviewable home. These helpers know about the
:class:`FirstRunDecision` dataclass but not about the env-var probes
or the persistence sentinel -- the policy/rendering split mirrors the
``_discover_strategies`` / ``_discover_summary`` separation.

All functions are pure: they take a decision in and return a string
out. Side effects (writing to stderr, prompting via ``input()``) stay
in :func:`weld._first_run_enrich.run_first_run`.
"""

from __future__ import annotations

from weld._enrichment_cost import (
    AUTO_FLOW_NODE_CAP,
    estimate_enrichment_cost,
    format_cost_range,
)
from weld._prime_coverage import MEANINGFUL_COVERAGE_THRESHOLD


def branch_a_prompt(provider: str, node_count: int) -> str:
    """Return the Branch A cost-honest prompt body.

    Lists the precedence chain so the user understands *why* this
    provider was picked (especially relevant when several env vars are
    set), states the node count, and quotes the static-table cost
    range. The trailing ``? [y/N] `` is the actual prompt the
    interactive caller passes to ``input()``.
    """
    estimate = estimate_enrichment_cost(provider, node_count)
    money = format_cost_range(estimate)
    return (
        f"Detected enrichment provider: {provider} "
        f"(precedence: WELD_ENRICH_PROVIDER -> ANTHROPIC_API_KEY -> "
        f"OPENAI_API_KEY -> ollama -> copilot-cli).\n"
        f"This will enrich ~{node_count} nodes.\n"
        f"Estimated cost: {money}.\n"
        f"Run enrichment now? [y/N] "
    )


def branch_a_above_cap_message(provider: str, node_count: int) -> str:
    """Return the Branch A over-cap message.

    Above the 2k-node auto-flow cap, weld refuses to prompt and
    instead points the user at the explicit batched flow. The cap is
    soft (see :func:`weld._enrichment_cost.auto_flow_within_cap`); the
    user can always run ``wd enrich`` directly with whatever batch
    size they want.
    """
    return (
        f"Detected enrichment provider: {provider}, "
        f"but the graph has {node_count} nodes (> "
        f"{AUTO_FLOW_NODE_CAP}-node auto-flow cap).\n"
        f"Run 'wd enrich --provider {provider} --batch=N' "
        f"directly to enrich in controlled batches.\n"
    )


def branch_b_message(agent: str) -> str:
    """Return the Branch B agent-host recommendation.

    Names the detected agent harness so the user can confirm it's the
    one they're actually using (vs a CI variable leaking through), and
    points at the canonical agent-direct entry point documented in
    ``.claude/commands/enrich-weld.md``.
    """
    return (
        f"No enrichment provider configured. Agent host detected "
        f"({agent}).\n"
        f"Recommend: run /enrich-weld to let the agent enrich the "
        f"graph directly (no API keys needed).\n"
    )


# Meaningful description-coverage (percent) at or above which the long-tail
# Branch C tip falls silent: past this line the graph is already well
# described and nudging to enrich would be inaccurate. Reuses ``wd prime``'s
# escalation threshold so both surfaces agree on what "well covered" means.
_TIP_SILENT_COVERAGE_PCT = MEANINGFUL_COVERAGE_THRESHOLD


def branch_c_message(coverage_pct: float = 0.0) -> str:
    """Return the Branch C silent tip, worded for *coverage_pct*.

    Branch C is the long-tail reminder for CI and shell users who have no
    provider and no agent. Because enrichment now survives ``wd discover``
    (ADR 0079), a rediscovered graph can already carry descriptions, so the
    tip is coverage-aware across three regimes:

    * ``0`` -- descriptions are genuinely empty; nudge to populate.
    * ``0 < pct < threshold`` -- coverage is sparse; report the percent and
      nudge to raise it (not "empty", which would be a lie).
    * ``>= threshold`` -- coverage is already good; stay silent (return
      ``""``) rather than nag inaccurately.

    Both nudging regimes name ``wd enrich`` and ``/enrich-weld`` so the user
    keeps a discoverable next step regardless of environment.
    """
    if coverage_pct >= _TIP_SILENT_COVERAGE_PCT:
        return ""
    if coverage_pct <= 0.0:
        state, action = "descriptions empty", "to populate"
    else:
        state = f"descriptions sparse ({coverage_pct:g}%)"
        action = "to raise coverage"
    return (
        f"Tip: {state}. Run 'wd enrich --provider <name>' {action} "
        "(or run /enrich-weld inside an agent harness).\n"
    )
