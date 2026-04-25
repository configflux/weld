"""CLI tests for ``wd agents plan-change``."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Iterator

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.cli import main as wd_main  # noqa: E402


@contextmanager
def _cwd(path: Path) -> Iterator[None]:
    old = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


def _write(root: Path, rel_path: str, text: str = "content\n") -> None:
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _run(argv: list[str], root: Path) -> tuple[int, str, str]:
    out = io.StringIO()
    err = io.StringIO()
    with _cwd(root), redirect_stdout(out), redirect_stderr(err):
        rc = wd_main(argv)
    return rc, out.getvalue(), err.getvalue()


class AgentGraphPlanCliTest(unittest.TestCase):
    def test_agents_plan_change_json_is_static_and_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _workspace(root)

            self.assertEqual(_run(["agents", "discover"], root)[0], 0)
            rc, stdout, stderr = _run(
                [
                    "agents",
                    "plan-change",
                    "planner should always include test strategy",
                    "--json",
                ],
                root,
            )
            rc_again, stdout_again, stderr_again = _run(
                [
                    "agents",
                    "plan-change",
                    "planner should always include test strategy",
                    "--json",
                ],
                root,
            )

            self.assertEqual((rc, stderr), (0, ""))
            self.assertEqual((rc_again, stderr_again), (0, ""))
            self.assertEqual(stdout, stdout_again)
            payload = json.loads(stdout)
            self.assertEqual(
                payload["primary_files"],
                [".claude/agents/planner.md", ".github/agents/planner.agent.md"],
            )
            self.assertTrue(
                {
                    ".claude/skills/architecture-decision/SKILL.md",
                    ".github/agents/reviewer.agent.md",
                } <= set(payload["secondary_files"])
            )
            self.assertIn(".weld/agent-graph.json", payload["validation_files"])
            self.assertIn("wd agents audit", payload["validation_steps"])
            self.assertTrue(
                any(step.startswith("wd agents explain ") for step in payload["validation_steps"])
            )
            self.assertTrue(
                any(step.startswith("wd agents impact ") for step in payload["validation_steps"])
            )
            self.assertIn(
                "Platform variants may drift for planner.",
                payload["warnings"],
            )

    def test_agents_plan_change_human_and_no_match_paths(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _workspace(root)

            self.assertEqual(_run(["agents", "discover"], root)[0], 0)
            rc, stdout, stderr = _run(
                ["agents", "plan-change", "planner should include tests"],
                root,
            )

            self.assertEqual((rc, stderr), (0, ""))
            self.assertIn("Change plan", stdout)
            self.assertIn("Primary files:", stdout)
            self.assertIn(".github/agents/planner.agent.md", stdout)
            self.assertIn("Validation:", stdout)

            rc, stdout, stderr = _run(
                ["agents", "plan-change", "reticulate spline widgets", "--json"],
                root,
            )

            self.assertEqual((rc, stderr), (0, ""))
            payload = json.loads(stdout)
            self.assertEqual(payload["primary_assets"], [])
            self.assertEqual(payload["secondary_files"], [])
            self.assertEqual(
                payload["warnings"],
                ["No matching assets found; authoritative source is unknown."],
            )


def _workspace(root: Path) -> None:
    _write(
        root,
        ".github/agents/planner.agent.md",
        "---\nname: planner\ndescription: Produces implementation plans.\n"
        "skills: [architecture-decision]\nhandoffs: [reviewer]\n---\n",
    )
    _write(
        root,
        ".github/agents/reviewer.agent.md",
        "---\nname: reviewer\ndescription: Reviews plans.\n---\n",
    )
    _write(
        root,
        ".github/prompts/create-plan.prompt.md",
        "---\nname: create-plan\ndescription: Drafts test strategy prompts.\n---\n",
    )
    _write(root, ".claude/agents/planner.md", "---\nname: planner\n---\n")
    _write(
        root,
        ".claude/skills/architecture-decision/SKILL.md",
        "---\nname: architecture-decision\ndescription: Records decisions.\n---\n",
    )


if __name__ == "__main__":
    unittest.main()
