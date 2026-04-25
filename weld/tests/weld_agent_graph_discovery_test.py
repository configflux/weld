"""Tests for static Agent Graph discovery."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.agent_graph_discovery import (  # noqa: E402
    discover_agent_assets,
    discover_agent_graph,
)


def _write(root: Path, rel_path: str, text: str = "content\n") -> None:
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=check,
        capture_output=True,
        text=True,
    )


class AgentGraphDiscoveryTest(unittest.TestCase):
    def test_discovers_known_customization_assets(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for rel_path in [
                ".github/copilot-instructions.md",
                ".github/instructions/testing.instructions.md",
                ".github/prompts/create-plan.prompt.md",
                ".github/agents/planner.agent.md",
                ".github/skills/review/SKILL.md",
                "AGENTS.md",
                "AGENTS.override.md",
                "CLAUDE.md",
                ".claude/agents/reviewer.md",
                ".claude/skills/pr-review/SKILL.md",
                ".claude/settings.json",
                "opencode.json",
                ".cursor/rules/testing.mdc",
                "GEMINI.md",
                ".gemini/agents/planner.md",
                "tools/skills/generic/SKILL.md",
                ".mcp.json",
            ]:
                _write(root, rel_path)

            assets = discover_agent_assets(root)

        by_path = {asset.path: asset for asset in assets}
        expected = {
            ".github/copilot-instructions.md": ("instruction", "github-copilot"),
            ".github/instructions/testing.instructions.md": (
                "instruction",
                "github-copilot",
            ),
            ".github/prompts/create-plan.prompt.md": ("prompt", "github-copilot"),
            ".github/agents/planner.agent.md": ("agent", "github-copilot"),
            ".github/skills/review/SKILL.md": ("skill", "github-copilot"),
            "AGENTS.md": ("instruction", "generic"),
            "AGENTS.override.md": ("instruction", "codex"),
            "CLAUDE.md": ("instruction", "claude"),
            ".claude/agents/reviewer.md": ("agent", "claude"),
            ".claude/skills/pr-review/SKILL.md": ("skill", "claude"),
            ".claude/settings.json": ("config", "claude"),
            "opencode.json": ("config", "opencode"),
            ".cursor/rules/testing.mdc": ("instruction", "cursor"),
            "GEMINI.md": ("instruction", "gemini"),
            ".gemini/agents/planner.md": ("agent", "gemini"),
            "tools/skills/generic/SKILL.md": ("skill", "generic"),
            ".mcp.json": ("config", "generic"),
        }
        self.assertEqual(set(by_path), set(expected))
        for path, (node_type, platform) in expected.items():
            self.assertEqual(by_path[path].node_type, node_type)
            self.assertEqual(by_path[path].platform, platform)

    def test_discovery_graph_is_deterministic_with_fixed_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, ".github/agents/planner.agent.md", "planner\n")
            _write(root, ".claude/skills/review/SKILL.md", "review\n")

            first = discover_agent_graph(
                root,
                git_sha="abc123",
                updated_at="2026-04-24T00:00:00+00:00",
            )
            second = discover_agent_graph(
                root,
                git_sha="abc123",
                updated_at="2026-04-24T00:00:00+00:00",
            )

        self.assertEqual(first, second)
        self.assertEqual(
            first["meta"]["discovered_from"],
            [
                ".claude/skills/review/SKILL.md",
                ".github/agents/planner.agent.md",
            ],
        )
        self.assertEqual(first["meta"]["diagnostics"], [])
        self.assertEqual(
            sorted(node["props"]["file"] for node in first["nodes"].values()),
            first["meta"]["discovered_from"],
        )

    def test_scanner_respects_repo_boundary_exclusions(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _git(root, "init", "-q")
            _write(root, "AGENTS.md")
            _write(root, ".claude/worktrees/copy/AGENTS.md")
            _write(root, ".claude/worktrees/copy/SKILL.md")
            _write(root, ".cache/copied/AGENTS.md")
            _write(root, ".cache/copied/SKILL.md")
            _write(root, ".weld/AGENTS.md")
            _write(root, ".weld/SKILL.md")
            _write(root, "bazel-out/AGENTS.md")
            _write(root, "bazel-out/SKILL.md")
            _write(root, "bazel-bin/.github/agents/shadow.agent.md")
            _write(root, "bazel-agent/AGENTS.md")
            _write(root, "bazel-agent/SKILL.md")
            _write(root, ".gitignore", "*.ignored.md\n")
            _write(root, "ignored.ignored.md")
            _git(root, "add", "-f", ".")

            graph = discover_agent_graph(
                root,
                git_sha="abc123",
                updated_at="2026-04-24T00:00:00+00:00",
            )

        paths = graph["meta"]["discovered_from"]
        self.assertEqual(paths, ["AGENTS.md"])
        node_files = [node["props"]["file"] for node in graph["nodes"].values()]
        self.assertEqual(node_files, ["AGENTS.md"])

    def test_static_discovery_does_not_execute_customization_content(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            marker = root / "marker"
            _write(
                root,
                "opencode.json",
                textwrap.dedent(
                    f"""\
                    {{
                      "commands": {{
                        "danger": "touch {marker}"
                      }}
                    }}
                    """
                ),
            )

            assets = discover_agent_assets(root)

        self.assertFalse(marker.exists())
        self.assertEqual([asset.path for asset in assets], ["opencode.json"])


if __name__ == "__main__":
    unittest.main()
