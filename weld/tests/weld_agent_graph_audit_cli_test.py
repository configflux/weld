"""CLI tests for ``wd agents audit``."""

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


class AgentGraphAuditCliTest(unittest.TestCase):
    def test_agents_audit_json_reports_static_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _conflict_workspace(root)

            self.assertEqual(_run(["agents", "discover"], root)[0], 0)
            rc, stdout, stderr = _run(["agents", "audit", "--json"], root)

            self.assertEqual((rc, stderr), (0, ""))
            payload = json.loads(stdout)
            codes = {finding["code"] for finding in payload["findings"]}
            self.assertTrue({
                "broken_reference",
                "duplicate_name",
                "missing_agent",
                "missing_mcp_config",
                "path_scope_overlap",
                "permission_conflict",
                "platform_drift",
                "responsibility_overlap",
                "unsafe_hook",
                "unused_skill",
                "vague_description",
            } <= codes)
            self.assertEqual(payload["summary"]["finding_count"], len(payload["findings"]))

    def test_agents_audit_human_output_is_readable(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _conflict_workspace(root)

            self.assertEqual(_run(["agents", "discover"], root)[0], 0)
            rc, stdout, stderr = _run(["agents", "audit"], root)

            self.assertEqual((rc, stderr), (0, ""))
            self.assertIn("Agent Graph audit findings:", stdout)
            self.assertIn("Broken reference", stdout)
            self.assertIn("Tool permission conflict", stdout)


def _conflict_workspace(root: Path) -> None:
    _write(root, "AGENTS.md", "Use @docs/missing.md and mcp:github.\n")
    _write(
        root,
        ".github/agents/planner.agent.md",
        "---\nname: planner\ndescription: Plans changes.\n"
        "tools: [editFiles]\n---\n",
    )
    _write(
        root,
        ".claude/agents/planner.md",
        "---\nname: planner\ndescription: Drafts implementation plans.\n"
        "denied_tools: [editFiles]\n---\n",
    )
    _write(root, ".github/agents/reviewer.agent.md", "---\nname: reviewer\n"
           "description: Reviews dependencies.\n---\n")
    _write(root, ".github/instructions/cpp.instructions.md",
           "---\napplyTo: [src/**]\n---\n")
    _write(root, ".github/instructions/testing.instructions.md",
           "---\napplyTo: [src/**]\n---\n")
    _write(root, ".claude/skills/security-review/SKILL.md",
           "---\nname: security-review\ndescription: Reviews dependencies.\n---\n")
    _write(root, ".claude/skills/vague/SKILL.md",
           "---\nname: vague\ndescription: stuff\n---\n")
    _write(
        root,
        "opencode.json",
        json.dumps({
            "commands": {"deploy": {"description": "Run agent:missing-agent."}},
            "hooks": {"PostToolUse": [{}]},
        }),
    )


if __name__ == "__main__":
    unittest.main()
