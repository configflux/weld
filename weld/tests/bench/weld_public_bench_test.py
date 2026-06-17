"""Unit tests for ``wd bench --public`` machinery (ADR 0059).

Covers corpus loading, accuracy metrics, report rendering, per-family
aggregation, and the smoke-corpus end-to-end run. Adapter-specific
tests live in ``weld_public_bench_adapters_test.py``; CLI tests live
in ``weld_public_bench_cli_test.py``.

These tests use the smoke corpus shipped in
``weld/bench/fixtures/public_corpus_smoke/`` so the full Tier-2 corpus
is never invoked in CI.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


from weld.bench._public_runner import (  # noqa: E402
    AdapterResult,
    PublicRowResult,
    PublicRunReport,
    PublicTask,
    accuracy_metrics,
    load_public_corpus,
    materialize_smoke_corpus,
    run_public,
)
from weld.bench._public_report import (  # noqa: E402
    aggregate_by_family,
    render_public_report,
)

_SMOKE_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "bench"
    / "fixtures"
    / "public_corpus_smoke"
)
_SMOKE_MANIFEST = _SMOKE_DIR / "smoke_corpus.yaml"


class CorpusLoaderTest(unittest.TestCase):
    """Schema validation for the public-corpus YAML manifest."""

    def test_loads_smoke_corpus(self) -> None:
        corpus = load_public_corpus(_SMOKE_MANIFEST)
        self.assertEqual(corpus.corpus_id, "smoke")
        self.assertEqual(corpus.schema_version, 1)
        self.assertGreaterEqual(len(corpus.repos), 2)
        # Tasks are bound to their repo and carry the expected fields.
        first_repo = corpus.repos[0]
        self.assertEqual(first_repo.id, "repo_a")
        self.assertGreaterEqual(len(first_repo.tasks), 1)
        t = first_repo.tasks[0]
        self.assertEqual(t.repo_id, "repo_a")
        self.assertEqual(t.family, "navigation")
        self.assertEqual(t.term, "Store")

    def test_loads_production_corpus(self) -> None:
        # The production SHA-pinned manifest must parse with the same loader.
        prod_path = (
            Path(__file__).resolve().parent.parent.parent
            / "bench"
            / "public_corpus.yaml"
        )
        corpus = load_public_corpus(prod_path)
        self.assertEqual(corpus.corpus_id, "public-v0")
        # ADR 0059 mandates exactly five repos in the production corpus.
        self.assertEqual(len(corpus.repos), 5)
        # Each repo must declare a SHA-pinned git source (production mode).
        for repo in corpus.repos:
            self.assertEqual(repo.source.kind, "git")
            self.assertEqual(
                len(repo.source.sha), 40,
                f"repo {repo.id!r} has non-40-char sha "
                f"{repo.source.sha!r}",
            )

    def test_rejects_missing_repos(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.yaml"
            path.write_text("schema_version: 1\ncorpus_id: x\n", "utf-8")
            with self.assertRaises(ValueError):
                load_public_corpus(path)

    def test_rejects_unknown_family(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.yaml"
            path.write_text(
                (
                    "schema_version: 1\n"
                    "corpus_id: x\n"
                    "repos:\n"
                    "  - id: r1\n"
                    "    language: python\n"
                    "    source:\n"
                    "      kind: local\n"
                    "      path: r1\n"
                    "    tasks:\n"
                    "      - id: t1\n"
                    "        family: bogus\n"
                    "        prompt: x\n"
                    "        term: x\n"
                    "        answer_files:\n"
                    "          - a.py\n"
                ),
                "utf-8",
            )
            with self.assertRaises(ValueError):
                load_public_corpus(path)

    def test_rejects_bad_sha(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.yaml"
            path.write_text(
                (
                    "schema_version: 1\n"
                    "corpus_id: x\n"
                    "repos:\n"
                    "  - id: r1\n"
                    "    language: python\n"
                    "    source:\n"
                    "      kind: git\n"
                    "      url: https://example/r\n"
                    "      sha: \"shortbeef\"\n"
                    "    tasks: []\n"
                ),
                "utf-8",
            )
            with self.assertRaises(ValueError):
                load_public_corpus(path)


class AccuracyMetricsTest(unittest.TestCase):
    def test_perfect_match(self) -> None:
        m = accuracy_metrics(["a", "b"], ["a", "b"])
        self.assertEqual(m.precision, 1.0)
        self.assertEqual(m.recall, 1.0)
        self.assertEqual(m.f1, 1.0)

    def test_partial_match(self) -> None:
        m = accuracy_metrics(["a", "b", "c"], ["a", "b"])
        # precision = 2/3, recall = 2/2 = 1
        self.assertAlmostEqual(m.precision, 2 / 3, places=4)
        self.assertEqual(m.recall, 1.0)
        self.assertAlmostEqual(m.f1, 0.8, places=4)

    def test_no_match(self) -> None:
        m = accuracy_metrics(["c"], ["a"])
        self.assertEqual(m.precision, 0.0)
        self.assertEqual(m.recall, 0.0)
        self.assertEqual(m.f1, 0.0)

    def test_dedup(self) -> None:
        m = accuracy_metrics(["a", "a", "a"], ["a"])
        # Should NOT inflate precision with repeated hits.
        self.assertEqual(m.precision, 1.0)
        self.assertEqual(m.recall, 1.0)


def _adapter_row(
    family: str, weld_files: list[str], grep_files: list[str],
) -> PublicRowResult:
    """Tiny helper: build a row for report tests without adapter calls."""
    task = PublicTask(
        repo_id="r",
        id=f"{family}-1",
        family=family,
        prompt="x",
        term="X",
        symbol="X",
        answer_files=("a.py",),
    )
    return PublicRowResult(
        task=task,
        adapter_results={
            "weld": AdapterResult(
                status="ok", files=weld_files, tokens=10,
                duration_ms=2.0, cost_usd=0.0, ttft_ms=1.0,
            ),
            "grep": AdapterResult(
                status="ok", files=grep_files, tokens=100,
                duration_ms=5.0, cost_usd=0.0, ttft_ms=2.0,
            ),
            "tree_sitter": AdapterResult(
                status="unavailable", files=[], tokens=0,
                duration_ms=0.0, cost_usd=0.0, ttft_ms=0.0,
            ),
            "graphify": AdapterResult(
                status="unavailable", files=[], tokens=0,
                duration_ms=0.0, cost_usd=0.0, ttft_ms=0.0,
            ),
        },
    )


class ReportRenderTest(unittest.TestCase):
    def test_renders_methodology_and_caveats(self) -> None:
        row = _adapter_row("navigation", ["a.py"], ["a.py", "b.py"])
        # Override the row task id for the expected table cell.
        row.task = PublicTask(
            repo_id="r", id="t1", family="navigation",
            prompt="x", term="X", symbol="X",
            answer_files=("a.py",),
        )
        report = PublicRunReport(
            corpus_id="smoke",
            schema_version=1,
            weld_version="0.17.2",
            rows=[row],
        )
        md = render_public_report(report)
        # Methodology + corpus manifest sections.
        self.assertIn("## Methodology", md)
        self.assertIn("## Corpus manifest", md)
        self.assertIn("smoke", md)
        # Per-task table row.
        self.assertIn("| t1 | navigation |", md)
        # Per-family aggregate section.
        self.assertIn("## Per-family aggregates", md)
        # Caveats section is always present.
        self.assertIn("## Caveats", md)
        # graphify / tree_sitter shown as unavailable in the row.
        self.assertIn("unavailable", md)

    def test_caveats_lists_losses(self) -> None:
        # weld returns nothing while grep nails the answer.
        row = _adapter_row("callgraph", [], ["a.py"])
        rpt = PublicRunReport(
            corpus_id="smoke",
            schema_version=1,
            weld_version="0.17.2",
            rows=[row],
        )
        md = render_public_report(rpt)
        # Caveats should call out the loss explicitly per ADR 0059
        # ("honest losing"). We require the family name to appear in
        # the caveats section.
        idx = md.find("## Caveats")
        self.assertGreater(idx, 0)
        caveats_section = md[idx:]
        self.assertIn("callgraph", caveats_section)

    def test_caveats_lists_degraded_weld(self) -> None:
        # A degraded weld run must appear under the Caveats section so a
        # reader sees the failure plainly. (ADR 0059 'honest losing'.)
        task = PublicTask(
            repo_id="r", id="t99", family="navigation",
            prompt="x", term="X", symbol="X",
            answer_files=("a.py",),
        )
        row = PublicRowResult(
            task=task,
            adapter_results={
                "weld": AdapterResult(
                    status="degraded", files=[], tokens=0,
                    duration_ms=2.0, error="graph missing",
                ),
                "grep": AdapterResult(
                    status="ok", files=["a.py"], tokens=10,
                    duration_ms=1.0,
                ),
            },
        )
        rpt = PublicRunReport(
            corpus_id="smoke",
            schema_version=1,
            weld_version="0.17.2",
            rows=[row],
        )
        md = render_public_report(rpt)
        idx = md.find("## Caveats")
        self.assertGreater(idx, 0)
        caveats_section = md[idx:]
        self.assertIn("Degraded", caveats_section)
        self.assertIn("graph missing", caveats_section)

    def test_caveats_clean_run(self) -> None:
        # All-ok run with weld matching the answer key: caveats section
        # must still exist but say there are no weld losses.
        row = _adapter_row("navigation", ["a.py"], ["a.py"])
        rpt = PublicRunReport(
            corpus_id="smoke",
            schema_version=1,
            weld_version="0.17.2",
            rows=[row],
        )
        md = render_public_report(rpt)
        self.assertIn("## Caveats", md)
        # When nothing degraded and nothing lost, the report should say
        # so explicitly rather than emit an empty section.
        idx = md.find("## Caveats")
        self.assertIn("No weld losses", md[idx:])


class FamilyAggregateTest(unittest.TestCase):
    def test_aggregate_by_family_groups_correctly(self) -> None:
        rows = [
            _adapter_row("navigation", ["a.py"], ["a.py"]),
            _adapter_row("navigation", ["a.py"], ["a.py"]),
            _adapter_row("callgraph", [], []),
        ]
        agg = aggregate_by_family(rows, "weld")
        self.assertIn("navigation", agg)
        self.assertEqual(agg["navigation"].n, 2)
        self.assertGreater(agg["navigation"].f1, 0.99)
        self.assertEqual(agg["callgraph"].n, 1)

    def test_aggregate_skips_unavailable_adapters(self) -> None:
        # If the adapter is unavailable across every row, its family
        # rollup is empty.
        rows = [
            _adapter_row("navigation", ["a.py"], ["a.py"]),
            _adapter_row("callgraph", ["a.py"], ["a.py"]),
        ]
        agg = aggregate_by_family(rows, "tree_sitter")
        self.assertEqual(agg, {})


class SmokeEndToEndTest(unittest.TestCase):
    def test_run_public_on_smoke_corpus(self) -> None:
        # End-to-end smoke run: load smoke corpus, run all adapters,
        # render report. Adapters that need an external binary
        # (tree_sitter, graphify) return "unavailable" on this machine.
        with tempfile.TemporaryDirectory() as workdir:
            workdir_p = Path(workdir)
            materialize_smoke_corpus(_SMOKE_MANIFEST, workdir_p)
            corpus = load_public_corpus(_SMOKE_MANIFEST)
            report = run_public(
                corpus,
                workdir_p,
                adapters=("weld", "grep", "tree_sitter", "graphify"),
            )
            self.assertEqual(report.corpus_id, "smoke")
            # At least one row per task.
            total_tasks = sum(len(r.tasks) for r in corpus.repos)
            self.assertEqual(len(report.rows), total_tasks)
            # Each row has all four adapters wired.
            for row in report.rows:
                self.assertEqual(
                    set(row.adapter_results.keys()),
                    {"weld", "grep", "tree_sitter", "graphify"},
                )

    def test_verify_byte_identical(self) -> None:
        # Run the smoke corpus twice; report bytes must match exactly.
        # Latency / wall-clock fields are normalized in the report so
        # the bytes do not drift run-to-run.
        with tempfile.TemporaryDirectory() as workdir:
            workdir_p = Path(workdir)
            materialize_smoke_corpus(_SMOKE_MANIFEST, workdir_p)
            corpus = load_public_corpus(_SMOKE_MANIFEST)
            r1 = run_public(
                corpus, workdir_p,
                adapters=("weld", "grep", "tree_sitter", "graphify"),
            )
            r2 = run_public(
                corpus, workdir_p,
                adapters=("weld", "grep", "tree_sitter", "graphify"),
            )
            md1 = render_public_report(r1)
            md2 = render_public_report(r2)
            self.assertEqual(
                md1, md2,
                "Public bench report not byte-identical between runs "
                "(non-determinism leaked into the rendered output).",
            )


if __name__ == "__main__":
    unittest.main()
