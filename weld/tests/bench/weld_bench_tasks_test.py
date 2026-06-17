"""Unit tests for the weld comparative agent-task benchmark core.

These tests cover the pure building blocks: loading fixtures, accuracy
math, the latency helper, the run_compare orchestrator, and report
rendering. The CLI surface is exercised in a companion test file
(``weld_bench_tasks_cli_test.py``).
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent.parent)

# Allow the test helpers next to this file to be imported.
_bench_dir = str(Path(__file__).resolve().parent)
if _bench_dir not in sys.path:
    sys.path.insert(0, _bench_dir)

from weld.bench_tasks import (  # noqa: E402
    AgentTask,
    CompareMetrics,
    CompareResult,
    load_tasks,
    render_compare_report,
    run_compare,
)
from weld.bench_tasks.compare import (  # noqa: E402
    accuracy_metrics,
    latency_ms,
)
from bench_test_helpers import (  # noqa: E402
    FIXTURE_TASKS_YAML,
    setup_compare_repo,
)


# --- Fixture loading ---------------------------------------------------------


class LoadTasksTest(unittest.TestCase):
    def test_load_tasks_from_yaml(self) -> None:
        path = Path(tempfile.mktemp(suffix=".yaml"))
        path.write_text(FIXTURE_TASKS_YAML, encoding="utf-8")
        try:
            tasks = load_tasks(path)
        finally:
            path.unlink()
        self.assertEqual(len(tasks), 3)
        self.assertEqual(tasks[0].id, "t01")
        self.assertEqual(tasks[0].category, "navigation")
        self.assertEqual(tasks[0].answer_files, ("src/store.py",))
        self.assertEqual(tasks[2].symbol, "Store")

    def test_real_fixture_loads(self) -> None:
        path = (
            Path(_repo_root)
            / "weld"
            / "bench_tasks"
            / "fixtures"
            / "default.yaml"
        )
        tasks = load_tasks(path)
        # First-increment corpus expansion (bd ec80.5) lifted the floor
        # from the original 4 tasks to 12; a follow-up issue tracks the
        # 20-30 target. Tighten this bound when the next increment lands.
        self.assertGreaterEqual(len(tasks), 12)
        # Every task must have at least one expected answer file so the
        # accuracy metric is well-defined.
        for t in tasks:
            self.assertGreater(
                len(t.answer_files),
                0,
                f"task {t.id!r} has no answer_files",
            )

    def test_real_fixture_category_diversity(self) -> None:
        """The corpus must span more than one category.

        With n=4 all-navigation tasks the bench median was statistically
        flimsy. The first-increment expansion adds comprehension and
        refactor tasks. We assert >= 3 distinct categories and that each
        category has at least 3 representatives so the per-category
        median in the report is meaningful (not a single-task headline).
        """
        path = (
            Path(_repo_root)
            / "weld"
            / "bench_tasks"
            / "fixtures"
            / "default.yaml"
        )
        tasks = load_tasks(path)
        cats: dict[str, int] = {}
        for t in tasks:
            cats[t.category] = cats.get(t.category, 0) + 1
        self.assertGreaterEqual(
            len(cats),
            3,
            f"corpus must span >= 3 categories; got {cats}",
        )
        for cat, count in cats.items():
            self.assertGreaterEqual(
                count,
                3,
                f"category {cat!r} has only {count} tasks; "
                "need >= 3 for a stable per-category median",
            )

    def test_real_fixture_answer_files_exist(self) -> None:
        """Every answer_file path must point at a real repo file.

        The bench scores retrieval against the answer key. If the answer
        key drifts from the repo (file renamed or removed), grep can hit
        nothing and weld can hit nothing, and both retrieval modes look
        equally bad for a reason that has nothing to do with retrieval
        quality. This guards the corpus against silent rot.
        """
        path = (
            Path(_repo_root)
            / "weld"
            / "bench_tasks"
            / "fixtures"
            / "default.yaml"
        )
        tasks = load_tasks(path)
        repo = Path(_repo_root)
        missing: list[tuple[str, str]] = []
        for t in tasks:
            for af in t.answer_files:
                if not (repo / af).exists():
                    missing.append((t.id, af))
        self.assertEqual(
            missing,
            [],
            "fixture answer_files must point at real files; missing: "
            f"{missing}",
        )

    def test_real_fixture_callgraph_tasks_have_symbol(self) -> None:
        """`callgraph` tasks route to the references surface.

        :func:`weld.bench_tasks.compare.weld_files_and_text` only takes
        the references path when both ``category == 'callgraph'`` AND
        ``symbol`` is set; otherwise it falls through to ``query``. A
        callgraph task missing a symbol is silently downgraded -- the
        bench number for that task no longer measures what the
        category label claims to measure. Catch the misconfiguration at
        load time instead of in a flat report a week later.
        """
        path = (
            Path(_repo_root)
            / "weld"
            / "bench_tasks"
            / "fixtures"
            / "default.yaml"
        )
        tasks = load_tasks(path)
        offenders = [
            t.id for t in tasks if t.category == "callgraph" and not t.symbol
        ]
        self.assertEqual(
            offenders,
            [],
            f"callgraph tasks missing `symbol`: {offenders}",
        )

    def test_load_tasks_filters_non_dicts(self) -> None:
        path = Path(tempfile.mktemp(suffix=".yaml"))
        path.write_text(
            "tasks:\n  - not-a-dict\n  - id: ok\n    prompt: p\n"
            "    category: navigation\n    term: Store\n"
            "    answer_files: [src/store.py]\n",
            encoding="utf-8",
        )
        try:
            tasks = load_tasks(path)
        finally:
            path.unlink()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].id, "ok")


# --- Accuracy primitives -----------------------------------------------------


class AccuracyMetricsTest(unittest.TestCase):
    def test_perfect_match(self) -> None:
        m = accuracy_metrics(
            found=["a.py", "b.py"], expected=["a.py", "b.py"],
        )
        self.assertEqual(m.precision, 1.0)
        self.assertEqual(m.recall, 1.0)
        self.assertEqual(m.f1, 1.0)

    def test_partial_overlap(self) -> None:
        m = accuracy_metrics(
            found=["a.py", "c.py"], expected=["a.py", "b.py"],
        )
        # 1 tp / 2 found = 0.5 precision, 1 tp / 2 expected = 0.5 recall
        self.assertAlmostEqual(m.precision, 0.5)
        self.assertAlmostEqual(m.recall, 0.5)
        self.assertAlmostEqual(m.f1, 0.5)

    def test_no_overlap(self) -> None:
        m = accuracy_metrics(found=["x.py"], expected=["a.py"])
        self.assertEqual(m.precision, 0.0)
        self.assertEqual(m.recall, 0.0)
        self.assertEqual(m.f1, 0.0)

    def test_empty_found(self) -> None:
        m = accuracy_metrics(found=[], expected=["a.py"])
        # precision undefined with no predictions; the helper reports 0.0
        # so F1 is well-defined and the metric can be averaged.
        self.assertEqual(m.precision, 0.0)
        self.assertEqual(m.recall, 0.0)
        self.assertEqual(m.f1, 0.0)

    def test_empty_expected_is_skipped(self) -> None:
        # A task with no answer files should never reach accuracy_metrics,
        # but if it does, return all-zeros rather than dividing by zero.
        m = accuracy_metrics(found=["a.py"], expected=[])
        self.assertEqual(m.recall, 0.0)

    def test_duplicate_found_are_deduplicated(self) -> None:
        m = accuracy_metrics(found=["a.py", "a.py"], expected=["a.py"])
        # Duplicates must not inflate precision.
        self.assertEqual(m.precision, 1.0)
        self.assertEqual(m.recall, 1.0)


# --- Latency helper ----------------------------------------------------------


class LatencyMsTest(unittest.TestCase):
    def test_latency_ms_returns_value_and_result(self) -> None:
        def _slow():
            return "done"

        ms, value = latency_ms(_slow)
        self.assertGreaterEqual(ms, 0.0)
        self.assertEqual(value, "done")

    def test_latency_ms_propagates_exceptions(self) -> None:
        def _boom():
            raise RuntimeError("nope")

        with self.assertRaises(RuntimeError):
            latency_ms(_boom)


# --- run_compare: end-to-end comparative pass --------------------------------


class RunCompareTest(unittest.TestCase):
    def test_run_compare_returns_one_result_per_task(self) -> None:
        root = Path(setup_compare_repo("Store"))
        tasks = [
            AgentTask(
                id="t1",
                prompt="x",
                category="navigation",
                term="Store",
                answer_files=("src/store.py",),
            ),
            AgentTask(
                id="t2",
                prompt="x",
                category="dependency",
                term="Store",
                answer_files=("src/store.py", "src/use_store.py"),
            ),
        ]
        results = run_compare(tasks, root)
        self.assertEqual(len(results), 2)
        for r in results:
            self.assertIsInstance(r, CompareResult)
            # Each result carries grep and weld measurements.
            self.assertGreaterEqual(r.grep_tokens, 0)
            self.assertGreaterEqual(r.weld_tokens, 0)
            self.assertGreaterEqual(r.grep_latency_ms, 0.0)
            self.assertGreaterEqual(r.weld_latency_ms, 0.0)
            self.assertIsInstance(r.grep_accuracy, CompareMetrics)
            self.assertIsInstance(r.weld_accuracy, CompareMetrics)

    def test_weld_accuracy_matches_answer_key_on_navigation(self) -> None:
        root = Path(setup_compare_repo("Store"))
        tasks = [
            AgentTask(
                id="nav",
                prompt="x",
                category="navigation",
                term="Store",
                answer_files=("src/store.py",),
            ),
        ]
        results = run_compare(tasks, root)
        # wd brief should include the `file` property of the Store
        # entity node and thus recall the answer file.
        self.assertGreater(
            results[0].weld_accuracy.recall,
            0.0,
            "weld retrieval must find at least the answer file",
        )


# --- Report rendering --------------------------------------------------------


class RenderCompareReportTest(unittest.TestCase):
    def test_report_includes_summary_and_table(self) -> None:
        root = Path(setup_compare_repo("Store"))
        tasks = [
            AgentTask(
                id="t1",
                prompt="x",
                category="navigation",
                term="Store",
                answer_files=("src/store.py",),
            ),
            AgentTask(
                id="t2",
                prompt="x",
                category="dependency",
                term="Store",
                answer_files=("src/use_store.py",),
            ),
        ]
        results = run_compare(tasks, root)
        report = render_compare_report(results)
        self.assertIn("# Weld comparative agent benchmark", report)
        self.assertIn("| id ", report)
        self.assertIn("## Summary", report)
        self.assertIn("Accuracy", report)
        self.assertIn("Latency", report)
        self.assertIn("Token cost", report)
        # Per-category breakdown when categories vary.
        self.assertIn("By category", report)

    def test_report_handles_empty_results(self) -> None:
        report = render_compare_report([])
        self.assertIn("# Weld comparative agent benchmark", report)
        self.assertIn("no tasks", report.lower())


if __name__ == "__main__":
    unittest.main()
