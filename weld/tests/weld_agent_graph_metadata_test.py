"""Tests for Agent Graph metadata and reference extraction."""

from __future__ import annotations

import json
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.agent_graph_discovery import discover_agent_graph  # noqa: E402


def _write(root: Path, rel_path: str, text: str = "content\n") -> None:
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _edge_types_from(graph: dict, source_id: str) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for edge in graph["edges"]:
        if edge["from"] == source_id:
            result.setdefault(edge["type"], set()).add(edge["to"])
            provenance = edge["props"].get("provenance", {})
            assert provenance.get("file")
            assert provenance.get("raw")
            assert isinstance(provenance.get("line"), int)
    return result


class AgentGraphMetadataTest(unittest.TestCase):
    def test_markdown_frontmatter_references_and_broken_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "docs/architecture/principles.md")
            _write(
                root,
                ".claude/skills/architecture-decision/SKILL.md",
                textwrap.dedent(
                    """\
                    ---
                    name: architecture-decision
                    description: Captures architecture decisions.
                    ---

                    # Architecture Decision
                    """
                ),
            )
            _write(
                root,
                ".github/agents/planner.agent.md",
                textwrap.dedent(
                    """\
                    ---
                    name: planner
                    description: Produces implementation plans.
                    tools: ['search', 'runCommands']
                    model: gpt-5
                    handoffs:
                      - implementer
                    applyTo: ['src/**']
                    skills: ['architecture-decision']
                    ---

                    Use @docs/architecture/principles.md.
                    Also check [missing](docs/missing.md), command:test, agent:reviewer, and mcp:filesystem.
                    """
                ),
            )

            graph = discover_agent_graph(
                root,
                git_sha="abc123",
                updated_at="2026-04-24T00:00:00+00:00",
            )

        planner = graph["nodes"]["agent:github-copilot:planner"]
        self.assertEqual(planner["props"]["description"], "Produces implementation plans.")
        self.assertEqual(planner["props"]["model"], "gpt-5")
        self.assertEqual(planner["props"]["tools"], ["runCommands", "search"])
        self.assertEqual(planner["props"]["handoffs"], ["implementer"])
        outgoing = _edge_types_from(graph, "agent:github-copilot:planner")
        self.assertIn("file:generic:docs-architecture-principles.md", outgoing["references_file"])
        self.assertIn("agent:github-copilot:implementer", outgoing["handoff_to"])
        self.assertIn("agent:github-copilot:reviewer", outgoing["invokes_agent"])
        self.assertIn("scope:generic:src", outgoing["applies_to_path"])
        self.assertIn("skill:claude:architecture-decision", outgoing["uses_skill"])
        self.assertIn("tool:generic:search", outgoing["provides_tool"])
        broken = [
            item for item in graph["meta"]["diagnostics"]
            if item["code"] == "agent_graph_broken_reference"
        ]
        self.assertEqual(len(broken), 1)
        self.assertEqual(broken[0]["reference"], "docs/missing.md")
        self.assertEqual(broken[0]["source_node"], "agent:github-copilot:planner")

    def test_skill_markdown_fallback_name_and_description(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(
                root,
                "tools/review/SKILL.md",
                textwrap.dedent(
                    """\
                    # PR Review

                    Reviews pull requests for risk and missing tests.

                    - Ignore list content for the short description.
                    """
                ),
            )

            graph = discover_agent_graph(
                root,
                git_sha="abc123",
                updated_at="2026-04-24T00:00:00+00:00",
            )

        node = graph["nodes"]["skill:generic:review"]
        self.assertEqual(node["label"], "PR Review")
        self.assertEqual(
            node["props"]["description"],
            "Reviews pull requests for risk and missing tests.",
        )

    def test_opencode_json_agents_commands_and_mcp_servers(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "docs/plan.md")
            _write(
                root,
                "opencode.json",
                json.dumps({
                    "agents": {
                        "planner": {
                            "description": "Plans work.",
                            "model": "gpt-5",
                            "tools": ["read"],
                            "prompt": "Use skill:architecture-decision and @docs/plan.md",
                        }
                    },
                    "commands": {
                        "plan": {
                            "description": "Run planning.",
                            "prompt": "Invoke agent:planner",
                        }
                    },
                    "mcpServers": {"filesystem": {"command": "mcp-filesystem"}},
                }),
            )

            graph = discover_agent_graph(
                root,
                git_sha="abc123",
                updated_at="2026-04-24T00:00:00+00:00",
            )

        self.assertIn("agent:opencode:planner", graph["nodes"])
        self.assertIn("command:opencode:plan", graph["nodes"])
        self.assertIn("mcp-server:opencode:filesystem", graph["nodes"])
        self.assertEqual(graph["nodes"]["agent:opencode:planner"]["props"]["model"], "gpt-5")
        command_edges = _edge_types_from(graph, "command:opencode:plan")
        self.assertIn("agent:opencode:planner", command_edges["invokes_agent"])
        agent_edges = _edge_types_from(graph, "agent:opencode:planner")
        self.assertIn("file:generic:docs-plan.md", agent_edges["references_file"])
        self.assertIn("skill:opencode:architecture-decision", agent_edges["uses_skill"])

    def test_claude_settings_hooks_permissions_and_mcp_servers(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "docs/hook.md")
            _write(
                root,
                ".claude/settings.json",
                json.dumps({
                    "hooks": {
                        "PreToolUse": [
                            {
                                "matcher": "Bash",
                                "hooks": [{"type": "command", "command": "echo docs/hook.md"}],
                            }
                        ]
                    },
                    "permissions": {
                        "allow": ["Read", "Bash(git status)"],
                        "deny": ["Write"],
                    },
                    "mcpServers": {"github": {"command": "mcp-github"}},
                }),
            )

            graph = discover_agent_graph(
                root,
                git_sha="abc123",
                updated_at="2026-04-24T00:00:00+00:00",
            )

        self.assertIn("hook:claude:pretooluse-1", graph["nodes"])
        self.assertIn("mcp-server:claude:github", graph["nodes"])
        settings_edges = _edge_types_from(graph, "config:claude:settings")
        self.assertIn("hook:claude:pretooluse-1", settings_edges["triggers_on_event"])
        self.assertIn("tool:generic:read", settings_edges["provides_tool"])
        self.assertIn("tool:generic:bash", settings_edges["provides_tool"])
        self.assertIn("tool:generic:write", settings_edges["restricts_tool"])
        hook_edges = _edge_types_from(graph, "hook:claude:pretooluse-1")
        self.assertIn("file:generic:docs-hook.md", hook_edges["references_file"])


if __name__ == "__main__":
    unittest.main()
