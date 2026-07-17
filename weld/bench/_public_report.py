"""Markdown rendering for ``wd bench --public`` (ADR 0059).

The output is intentionally deterministic so ``wd bench --public --verify``
can assert byte-identity between two consecutive runs. Wall-clock fields
and floating point numbers are normalized; only stable per-task facts
(files returned, token counts, status codes) and per-family aggregates
appear in the rendered report.

Sections: title, Methodology, Corpus manifest, Per-task results,
Per-family aggregates, optional C++ variant comparison, and Caveats.
Honest losing (ADR 0059) is enforced: the Caveats section is always
emitted and any weld loss is recorded plainly rather than hidden.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Iterable, Sequence

from weld.bench._public_runner import (
    AccuracyMetrics,
    AdapterResult,
    PublicRowResult,
    PublicRunReport,
    PublicTask,
    accuracy_metrics,
)


# --- Aggregates -------------------------------------------------------------


@dataclass(frozen=True)
class FamilyAggregate:
    """Per-family rollup for a single adapter."""

    family: str
    adapter: str
    n: int
    precision: float
    recall: float
    f1: float


def aggregate_by_family(
    rows: Sequence[PublicRowResult], adapter: str,
) -> dict[str, FamilyAggregate]:
    """Median precision / recall / F1 per family for ``adapter``.

    Tasks where the adapter reported ``unavailable`` or ``skipped`` are
    excluded from the rollup so an absent tool or a placeholder-SHA
    corpus entry does not depress its own averages.
    """
    by_family: dict[str, list[AccuracyMetrics]] = {}
    for row in rows:
        result = row.adapter_results.get(adapter)
        if result is None or result.status in ("unavailable", "skipped"):
            continue
        metric = accuracy_metrics(result.files, row.task.answer_files)
        by_family.setdefault(row.task.family, []).append(metric)

    out: dict[str, FamilyAggregate] = {}
    for family, metrics in by_family.items():
        out[family] = FamilyAggregate(
            family=family,
            adapter=adapter,
            n=len(metrics),
            precision=statistics.median([m.precision for m in metrics]),
            recall=statistics.median([m.recall for m in metrics]),
            f1=statistics.median([m.f1 for m in metrics]),
        )
    return out


# --- Caveats discovery ------------------------------------------------------


def _find_caveats(report: PublicRunReport) -> list[str]:
    """Build the Caveats lines (ADR 0059 'honest losing').

    A caveat is emitted whenever weld's F1 on a task is strictly less
    than another adapter's F1, OR whenever weld is reported as
    ``degraded``. Adapters that are ``unavailable`` are NOT treated as
    a win for weld -- those go into the methodology note instead.
    """
    lines: list[str] = []
    family_losses: dict[str, list[str]] = {}
    weld_degraded: list[str] = []

    for row in report.rows:
        weld = row.adapter_results.get("weld")
        if weld is None:
            continue
        # Skipped rows mean the bench did not run; not a loss, not a
        # degradation. The 'Skipped repos' section of the corpus manifest
        # surfaces these for the reader.
        if weld.status == "skipped":
            continue
        weld_f1 = accuracy_metrics(weld.files, row.task.answer_files).f1
        if weld.status == "degraded":
            weld_degraded.append(
                f"{row.task.repo_id}/{row.task.id} ({row.task.family}): "
                f"{weld.error or 'degraded'}"
            )
            continue
        for name, other in row.adapter_results.items():
            if name == "weld":
                continue
            if other.status != "ok":
                continue
            other_f1 = accuracy_metrics(
                other.files, row.task.answer_files,
            ).f1
            if other_f1 > weld_f1 + 1e-9:
                # Round to 2dp so the report bytes are stable.
                family_losses.setdefault(row.task.family, []).append(
                    f"{row.task.repo_id}/{row.task.id}: "
                    f"weld F1={weld_f1:.2f}, "
                    f"{name} F1={other_f1:.2f}"
                )

    if not family_losses and not weld_degraded:
        lines.append(
            "_No weld losses or degradations on this corpus run._"
        )
        return lines

    if family_losses:
        for family in sorted(family_losses):
            lines.append(f"### {family}")
            for entry in sorted(family_losses[family]):
                lines.append(f"- {entry}")
            lines.append("")
    if weld_degraded:
        lines.append("### Degraded weld runs")
        for entry in sorted(weld_degraded):
            lines.append(f"- {entry}")
    return lines


# --- Helpers ----------------------------------------------------------------


def _fmt_metric(value: float) -> str:
    """Stable two-decimal formatting for floats in the markdown."""
    return f"{value:.2f}"


def _status_marker(status: str) -> str:
    """One-token status label that survives the byte-identity check."""
    if status == "ok":
        return "ok"
    if status == "unavailable":
        return "unavailable"
    if status == "degraded":
        return "degraded"
    return status or "unknown"


def _adapter_metric_cell(
    result: AdapterResult, task: PublicTask,
) -> str:
    """Render one (task, adapter) cell as ``F1=X.YZ tokens=N status=s``.

    Two terminal cells short-circuit the metric block: ``unavailable``
    (adapter binary missing) and ``SKIPPED: <reason>`` (corpus entry
    was a placeholder SHA or the clone failed). Both are honest output:
    we DO NOT score 0/0/0 against the answer key when the adapter
    never ran.
    """
    if result.status == "unavailable":
        return "unavailable"
    if result.status == "skipped":
        # Mirror ADR 0059 'honest losing' -- a skipped row carries its
        # reason so a reader can tell at a glance why the task didn't
        # run. Keeping a single-line cell preserves table alignment.
        reason = result.error or "placeholder SHA"
        return f"SKIPPED: {reason}"
    metric = accuracy_metrics(result.files, task.answer_files)
    return (
        f"F1={_fmt_metric(metric.f1)} "
        f"P={_fmt_metric(metric.precision)} "
        f"R={_fmt_metric(metric.recall)} "
        f"tokens={result.tokens} "
        f"status={_status_marker(result.status)}"
    )


# --- Renderer ---------------------------------------------------------------


_METHODOLOGY = """\
This report is produced by ``wd bench --public``. The methodology is
defined as follows:

