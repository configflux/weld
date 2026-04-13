"""First-context quality scoring for cortex retrieval surfaces.

Measures whether ``cortex brief`` and ``cortex trace`` return relevant, well-bucketed
results for representative agent queries. Complements the token-cost harness
in :mod:`cortex.bench.runner` by adding **relevance** and **bucket coverage**
dimensions.

Three quality metrics per case:

  1. **Bucket hit rate** -- did each expected bucket come back non-empty?
  2. **Label recall** -- did at least one expected label appear in the top
     results (case-insensitive substring)?
  3. **Token budget** -- did the serialized result fit within the optional
     ``max_tokens`` cap?

The harness is on-demand, not a CI gate. It runs via ``cortex bench --quality``
and writes a markdown quality report.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from cortex._yaml import parse_yaml
from cortex.bench.runner import count_tokens

# -- Case loading -------------------------------------------------------------

@dataclass(frozen=True)
class QualityCase:
    """A single quality benchmark case loaded from the YAML fixture."""

    id: str
    query: str
    surface: str  # "brief" or "trace"
    expect_buckets: tuple[str, ...]
    expect_labels: tuple[str, ...]
    max_tokens: int | None = None
    category: str = "general"

def load_cases(path: Path) -> list[QualityCase]:
    """Load quality cases from a YAML fixture (top-level ``cases:`` list)."""
    data = parse_yaml(path.read_text(encoding="utf-8"))
    items = data.get("cases", []) if isinstance(data, dict) else data
    out: list[QualityCase] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        out.append(
            QualityCase(
                id=str(item.get("id", "")),
                query=str(item.get("query", "")),
                surface=str(item.get("surface", "brief")),
                expect_buckets=tuple(item.get("expect_buckets", [])),
                expect_labels=tuple(item.get("expect_labels", [])),
                max_tokens=item.get("max_tokens"),
                category=str(item.get("category", "general")),
            )
        )
    return out

# -- Quality result -----------------------------------------------------------

@dataclass
class BucketScore:
    """Score for a single expected bucket."""

    bucket: str
    expected: bool = True
    present: bool = False
    count: int = 0

@dataclass
class QualityResult:
    """Quality evaluation result for one case."""

    case: QualityCase
    bucket_scores: list[BucketScore] = field(default_factory=list)
    label_hits: dict[str, bool] = field(default_factory=dict)
    total_tokens: int = 0
    within_budget: bool | None = None  # None when no budget set
    raw_result: dict | None = None

    @property
    def bucket_hit_rate(self) -> float:
        """Fraction of expected buckets that were non-empty."""
        if not self.bucket_scores:
            return 1.0
        hits = sum(1 for s in self.bucket_scores if s.present)
        return hits / len(self.bucket_scores)

    @property
    def label_recall(self) -> float:
        """Fraction of expected labels found in the result."""
        if not self.label_hits:
            return 1.0
        hits = sum(1 for v in self.label_hits.values() if v)
        return hits / len(self.label_hits)

    @property
    def passed(self) -> bool:
        """True when all expected buckets hit and all labels found."""
        budget_ok = self.within_budget is not False
        return self.bucket_hit_rate == 1.0 and self.label_recall == 1.0 and budget_ok

# -- Evaluation ---------------------------------------------------------------

def _extract_labels(result: dict, surface: str) -> list[str]:
    """Collect all node labels from a brief or trace result."""
    labels: list[str] = []
    if surface == "brief":
        buckets = ("primary", "interfaces", "docs", "build", "boundaries")
    else:
        buckets = ("services", "interfaces", "contracts", "boundaries",
                   "verifications")
    for bucket in buckets:
        for node in result.get(bucket, []):
            label = node.get("label", "")
            if label:
                labels.append(label)
    return labels

def evaluate_case(case: QualityCase, root: Path) -> QualityResult:
    """Run one quality case against the live graph and score it."""
    from cortex.brief import brief as _brief
    from cortex.graph import Graph as _Graph
    from cortex.trace import trace as _trace

    g = _Graph(root)
    g.load()

    if case.surface == "trace":
        raw = _trace(g, term=case.query)
    else:
        raw = _brief(g, case.query, limit=20)

    serialized = json.dumps(raw, ensure_ascii=False, indent=2)
    total_tokens = count_tokens(serialized)

    # Score buckets
    bucket_scores: list[BucketScore] = []
    for bucket_name in case.expect_buckets:
        items = raw.get(bucket_name, [])
        bucket_scores.append(BucketScore(
            bucket=bucket_name,
            expected=True,
            present=len(items) > 0,
            count=len(items),
        ))

    # Score labels
    all_labels = _extract_labels(raw, case.surface)
    all_labels_lower = [lbl.lower() for lbl in all_labels]
    label_hits: dict[str, bool] = {}
    for expected_label in case.expect_labels:
        expected_lower = expected_label.lower()
        label_hits[expected_label] = any(
            expected_lower in lbl for lbl in all_labels_lower
        )

    # Token budget
    within_budget: bool | None = None
    if case.max_tokens is not None:
        within_budget = total_tokens <= case.max_tokens

    return QualityResult(
        case=case,
        bucket_scores=bucket_scores,
        label_hits=label_hits,
        total_tokens=total_tokens,
        within_budget=within_budget,
        raw_result=raw,
    )

def run_quality(
    cases: list[QualityCase],
    root: Path,
) -> list[QualityResult]:
    """Evaluate all quality cases and return results."""
    return [evaluate_case(case, root) for case in cases]

# -- Report rendering ---------------------------------------------------------

def _fmt_pct(value: float) -> str:
    return f"{value * 100:.0f}%"

def _pass_fail(passed: bool) -> str:
    return "PASS" if passed else "FAIL"

def render_quality_report(results: list[QualityResult]) -> str:
    """Render a markdown quality report."""
    lines: list[str] = [
        "# Cortex first-context quality benchmark",
        "",
        "Measures whether `cortex brief` and `cortex trace` return relevant, "
        "well-bucketed results for representative agent queries.",
        "",
        "See `cortex/bench/quality.py` for the scoring engine and "
        "`cortex/tests/bench/quality_cases.yaml` for the fixture.",
        "",
        "| id   | surface | category | bucket hit | label recall | "
        "tokens | budget | verdict |",
        "|------|---------|----------|------------|--------------|"
        "--------|--------|---------|",
    ]
    for r in results:
        budget_str = (
            _pass_fail(r.within_budget)
            if r.within_budget is not None
            else "n/a"
        )
        lines.append(
            "| {id} | {surface} | {cat} | {bhr} | {lr} | "
            "{tok} | {budget} | {verdict} |".format(
                id=r.case.id,
                surface=r.case.surface,
                cat=r.case.category,
                bhr=_fmt_pct(r.bucket_hit_rate),
                lr=_fmt_pct(r.label_recall),
                tok=r.total_tokens,
                budget=budget_str,
                verdict=_pass_fail(r.passed),
            )
        )

    # Summary by category
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    lines += [
        "",
        "## Summary",
        "",
        f"- Overall: {passed}/{total} cases passed "
        f"({_fmt_pct(passed / total) if total else 'n/a'})",
    ]
    by_cat: dict[str, list[QualityResult]] = {}
    for r in results:
        by_cat.setdefault(r.case.category, []).append(r)
    if by_cat:
        lines += ["", "### By category", ""]
        for cat in sorted(by_cat):
            cat_pass = sum(1 for r in by_cat[cat] if r.passed)
            cat_total = len(by_cat[cat])
            lines.append(
                f"- **{cat}**: {cat_pass}/{cat_total} passed"
            )

    # Surface breakdown
    by_surface: dict[str, list[QualityResult]] = {}
    for r in results:
        by_surface.setdefault(r.case.surface, []).append(r)
    if by_surface:
        lines += ["", "### By surface", ""]
        for surf in sorted(by_surface):
            s_pass = sum(1 for r in by_surface[surf] if r.passed)
            s_total = len(by_surface[surf])
            lines.append(
                f"- **{surf}**: {s_pass}/{s_total} passed"
            )

    # Failures detail
    failures = [r for r in results if not r.passed]
    if failures:
        lines += ["", "### Failures", ""]
        for r in failures:
            lines.append(f"- **{r.case.id}** ({r.case.query}):")
            missing_buckets = [
                s.bucket for s in r.bucket_scores if not s.present
            ]
            if missing_buckets:
                lines.append(
                    f"  - Missing buckets: {', '.join(missing_buckets)}"
                )
            missing_labels = [
                lbl for lbl, hit in r.label_hits.items() if not hit
            ]
            if missing_labels:
                lines.append(
                    f"  - Missing labels: {', '.join(missing_labels)}"
                )
            if r.within_budget is False:
                lines.append(
                    f"  - Over budget: {r.total_tokens} tokens "
                    f"(max {r.case.max_tokens})"
                )

    return "\n".join(lines).rstrip() + "\n"
