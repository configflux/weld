"""CLI tests for ``wd agents`` discovery commands."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import textwrap
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


def _run(argv: list[str], root: Path) -> tuple[int, str]:
    rc, stdout, _stderr = _run_with_stderr(argv, root)
    return rc, stdout


def _run_with_stderr(argv: list[str], root: Path) -> tuple[int, str, str]:
    out = io.StringIO()
    err = io.StringIO()
    with _cwd(root), redirect_stdout(out), redirect_stderr(err):
        rc = wd_main(argv)
    return rc, out.getvalue(), err.getvalue()


class AgentGraphCliTest(unittest.TestCase):
    def test_agents_discover_writes_graph_and_prints_summary(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(
                root,
                ".github/agents/planner.agent.md",
                textwrap.dedent(
                    """\
                    ---
                    name: planner
                    description: Plans changes.
                    ---
                    """
                ),
            )

            rc, stdout = _run(["agents", "discover"], root)

            graph_path = root / ".weld" / "agent-graph.json"
            self.assertEqual(rc, 0)
            self.assertTrue(graph_path.is_file())
            self.assertIn("Agent Graph discovery", stdout)
            self.assertIn("Assets: 1", stdout)
            self.assertIn("Write: .weld/agent-graph.json", stdout)
            graph = json.loads(graph_path.read_text(encoding="utf-8"))
            self.assertIn("agent:github-copilot:planner", graph["nodes"])

    def test_agents_discover_json_can_skip_write(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "AGENTS.md", "Use @docs/guide.md.\n")
            _write(root, "docs/guide.md")

            rc, stdout = _run(["agents", "discover", "--json", "--no-write"], root)

            self.assertEqual(rc, 0)
            graph = json.loads(stdout)
            self.assertIn("instruction:generic:agents", graph["nodes"])
            self.assertFalse((root / ".weld" / "agent-graph.json").exists())

    def test_agents_json_still_writes_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "CLAUDE.md")

            rc, stdout = _run(["agents", "discover", "--json"], root)

            self.assertEqual(rc, 0)
            self.assertTrue((root / ".weld" / "agent-graph.json").is_file())
            graph = json.loads(stdout)
            self.assertIn("instruction:claude:claude", graph["nodes"])

    def test_agents_rediscover_refreshes_persisted_graph(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "AGENTS.md")

            rc, stdout = _run(["agents", "rediscover"], root)

            self.assertEqual(rc, 0)
            self.assertIn("Agent Graph discovery", stdout)
            graph = json.loads(
                (root / ".weld" / "agent-graph.json").read_text(encoding="utf-8")
            )
            self.assertIn("instruction:generic:agents", graph["nodes"])

    def test_agents_list_groups_discovered_assets_by_platform(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(
                root,
                ".github/agents/planner.agent.md",
                textwrap.dedent(
                    """\
                    ---
                    name: planner
                    description: Plans changes before implementation.
                    ---
                    """
                ),
            )
            _write(
                root,
                ".claude/skills/pr-review/SKILL.md",
                textwrap.dedent(
                    """\
                    ---
                    name: pr-review
                    description: Reviews pull requests.
                    ---
                    """
                ),
            )

            self.assertEqual(_run(["agents", "discover"], root)[0], 0)
            rc, stdout = _run(["agents", "list"], root)

            self.assertEqual(rc, 0)
            self.assertIn("Claude Code", stdout)
            self.assertIn(
                "skill        pr-review", stdout,
            )
            self.assertIn(".claude/skills/pr-review/SKILL.md", stdout)
            self.assertIn("GitHub Copilot / VS Code", stdout)
            self.assertIn("agent        planner", stdout)
            self.assertIn(".github/agents/planner.agent.md", stdout)
            self.assertIn("Plans changes before implementation.", stdout)

    def test_agents_list_json_supports_type_and_platform_filters(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, ".github/agents/planner.agent.md")
            _write(root, ".claude/skills/pr-review/SKILL.md")
            _write(
                root,
                "opencode.json",
                json.dumps({
                    "commands": {"plan": {"description": "Create a plan."}},
                    "mcpServers": {"filesystem": {"description": "File access."}},
                }),
            )

            self.assertEqual(_run(["agents", "discover"], root)[0], 0)
            rc, stdout = _run(["agents", "list", "--json", "--type", "skill"], root)

            self.assertEqual(rc, 0)
            payload = json.loads(stdout)
            self.assertEqual(payload["count"], 1)
            self.assertEqual(payload["filters"], {"platform": None, "type": "skill"})
            self.assertEqual(payload["assets"][0]["type"], "skill")
            self.assertEqual(payload["assets"][0]["platform"], "claude")

            rc, stdout = _run(
                ["agents", "list", "--json", "--platform", "opencode"],
                root,
            )

            self.assertEqual(rc, 0)
            payload = json.loads(stdout)
            self.assertEqual(
                [asset["type"] for asset in payload["assets"]],
                ["command", "config", "mcp-server"],
            )
            self.assertEqual(
                [asset["name"] for asset in payload["assets"]],
                ["plan", "opencode", "filesystem"],
            )

    def test_agents_list_requires_persisted_graph(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            rc, stdout, stderr = _run_with_stderr(["agents", "list"], root)

            self.assertEqual(rc, 2)
            self.assertEqual(stdout, "")
            self.assertIn("Run `wd agents discover`", stderr)

    def test_agents_explain_name_shows_relationships_and_variants(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "docs/plan.md")
            _write(
                root,
                ".github/agents/planner.agent.md",
                textwrap.dedent(
                    """\
                    ---
                    name: planner
                    description: Plans changes.
                    skills: [architecture-decision]
                    handoffs: [reviewer]
                    ---

                    See @docs/plan.md.
                    """
                ),
            )
            _write(
                root,
                ".github/agents/reviewer.agent.md",
                textwrap.dedent(
                    """\
                    ---
                    name: reviewer
                    description: Reviews changes.
                    ---
                    """
                ),
            )
            _write(
                root,
                ".claude/agents/planner.md",
                textwrap.dedent(
                    """\
                    ---
                    name: planner
                    description: Plans changes.
                    ---
                    """
                ),
            )
            _write(
                root,
                ".claude/skills/architecture-decision/SKILL.md",
                textwrap.dedent(
                    """\
                    ---
                    name: architecture-decision
                    description: Records architecture decisions.
                    ---
                    """
                ),
            )

            self.assertEqual(_run(["agents", "discover"], root)[0], 0)
            rc, stdout = _run(["agents", "explain", "planner"], root)

            self.assertEqual(rc, 0)
            self.assertIn("planner", stdout)
            self.assertIn("Type: agent", stdout)
            self.assertIn("Purpose:", stdout)
            self.assertIn("Plans changes.", stdout)
            self.assertIn("GitHub Copilot / VS Code", stdout)
            self.assertIn(".github/agents/planner.agent.md", stdout)
            self.assertIn("Claude Code", stdout)
            self.assertIn(".claude/agents/planner.md", stdout)
            self.assertIn("uses_skill -> skill:architecture-decision", stdout)
            self.assertIn("handoff_to -> agent:reviewer", stdout)
            self.assertIn("references_file -> file:docs/plan.md", stdout)
            self.assertIn("Potential overlap:", stdout)
            self.assertIn("same name", stdout)

    def test_agents_explain_json_resolves_supported_asset_types(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "AGENTS.md", "Repository instructions.\n")
            _write(
                root,
                ".github/agents/planner.agent.md",
                "---\nname: planner\ndescription: Plans changes.\n---\n",
            )
            _write(
                root,
                ".github/prompts/create-plan.prompt.md",
                "---\nname: create-plan\ndescription: Creates a plan.\n---\n",
            )
            _write(
                root,
                ".claude/skills/pr-review/SKILL.md",
                "---\nname: pr-review\ndescription: Reviews pull requests.\n---\n",
            )
            _write(
                root,
                "opencode.json",
                json.dumps({
                    "commands": {"plan": {"description": "Create a plan."}},
                    "hooks": {
                        "PostToolUse": [
                            {"description": "Record tool use.", "matcher": "*"},
                        ],
                    },
                }),
            )

            self.assertEqual(_run(["agents", "discover"], root)[0], 0)
            cases = {
                ".github/agents/planner.agent.md": "agent",
                ".github/prompts/create-plan.prompt.md": "prompt",
                ".claude/skills/pr-review/SKILL.md": "skill",
                "AGENTS.md": "instruction",
                "opencode.json#/commands/plan": "command",
                "opencode.json#/hooks/PostToolUse/0": "hook",
            }

            for query, expected_type in cases.items():
                with self.subTest(query=query):
                    rc, stdout = _run(["agents", "explain", query, "--json"], root)

                    self.assertEqual(rc, 0)
                    payload = json.loads(stdout)
                    self.assertEqual(payload["asset"]["type"], expected_type)
                    self.assertIn("incoming_references", payload)
                    self.assertIn("outgoing_references", payload)
                    self.assertIn("platform_variants", payload)

            rc, stdout = _run(
                ["agents", "explain", "agent:github-copilot:planner", "--json"],
                root,
            )

            self.assertEqual(rc, 0)
            payload = json.loads(stdout)
            self.assertEqual(payload["asset"]["name"], "planner")

    def test_agents_explain_reports_missing_asset(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "AGENTS.md")

            self.assertEqual(_run(["agents", "discover"], root)[0], 0)
            rc, stdout, stderr = _run_with_stderr(
                ["agents", "explain", "missing"],
                root,
            )

            self.assertEqual(rc, 2)
            self.assertEqual(stdout, "")
            self.assertIn("Agent Graph asset not found: missing", stderr)


if __name__ == "__main__":
    unittest.main()
