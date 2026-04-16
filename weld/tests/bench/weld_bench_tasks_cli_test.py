"""CLI tests for the comparative agent-task bench.

Exercises ``wd bench --compare`` and ``wd bench --report`` end to
end against a synthetic repo. The pure runner and scoring behavior is
covered by ``weld_bench_tasks_test.py``.
"""

from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

# Ensure weld package is importable from the repo root.
_repo_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

# Allow the test helpers next to this file to be imported.
_bench_dir = str(Path(__file__).resolve().parent)
if _bench_dir not in sys.path:
    sys.path.insert(0, _bench_dir)

from weld.cli import main as cli_main  # noqa: E402
from bench_test_helpers import (  # noqa: E402
    FIXTURE_TASKS_YAML,
    setup_compare_repo,
)


def _write_tasks(root: Path) -> Path:
    path = root / "tasks.yaml"
    path.write_text(FIXTURE_TASKS_YAML, encoding="utf-8")
    return path


class WeldBenchCompareCliTest(unittest.TestCase):
    def test_compare_writes_report_and_artifact(self) -> None:
        root = Path(setup_compare_repo("Store"))
        tasks_path = _write_tasks(root)
        out_path = root / "report.md"
        rc = cli_main(
            [
                "bench",
                "--compare",
                "--root",
                str(root),
                "--tasks",
                str(tasks_path),
                "--out",
                str(out_path),
            ]
        )
        self.assertEqual(rc, 0)
        self.assertTrue(out_path.exists())
        text = out_path.read_text(encoding="utf-8")
        self.assertIn("# Weld comparative agent benchmark", text)
        self.assertIn("| t01 | navigation", text)
        # A JSON artifact is written alongside so `--report` can regenerate
        # the report without re-running retrieval.
        artifact = out_path.with_suffix(".json")
        self.assertTrue(artifact.exists())
        data = json.loads(artifact.read_text(encoding="utf-8"))
        self.assertIn("results", data)
        self.assertEqual(len(data["results"]), 3)

    def test_compare_task_filter(self) -> None:
        root = Path(setup_compare_repo("Store"))
        tasks_path = _write_tasks(root)
        out_path = root / "report.md"
        rc = cli_main(
            [
                "bench",
                "--compare",
                "--task",
                "t01",
                "--root",
                str(root),
                "--tasks",
                str(tasks_path),
                "--out",
                str(out_path),
            ]
        )
        self.assertEqual(rc, 0)
        text = out_path.read_text(encoding="utf-8")
        self.assertIn("| t01 | navigation", text)
        self.assertNotIn("| t02 ", text)

    def test_compare_unknown_task_errors(self) -> None:
        root = Path(setup_compare_repo("Store"))
        tasks_path = _write_tasks(root)
        out_path = root / "report.md"
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            rc = cli_main(
                [
                    "bench",
                    "--compare",
                    "--task",
                    "does_not_exist",
                    "--root",
                    str(root),
                    "--tasks",
                    str(tasks_path),
                    "--out",
                    str(out_path),
                ]
            )
        self.assertEqual(rc, 1)
        self.assertIn("does_not_exist", buf.getvalue())

    def test_compare_print_only_does_not_write(self) -> None:
        root = Path(setup_compare_repo("Store"))
        tasks_path = _write_tasks(root)
        out_path = root / "report.md"
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            rc = cli_main(
                [
                    "bench",
                    "--compare",
                    "--root",
                    str(root),
                    "--tasks",
                    str(tasks_path),
                    "--out",
                    str(out_path),
                    "--print",
                ]
            )
        self.assertEqual(rc, 0)
        self.assertIn(
            "Weld comparative agent benchmark", buf.getvalue()
        )
        self.assertFalse(out_path.exists())


class WeldBenchReportCliTest(unittest.TestCase):
    def test_report_regenerates_from_artifact(self) -> None:
        root = Path(setup_compare_repo("Store"))
        tasks_path = _write_tasks(root)
        compare_out = root / "compare.md"
        # First run compare to produce an artifact.
        rc = cli_main(
            [
                "bench",
                "--compare",
                "--root",
                str(root),
                "--tasks",
                str(tasks_path),
                "--out",
                str(compare_out),
            ]
        )
        self.assertEqual(rc, 0)
        artifact = compare_out.with_suffix(".json")
        self.assertTrue(artifact.exists())

        # Now regenerate the report from the artifact alone, pointing at a
        # new path. Should not touch the graph or re-run retrieval.
        report_out = root / "regen.md"
        rc = cli_main(
            [
                "bench",
                "--report",
                "--artifact",
                str(artifact),
                "--out",
                str(report_out),
            ]
        )
        self.assertEqual(rc, 0)
        self.assertTrue(report_out.exists())
        text = report_out.read_text(encoding="utf-8")
        self.assertIn("# Weld comparative agent benchmark", text)
        self.assertIn("| t01 | navigation", text)

    def test_report_missing_artifact_errors(self) -> None:
        root = Path(setup_compare_repo("Store"))
        missing = root / "no_such_artifact.json"
        out = root / "x.md"
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            rc = cli_main(
                [
                    "bench",
                    "--report",
                    "--artifact",
                    str(missing),
                    "--out",
                    str(out),
                ]
            )
        self.assertEqual(rc, 1)
        self.assertIn("not found", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
