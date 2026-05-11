"""Tests for skipped-row semantics in ``wd bench --public``.

A skipped repo is one whose corpus SHA is a placeholder (heuristic or
explicit) or whose clone failed. Per the honest-losing posture documented
in ``docs/bench/README.md``, such a repo's tasks still appear in the
per-task table but render ``SKIPPED: <reason>`` in every adapter cell
and are excluded from the per-family aggregates. This file covers:

  - The renderer emits SKIPPED cells with a stable reason string.
  - ``aggregate_by_family`` excludes status="skipped" rows.
  - ``run_public`` synthesizes skipped AdapterResults for tasks in
    placeholder-SHA repos without invoking the clone path.

Split out of ``weld_public_bench_setup_test.py`` so each file stays
within the 400-line cap. Materialization-phase tests for the same
"skipped" decision live there.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_repo_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)


class SkippedRowsTest(unittest.TestCase):
    """Renderer + aggregator + runner all honor the skipped status."""

    def test_skipped_row_renders_in_per_task_cell(self) -> None:
        from weld.bench._public_report import render_public_report
        from weld.bench._public_runner import (
            AdapterResult,
            PublicRowResult,
            PublicRunReport,
            PublicTask,
        )

        task = PublicTask(
            repo_id="fake_eshop",
            id="eshop-nav-01",
            family="navigation",
            prompt="x",
            term="X",
            symbol="X",
            answer_files=("a.cs",),
        )
        skipped = AdapterResult(
            status="skipped",
            files=[],
            tokens=0,
            duration_ms=0.0,
            error="placeholder SHA (corpus entry not yet pinned)",
        )
        row = PublicRowResult(
            task=task,
            adapter_results={
                "weld": skipped,
                "grep": skipped,
                "tree_sitter": skipped,
                "graphify": skipped,
            },
        )
        report = PublicRunReport(
            corpus_id="x",
            schema_version=1,
            weld_version="0.17.2",
            rows=[row],
        )
        md = render_public_report(report)
        self.assertIn("eshop-nav-01", md)
        # SKIPPED: <reason> cell -- not "F1=0.00 ... status=skipped".
        self.assertIn("SKIPPED", md)
        self.assertIn("placeholder", md)

    def test_skipped_excluded_from_family_aggregate(self) -> None:
        from weld.bench._public_report import aggregate_by_family
        from weld.bench._public_runner import (
            AdapterResult,
            PublicRowResult,
            PublicTask,
        )

        task_ok = PublicTask(
            repo_id="r", id="t1", family="navigation",
            prompt="x", term="X", symbol="X",
            answer_files=("a.py",),
        )
        task_skip = PublicTask(
            repo_id="r2", id="t2", family="navigation",
            prompt="x", term="X", symbol="X",
            answer_files=("b.py",),
        )
        rows = [
            PublicRowResult(
                task=task_ok,
                adapter_results={
                    "weld": AdapterResult(
                        status="ok", files=["a.py"], tokens=10,
                        duration_ms=2.0,
                    ),
                },
            ),
            PublicRowResult(
                task=task_skip,
                adapter_results={
                    "weld": AdapterResult(
                        status="skipped", files=[], tokens=0,
                        duration_ms=0.0,
                        error="placeholder SHA",
                    ),
                },
            ),
        ]
        agg = aggregate_by_family(rows, "weld")
        self.assertIn("navigation", agg)
        # n=1 -- the skipped row is excluded.
        self.assertEqual(agg["navigation"].n, 1)
        self.assertEqual(agg["navigation"].f1, 1.0)

    def test_run_public_emits_skipped_rows_for_placeholder_repo(self) -> None:
        # End-to-end: a placeholder-SHA corpus repo produces skipped
        # rows for every adapter and never invokes the clone path.
        from weld.bench._public_corpus import (
            CorpusSource,
            PublicCorpus,
            PublicRepo,
        )
        from weld.bench._public_runner import PublicTask, run_public

        placeholder_repo = PublicRepo(
            id="fake",
            language="python",
            source=CorpusSource(
                kind="git",
                url="https://example.com/fake",
                sha="f" * 40,
            ),
            tasks=(
                PublicTask(
                    repo_id="fake",
                    id="fake-nav-01",
                    family="navigation",
                    prompt="x",
                    term="X",
                    symbol="X",
                    answer_files=("a.py",),
                ),
            ),
        )
        corpus = PublicCorpus(
            schema_version=1,
            corpus_id="mixed",
            description="",
            repos=(placeholder_repo,),
        )
        with tempfile.TemporaryDirectory() as workdir:
            workdir_p = Path(workdir)
            with patch(
                "weld.bench._public_setup.clone_repo_at_sha"
            ) as mock_clone:
                report = run_public(
                    corpus,
                    workdir_p,
                    adapters=("weld", "grep"),
                )
            mock_clone.assert_not_called()
            self.assertEqual(len(report.rows), 1)
            row = report.rows[0]
            for adapter in ("weld", "grep"):
                self.assertEqual(
                    row.adapter_results[adapter].status, "skipped"
                )
                self.assertIn(
                    "placeholder",
                    row.adapter_results[adapter].error.lower(),
                )


if __name__ == "__main__":
    unittest.main()