- Public corpus is SHA-pinned (see the Corpus manifest section).
- Each task has a ground-truth answer key (repo-relative files).
- Adapters under test: weld, grep, tree-sitter-cli, graphify. C++
  rows additionally exercise ``weld_libclang`` -- the libclang variant
  of the weld stack -- which runs only when the ``cpp-libclang`` extra
  is installed AND the repo's setup hook produced a
  ``compile_commands.json``; otherwise it is reported as ``unavailable``
  or ``SKIPPED: <reason>``.
- Metrics computed per task and per family:
  precision, recall, F1, tokens, and (where applicable) cost-per-task.
- An adapter whose external binary is missing is reported as
  ``unavailable`` and excluded from the per-family aggregate so its
  absence does not depress its own median scores.
- A repo whose corpus SHA is a placeholder (or whose clone failed) is
  reported as ``SKIPPED: <reason>`` per-task and excluded from
  per-family aggregates, again to keep numbers honest.
- Reproducibility: ``wd bench --public --verify`` re-runs the corpus
  and asserts byte-identical output to this report.
- Every metric where weld lost or was degraded is listed under
  Caveats (honest losing).
"""


def _render_corpus_manifest(report: PublicRunReport) -> list[str]:
    """Stable manifest section -- repo ids, families covered, task count.

    The pinned SHAs are intentionally NOT printed here -- they live in the
    YAML manifest, which is the source of truth. Surfacing them in
    multiple places would mean a refresh has to touch two artifacts.

    Repos whose every row is skipped are marked ``[skipped]`` in the
    status column so a reader knows the bench did not exercise them.
    """
    seen_repos: list[str] = []
    families_per_repo: dict[str, set[str]] = {}
    counts_per_repo: dict[str, int] = {}
    skipped_per_repo: dict[str, bool] = {}
    skip_reason_per_repo: dict[str, str] = {}
    for row in report.rows:
        rid = row.task.repo_id
        if rid not in seen_repos:
            seen_repos.append(rid)
        families_per_repo.setdefault(rid, set()).add(row.task.family)
        counts_per_repo[rid] = counts_per_repo.get(rid, 0) + 1
        # A repo is "skipped" if every adapter on every row is skipped.
        all_skipped = all(
            r.status == "skipped" for r in row.adapter_results.values()
        ) if row.adapter_results else False
        prior = skipped_per_repo.get(rid, True)
        skipped_per_repo[rid] = prior and all_skipped
        if all_skipped and rid not in skip_reason_per_repo:
            # Pick the first non-empty error string for stable reporting.
            for r in row.adapter_results.values():
                if r.error:
                    skip_reason_per_repo[rid] = r.error
                    break

    lines = [
        f"Corpus id: `{report.corpus_id}` (schema {report.schema_version}).",
        "",
        "| repo | tasks | families | status |",
        "|------|-------|----------|--------|",
    ]
    for rid in seen_repos:
        families = ", ".join(sorted(families_per_repo.get(rid, set())))
        if skipped_per_repo.get(rid, False):
            # Use a hyphen rather than nested parens so the column reads
            # cleanly (PLACEHOLDER_REASON already contains a parenthetical).
            reason = skip_reason_per_repo.get(rid, "placeholder")
            status = f"skipped - {reason}"
        else:
            status = "materialized"
        lines.append(
            f"| {rid} | {counts_per_repo.get(rid, 0)} | {families} | {status} |"
        )
    lines.append("")
    return lines


def _render_per_task_rows(
    rows: Sequence[PublicRowResult], adapter_order: Iterable[str],
) -> list[str]:
    adapters = list(adapter_order)
    header_cells = ["id", "family", "repo"] + adapters
    sep_cells = ["---"] * len(header_cells)
    lines = [
        "| " + " | ".join(header_cells) + " |",
        "|" + "|".join(sep_cells) + "|",
    ]
    for row in rows:
        cells = [row.task.id, row.task.family, row.task.repo_id]
        for name in adapters:
            result = row.adapter_results.get(name)
            if result is None:
                cells.append("missing")
            else:
                cells.append(_adapter_metric_cell(result, row.task))
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def _render_per_family_aggregate(
    report: PublicRunReport, adapter_order: Iterable[str],
) -> list[str]:
    adapters = list(adapter_order)
    # Collect all families that appeared so the header row is stable.
    families: set[str] = {row.task.family for row in report.rows}
    if not families:
        return ["_No tasks in this corpus._"]

    by_adapter = {
        name: aggregate_by_family(report.rows, name) for name in adapters
    }
    lines = [
        "Median F1 (precision / recall) per family per adapter. "
        "Tasks where an adapter was unavailable are excluded from its "
        "family rollup.",
        "",
        "| family | " + " | ".join(adapters) + " |",
        "|--------|" + "|".join(["---"] * len(adapters)) + "|",
    ]
    for family in sorted(families):
        cells = [family]
        for name in adapters:
            agg = by_adapter[name].get(family)
            if agg is None:
                cells.append("unavailable")
            else:
                cells.append(
                    f"F1={_fmt_metric(agg.f1)} "
                    f"P={_fmt_metric(agg.precision)} "
                    f"R={_fmt_metric(agg.recall)} "
                    f"(n={agg.n})"
                )
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def render_public_report(report: PublicRunReport) -> str:
    """Render the full public-benchmark markdown.

    Output is deterministic so ``wd bench --public --verify`` can assert
    byte-identity between two consecutive runs on the same corpus.
    """
    # The adapter column order is taken from the first row that has
    # results so column order matches what the runner produced.
    adapter_order: list[str] = []
    for row in report.rows:
        for name in row.adapter_results:
            if name not in adapter_order:
                adapter_order.append(name)
        if adapter_order:
            break

    lines: list[str] = [
        f"# Weld public benchmark ({report.weld_version})",
        "",
        "Published methodology and corpus results per the public "
        "benchmark methodology.",
        "",
        "## Methodology",
        "",
        _METHODOLOGY,
        "",
        "## Corpus manifest",
        "",
    ]
    lines += _render_corpus_manifest(report)

    lines += ["", "## Per-task results", ""]
    if report.rows:
        lines += _render_per_task_rows(report.rows, adapter_order)
    else:
        lines.append("_No tasks._")

    lines += ["", "## Per-family aggregates", ""]
    lines += _render_per_family_aggregate(report, adapter_order)

    from weld.bench._public_report_cpp import (
        render_cpp_variant_comparison,
    )
    cpp_section = render_cpp_variant_comparison(report)
    if cpp_section:
        lines += [""]
        lines += cpp_section

    lines += ["", "## Caveats", ""]
    lines += _find_caveats(report)

    return "\n".join(lines).rstrip() + "\n"


__all__ = [
    "FamilyAggregate",
    "aggregate_by_family",
    "render_public_report",
]
