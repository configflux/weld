"""Tests for per-entry permission edge explosion (slice 3 (a4) tracked issue).

The audit observed that ~25 distinct ``Bash(...)`` allowlist entries in
``.claude/settings.json`` collapsed to a single ``provides_tool`` edge
(target = ``tool:generic:bash``) with raw = the last entry seen, and that
zero ``restricts_tool`` edges were ever emitted from the deny list. The
fix in this slice emits one edge per allow/deny entry, preserves the full
pattern in ``raw``, and points provenance at the specific list entry's
line.

These tests are written black-box against the public discover_agent_graph
output: they assert edge multiplicity, provenance line numbers, and per-
entry raw values, not implementation internals.
"""

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


def _settings_edges(graph: dict, source_id: str, edge_type: str) -> list[dict]:
    return [
        edge for edge in graph["edges"]
        if edge["from"] == source_id and edge["type"] == edge_type
    ]


class PermissionExplodeTest(unittest.TestCase):
    def test_three_distinct_allow_entries_emit_three_provides_tool_edges(self) -> None:
        # Three Bash(*) variants would collapse to one edge under the
        # pre-fix code; verify they now produce three distinct edges with
        # per-entry raw values.
        settings = textwrap.dedent(
            """\
            {
              "permissions": {
                "allow": [
                  "Bash(git status)",
                  "Bash(git log*)",
                  "Bash(bazel build *)"
                ]
              }
            }
            """
        )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, ".claude/settings.json", settings)
            graph = discover_agent_graph(
                root, git_sha="t", updated_at="2026-04-24T00:00:00+00:00",
            )
        provides = _settings_edges(graph, "config:claude:settings", "provides_tool")
        raws = sorted(edge["props"]["provenance"]["raw"] for edge in provides)
        self.assertEqual(raws, [
            "Bash(bazel build *)", "Bash(git log*)", "Bash(git status)",
        ])
        # Each edge must point at the same aggregated tool node so audit
        # checks (permission_conflict) keep working.
        self.assertTrue(all(edge["to"] == "tool:generic:bash" for edge in provides))
        # Per-entry provenance: each edge has a distinct line number that
        # matches its position in the source file.
        lines = [edge["props"]["provenance"]["line"] for edge in provides]
        self.assertEqual(len(set(lines)), 3, lines)

    def test_two_distinct_deny_entries_emit_two_restricts_tool_edges(self) -> None:
        settings = textwrap.dedent(
            """\
            {
              "permissions": {
                "deny": [
                  "Bash(rm *)",
                  "Bash(curl *)"
                ]
              }
            }
            """
        )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, ".claude/settings.json", settings)
            graph = discover_agent_graph(
                root, git_sha="t", updated_at="2026-04-24T00:00:00+00:00",
            )
        restricts = _settings_edges(
            graph, "config:claude:settings", "restricts_tool",
        )
        raws = sorted(edge["props"]["provenance"]["raw"] for edge in restricts)
        self.assertEqual(raws, ["Bash(curl *)", "Bash(rm *)"])
        self.assertTrue(all(edge["to"] == "tool:generic:bash" for edge in restricts))
        lines = sorted(
            edge["props"]["provenance"]["line"] for edge in restricts
        )
        self.assertEqual(len(set(lines)), 2, lines)

    def test_empty_allow_and_deny_emit_no_edges(self) -> None:
        settings = json.dumps({"permissions": {"allow": [], "deny": []}})
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, ".claude/settings.json", settings)
            graph = discover_agent_graph(
                root, git_sha="t", updated_at="2026-04-24T00:00:00+00:00",
            )
        provides = _settings_edges(graph, "config:claude:settings", "provides_tool")
        restricts = _settings_edges(
            graph, "config:claude:settings", "restricts_tool",
        )
        self.assertEqual(provides, [])
        self.assertEqual(restricts, [])

    def test_missing_permissions_key_emits_no_edges(self) -> None:
        settings = json.dumps({"hooks": {}})
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, ".claude/settings.json", settings)
            graph = discover_agent_graph(
                root, git_sha="t", updated_at="2026-04-24T00:00:00+00:00",
            )
        provides = _settings_edges(graph, "config:claude:settings", "provides_tool")
        restricts = _settings_edges(
            graph, "config:claude:settings", "restricts_tool",
        )
        self.assertEqual(provides, [])
        self.assertEqual(restricts, [])

    def test_distinct_tool_prefixes_each_aggregate_correctly(self) -> None:
        # Bash(*) entries collapse to tool:generic:bash; Read/Grep are
        # already-aggregated tool names. Mixed input must still produce
        # one edge per entry, with the right tool target.
        settings = textwrap.dedent(
            """\
            {
              "permissions": {
                "allow": [
                  "Read",
                  "Grep",
                  "Bash(git status)",
                  "Bash(git log*)"
                ]
              }
            }
            """
        )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, ".claude/settings.json", settings)
            graph = discover_agent_graph(
                root, git_sha="t", updated_at="2026-04-24T00:00:00+00:00",
            )
        provides = _settings_edges(graph, "config:claude:settings", "provides_tool")
        raws = sorted(edge["props"]["provenance"]["raw"] for edge in provides)
        self.assertEqual(raws, [
            "Bash(git log*)",
            "Bash(git status)",
            "Grep",
            "Read",
        ])
        targets = {edge["to"] for edge in provides}
        self.assertEqual(targets, {
            "tool:generic:bash", "tool:generic:grep", "tool:generic:read",
        })

    def test_demo_settings_explodes_into_multiple_edges(self) -> None:
        # Cross-platform proof for the audit: the public demo's
        # settings.json has multiple allow + multiple deny entries.
        # After the fix it must produce >1 of each edge type.
        demo_root = Path(_repo_root) / "examples" / "agent-graph-demo"
        graph = discover_agent_graph(
            demo_root,
            git_sha="demo",
            updated_at="2026-04-24T00:00:00+00:00",
        )
        settings_id = "config:claude:settings"
        provides = _settings_edges(graph, settings_id, "provides_tool")
        restricts = _settings_edges(graph, settings_id, "restricts_tool")
        self.assertGreater(
            len(provides), 1,
            f"demo settings.json provides_tool edges = {len(provides)}",
        )
        self.assertGreater(
            len(restricts), 1,
            f"demo settings.json restricts_tool edges = {len(restricts)}",
        )

    def test_copilot_frontmatter_tools_list_still_emits_per_tool_edges(self) -> None:
        # Regression: the existing Copilot frontmatter `tools:` list
        # already emitted one provides_tool edge per item (because each
        # tool name is distinct and survives target-name dedupe). The
        # permission-explode change must not alter this behavior.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(
                root,
                ".github/agents/planner.agent.md",
                textwrap.dedent(
                    """\
                    ---
                    name: planner
                    description: Plans work.
                    tools:
                      - search
                      - runCommands
                      - editFiles
                    ---

                    Body.
                    """
                ),
            )
            graph = discover_agent_graph(
                root, git_sha="t", updated_at="2026-04-24T00:00:00+00:00",
            )
        provides = _settings_edges(
            graph, "agent:github-copilot:planner", "provides_tool",
        )
        targets = sorted(edge["to"] for edge in provides)
        self.assertEqual(targets, [
            "tool:generic:editfiles",
            "tool:generic:runcommands",
            "tool:generic:search",
        ])


if __name__ == "__main__":
    unittest.main()
