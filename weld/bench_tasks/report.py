"""Markdown rendering for the comparative agent-task benchmark.

Split out of :mod:`weld.bench_tasks.compare` so the runner stays focused
on retrieval and scoring while this module owns presentation.
"""

from __future__ import annotations

import math
import statistics
from typing import Sequence

from weld.bench_tasks.compare import CompareResult


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.0f}%"


def _fmt_ms(value: float) -> str:
    return f"{value:.1f}"


def _summary_section(label: str, vals: list[float]) -> list[str]:
    if not vals:
        return [f"- {label}: n/a"]
    median = statistics.median(vals)
    ranked = sorted(vals)
    p90 = ranked[max(0, math.ceil(0.9 * len(ranked)) - 1)]
    return [f"- {label}: median {_fmt_pct(median)}, P90 {_fmt_pct(p90)}"]


def render_compare_report(results: Sequence[CompareResult]) -> str:
    """Render a markdown comparative report.

    The report exposes three metric classes -- token cost, accuracy, and
    latency -- with a per-task row plus aggregated summary sections that
    make the grep vs. weld delta easy to spot at a glance.
    """
    lines: list[str] = [
        "# Weld comparative agent benchmark",
        "",
        "Side-by-side comparison of the grep baseline and the weld "
        "retrieval stack for a set of realistic agent tasks.",
        "",
    ]
    if not results:
        lines += ["_Report contains no tasks._", ""]
        return "\n".join(lines).rstrip() + "\n"

    lines += [
        "| id  | category | grep tokens | weld tokens | token red. | "
        "grep F1 | weld F1 | grep ms | weld ms | latency red. |",
        "|-----|----------|-------------|---------------|------------|"
        "---------|-----------|---------|-----------|--------------|",
    ]
    for r in results:
        lines.append(
            "| {id} | {cat} | {gt} | {ct} | {tr} | {gf} | {cf} | "
            "{gms} | {cms} | {lr} |".format(
                id=r.task.id,
                cat=r.task.category,
                gt=r.grep_tokens,
                ct=r.weld_tokens,
                tr=_fmt_pct(r.token_reduction),
                gf=f"{r.grep_accuracy.f1:.2f}",
                cf=f"{r.weld_accuracy.f1:.2f}",
                gms=_fmt_ms(r.grep_latency_ms),
                cms=_fmt_ms(r.weld_latency_ms),
                lr=_fmt_pct(r.latency_reduction),
            )
        )

    token_reds = [
        r.token_reduction
        for r in results
        if r.token_reduction is not None
    ]
    latency_reds = [
        r.latency_reduction
        for r in results
        if r.latency_reduction is not None
    ]
    grep_f1 = [r.grep_accuracy.f1 for r in results]
    weld_f1 = [r.weld_accuracy.f1 for r in results]
    grep_recall = [r.grep_accuracy.recall for r in results]
    weld_recall = [r.weld_accuracy.recall for r in results]

    lines += [
        "",
        "## Summary",
        "",
        "### Token cost",
    ]
    lines += _summary_section("weld vs. grep", token_reds)
    lines += ["", "### Accuracy"]
    lines.append(
        f"- grep F1: median {statistics.median(grep_f1):.2f}, "
        f"recall median {statistics.median(grep_recall):.2f}"
    )
    lines.append(
        f"- weld F1: median {statistics.median(weld_f1):.2f}, "
        f"recall median {statistics.median(weld_recall):.2f}"
    )
    lines += ["", "### Latency"]
    lines += _summary_section("weld vs. grep (ms)", latency_reds)

    # Per-category breakdown
    by_cat: dict[str, list[CompareResult]] = {}
    for r in results:
        by_cat.setdefault(r.task.category, []).append(r)
    if len(by_cat) > 1:
        lines += ["", "### By category", ""]
        for cat in sorted(by_cat):
            rs = by_cat[cat]
            cat_tok = [
                r.token_reduction
                for r in rs
                if r.token_reduction is not None
            ]
            cat_f1_grep = statistics.median(
                [r.grep_accuracy.f1 for r in rs]
            )
            cat_f1_weld = statistics.median(
                [r.weld_accuracy.f1 for r in rs]
            )
            tok_med = (
                _fmt_pct(statistics.median(cat_tok))
                if cat_tok
                else "n/a"
            )
            lines.append(
                f"- **{cat}**: token red. median {tok_med}, "
                f"grep F1 {cat_f1_grep:.2f}, weld F1 {cat_f1_weld:.2f} "
                f"(n={len(rs)})"
            )

    return "\n".join(lines).rstrip() + "\n"
