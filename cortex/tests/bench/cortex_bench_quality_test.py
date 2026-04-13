"""Tests for the cortex first-context quality benchmark (project-xoq.7.1).

These tests build a synthetic repo with interaction-surface nodes
(grpc routes, event channels, boundaries, services, contracts) and
verify that:

  - Quality cases load from the YAML fixture format.
  - ``evaluate_case`` correctly scores bucket hits and label recall
    for both ``brief`` and ``trace`` surfaces.
  - ``run_quality`` returns one ``QualityResult`` per case.
  - ``render_quality_report`` produces the documented markdown shape.
  - Token budget enforcement works.
  - The checked-in ``quality_cases.yaml`` fixture parses without error.

The tests do NOT depend on a live repo graph -- they use a synthetic
graph with known node types and labels.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

# Ensure cortex package is importable from the repo root.
_repo_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

# The bench test helpers live alongside this file; add the bench dir
# to sys.path so the import works without cortex/tests being a package.
_bench_dir = str(Path(__file__).resolve().parent)
if _bench_dir not in sys.path:
    sys.path.insert(0, _bench_dir)

from cortex.bench.quality import (  # noqa: E402
    BucketScore,
    QualityCase,
    QualityResult,
    evaluate_case,
    load_cases,
    render_quality_report,
    run_quality,
)
from bench_test_helpers import (  # noqa: E402
    FIXTURE_CASES_YAML,
    setup_interaction_repo,
)

class CaseLoaderTest(unittest.TestCase):
    def test_load_cases_from_yaml(self) -> None:
        path = Path(tempfile.mktemp(suffix=".yaml"))
        path.write_text(FIXTURE_CASES_YAML, encoding="utf-8")
        try:
            cases = load_cases(path)
        finally:
            path.unlink()
        self.assertEqual(len(cases), 4)
        self.assertEqual(cases[0].id, "c01")
        self.assertEqual(cases[0].surface, "brief")
        self.assertEqual(cases[0].expect_buckets, ("interfaces",))
        self.assertEqual(cases[0].expect_labels, ("grpc",))
        self.assertEqual(cases[2].surface, "trace")

    def test_real_fixture_loads(self) -> None:
        path = (
            Path(_repo_root) / "cortex" / "tests" / "bench"
            / "quality_cases.yaml"
        )
        cases = load_cases(path)
        surfaces = {c.surface for c in cases}
        self.assertIn("brief", surfaces)
        self.assertIn("trace", surfaces)
        self.assertGreaterEqual(len(cases), 8)

class QualityResultTest(unittest.TestCase):
    def test_bucket_hit_rate_all_present(self) -> None:
        r = QualityResult(
            case=QualityCase(
                id="x", query="x", surface="brief",
                expect_buckets=("primary",), expect_labels=(),
            ),
            bucket_scores=[
                BucketScore(bucket="primary", present=True, count=2),
            ],
        )
        self.assertEqual(r.bucket_hit_rate, 1.0)
        self.assertTrue(r.passed)

    def test_bucket_hit_rate_partial(self) -> None:
        r = QualityResult(
            case=QualityCase(
                id="x", query="x", surface="brief",
                expect_buckets=("primary", "docs"),
                expect_labels=(),
            ),
            bucket_scores=[
                BucketScore(bucket="primary", present=True, count=2),
                BucketScore(bucket="docs", present=False, count=0),
            ],
        )
        self.assertEqual(r.bucket_hit_rate, 0.5)
        self.assertFalse(r.passed)

    def test_label_recall(self) -> None:
        r = QualityResult(
            case=QualityCase(
                id="x", query="x", surface="brief",
                expect_buckets=(), expect_labels=("foo", "bar"),
            ),
            label_hits={"foo": True, "bar": False},
        )
        self.assertEqual(r.label_recall, 0.5)
        self.assertFalse(r.passed)

    def test_budget_enforcement(self) -> None:
        r = QualityResult(
            case=QualityCase(
                id="x", query="x", surface="brief",
                expect_buckets=(), expect_labels=(),
                max_tokens=10,
            ),
            total_tokens=20,
            within_budget=False,
        )
        self.assertFalse(r.passed)

    def test_no_budget_passes(self) -> None:
        r = QualityResult(
            case=QualityCase(
                id="x", query="x", surface="brief",
                expect_buckets=(), expect_labels=(),
            ),
            total_tokens=999,
            within_budget=None,
        )
        self.assertTrue(r.passed)

class EvaluateCaseTest(unittest.TestCase):
    def test_brief_grpc_finds_interfaces(self) -> None:
        root = Path(setup_interaction_repo())
        case = QualityCase(
            id="t1", query="grpc", surface="brief",
            expect_buckets=("interfaces",),
            expect_labels=("grpc",),
        )
        result = evaluate_case(case, root)
        self.assertGreater(result.total_tokens, 0)
        self.assertTrue(
            result.bucket_hit_rate > 0,
            f"Expected interfaces bucket hit; "
            f"scores={result.bucket_scores}",
        )

    def test_brief_store_finds_primary(self) -> None:
        root = Path(setup_interaction_repo())
        case = QualityCase(
            id="t2", query="Store", surface="brief",
            expect_buckets=("primary",),
            expect_labels=("Store",),
        )
        result = evaluate_case(case, root)
        self.assertEqual(result.label_recall, 1.0)

    def test_trace_store_returns_envelope(self) -> None:
        root = Path(setup_interaction_repo())
        case = QualityCase(
            id="t3", query="Store", surface="trace",
            expect_buckets=(),
            expect_labels=(),
        )
        result = evaluate_case(case, root)
        self.assertIsNotNone(result.raw_result)
        self.assertIn("trace_version", result.raw_result)
        self.assertGreater(result.total_tokens, 0)

    def test_brief_policy_finds_docs(self) -> None:
        root = Path(setup_interaction_repo())
        case = QualityCase(
            id="t4", query="policy", surface="brief",
            expect_buckets=("docs",),
            expect_labels=("security_policy",),
        )
        result = evaluate_case(case, root)
        self.assertTrue(
            result.bucket_hit_rate > 0,
            f"Expected docs bucket; scores={result.bucket_scores}",
        )

class RunQualityTest(unittest.TestCase):
    def test_returns_one_result_per_case(self) -> None:
        root = Path(setup_interaction_repo())
        cases = [
            QualityCase(
                id="a", query="grpc", surface="brief",
                expect_buckets=("interfaces",), expect_labels=(),
            ),
            QualityCase(
                id="b", query="Store", surface="trace",
                expect_buckets=(), expect_labels=(),
            ),
        ]
        results = run_quality(cases, root)
        self.assertEqual(len(results), 2)
        for r in results:
            self.assertIsInstance(r, QualityResult)
            self.assertGreaterEqual(r.total_tokens, 0)

class RenderQualityReportTest(unittest.TestCase):
    def test_report_includes_required_sections(self) -> None:
        root = Path(setup_interaction_repo())
        cases = [
            QualityCase(
                id="a", query="grpc", surface="brief",
                expect_buckets=("interfaces",),
                expect_labels=("grpc",),
                category="interaction",
            ),
            QualityCase(
                id="b", query="Store", surface="brief",
                expect_buckets=("primary",),
                expect_labels=("Store",),
                category="navigation",
            ),
        ]
        results = run_quality(cases, root)
        report = render_quality_report(results)
        self.assertIn("# Cortex first-context quality benchmark", report)
        self.assertIn("| id ", report)
        self.assertIn("## Summary", report)
        self.assertIn("By category", report)
        self.assertIn("By surface", report)

    def test_report_shows_failures(self) -> None:
        r = QualityResult(
            case=QualityCase(
                id="fail1", query="nonexistent", surface="brief",
                expect_buckets=("interfaces",),
                expect_labels=("missing_label",),
                category="test",
            ),
            bucket_scores=[
                BucketScore(
                    bucket="interfaces", present=False, count=0,
                ),
            ],
            label_hits={"missing_label": False},
            total_tokens=0,
        )
        report = render_quality_report([r])
        self.assertIn("Failures", report)
        self.assertIn("Missing buckets", report)
        self.assertIn("Missing labels", report)

if __name__ == "__main__":
    unittest.main()
