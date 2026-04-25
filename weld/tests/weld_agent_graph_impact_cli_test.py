"""CLI tests for ``wd agents impact``."""

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


class AgentGraphImpactCliTest(unittest.TestCase):
    def test_agents_impact_json_works_from_file_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _workspace(root)

            self.assertEqual(_run(["agents", "discover"], root)[0], 0)
            rc, stdout, stderr = _run(
                ["agents", "impact", ".github/agents/planner.agent.md", "--json"],
                root,
            )

            self.assertEqual((rc, stderr), (0, ""))
            payload = json.loads(stdout)
            self.assertEqual(payload["asset"]["name"], "planner")
            self.assertEqual(payload["authority_status"], "manual")
            self.assertEqual(
                {entry["name"] for entry in payload["same_name_variants"]},
                {"planner"},
            )
            self.assertIn(
                "Update or intentionally leave same-name platform variants.",
                payload["change_checklist"],
            )
            self.assertTrue(
                {"architecture-decision", "reviewer"} <= {
                    item["node"]["name"] for item in payload["downstream"]
                }
            )

    def test_agents_impact_human_works_from_node_id(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _workspace(root)

            self.assertEqual(_run(["agents", "discover"], root)[0], 0)
            rc, stdout, stderr = _run(
                ["agents", "impact", "agent:github-copilot:planner"],
                root,
            )

            self.assertEqual((rc, stderr), (0, ""))
            self.assertIn("Changing .github/agents/planner.agent.md affects:", stdout)
            self.assertIn("skill:architecture-decision", stdout)
            self.assertIn("agent:reviewer", stdout)
            self.assertIn("Same-name variants:", stdout)
            self.assertIn("Authority status: manual", stdout)
            self.assertIn("Run wd agents audit", stdout)


def _workspace(root: Path) -> None:
    _write(
        root,
        ".github/agents/planner.agent.md",
        "---\nname: planner\ndescription: Plans changes.\n"
        "skills: [architecture-decision]\nhandoffs: [reviewer]\n---\n",
    )
    _write(root, ".github/agents/reviewer.agent.md", "---\nname: reviewer\n---\n")
    _write(root, ".claude/agents/planner.md", "---\nname: planner\n---\n")
    _write(
        root,
        ".claude/skills/architecture-decision/SKILL.md",
        "---\nname: architecture-decision\n---\n",
    )


if __name__ == "__main__":
    unittest.main()
