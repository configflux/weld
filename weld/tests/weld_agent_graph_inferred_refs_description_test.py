"""Tests for inferred-confidence reference extraction in frontmatter prose.

Slice-3 (a1) k58t: ``description``/``desc``/``purpose`` scalars in YAML
frontmatter (and JSON config) must be scanned with the same body-text
regexes (``subagent_type``, ``Skill()``, bare ``/command``). This file is
sibling to ``weld_agent_graph_inferred_refs_test.py`` and split out to
respect the 400-line cap.
"""

from __future__ import annotations

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.agent_graph_discovery import discover_agent_graph  # noqa: E402
from weld.agent_graph_metadata import parse_agent_asset  # noqa: E402


def _write(root: Path, rel_path: str, text: str = "content\n") -> None:
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class FrontmatterDescriptionInferredRefsTest(unittest.TestCase):
    """Slice-3 (a1) k58t: prose-bearing frontmatter values must be scanned.

    Real-world example: ``release-manager.md`` puts a ``/release-audit``
    reference inside its frontmatter ``description:`` field. Slice-1 q8rl
    only scans body text, so the resulting ``uses_command`` edge was missing.
    """

    def test_description_bare_command_emits_inferred_uses_command(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(
                root,
                ".claude/agents/release-manager.md",
                textwrap.dedent(
                    """\
                    ---
                    name: release-manager
                    description: Pre-release audit. Invoked via /release-audit. Read-only.
                    ---

                    Body content with no command refs.
                    """
                ),
            )
            asset = parse_agent_asset(
                root,
                ".claude/agents/release-manager.md",
                "agent",
                "claude",
                known_commands=frozenset({"release-audit"}),
            )
        cmd_refs = [
            r for r in asset.references
            if r.edge_type == "uses_command" and r.target_name == "release-audit"
        ]
        self.assertEqual(len(cmd_refs), 1, asset.references)
        self.assertEqual(cmd_refs[0].confidence, "inferred")
        self.assertEqual(cmd_refs[0].target_type, "command")
        # Provenance: raw match preserved.
        self.assertIn("release-audit", cmd_refs[0].raw)

    def test_description_subagent_type_emits_inferred_invokes_agent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(
                root,
                ".claude/agents/sample.md",
                textwrap.dedent(
                    """\
                    ---
                    name: sample
                    description: Dispatches work via subagent_type="reviewer" pattern.
                    ---

                    Body has no inferred refs.
                    """
                ),
            )
            asset = parse_agent_asset(
                root,
                ".claude/agents/sample.md",
                "agent",
                "claude",
            )
        agent_refs = [
            r for r in asset.references
            if r.edge_type == "invokes_agent" and r.target_name == "reviewer"
        ]
        self.assertEqual(len(agent_refs), 1, asset.references)
        self.assertEqual(agent_refs[0].confidence, "inferred")

    def test_description_path_lookalike_filtered(self) -> None:
        """``/tmp/foo`` in description must NOT mint a command edge."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(
                root,
                ".claude/agents/sample.md",
                textwrap.dedent(
                    """\
                    ---
                    name: sample
                    description: Reads /tmp/foo and /unknown to compute.
                    ---

                    Body.
                    """
                ),
            )
            asset = parse_agent_asset(
                root,
                ".claude/agents/sample.md",
                "agent",
                "claude",
                known_commands=frozenset({"push"}),  # neither tmp nor unknown
            )
        for r in asset.references:
            if r.edge_type == "uses_command":
                self.fail(f"unexpected command edge: {r}")

    def test_description_purpose_alias_also_scanned(self) -> None:
        """Other prose-bearing keys (`purpose`, `desc`) get the same treatment."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(
                root,
                ".claude/agents/sample.md",
                textwrap.dedent(
                    """\
                    ---
                    name: sample
                    purpose: Run /push to ship.
                    ---

                    Body.
                    """
                ),
            )
            asset = parse_agent_asset(
                root,
                ".claude/agents/sample.md",
                "agent",
                "claude",
                known_commands=frozenset({"push"}),
            )
        cmd_refs = [
            r for r in asset.references
            if r.edge_type == "uses_command" and r.target_name == "push"
        ]
        self.assertEqual(len(cmd_refs), 1, asset.references)
        self.assertEqual(cmd_refs[0].confidence, "inferred")


class FrontmatterDescriptionTwoPassIntegrationTest(unittest.TestCase):
    """End-to-end: discover_agent_graph wires known_commands to frontmatter."""

    def test_frontmatter_description_command_resolves_through_two_pass(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # Seed the command (defined as a Claude command file).
            _write(
                root,
                ".claude/commands/release-audit.md",
                "---\nname: release-audit\n---\nRun the audit.\n",
            )
            # Agent that references the command in frontmatter description.
            _write(
                root,
                ".claude/agents/release-manager.md",
                textwrap.dedent(
                    """\
                    ---
                    name: release-manager
                    description: Pre-release audit. Invoked via /release-audit.
                    ---

                    Body has nothing relevant.
                    """
                ),
            )
            graph = discover_agent_graph(
                root,
                git_sha="abc123",
                updated_at="2026-04-30T00:00:00+00:00",
            )
        agent_id = "agent:claude:release-manager"
        cmd_id = "command:claude:release-audit"
        edges = [
            (edge["to"], edge["props"].get("confidence"))
            for edge in graph["edges"]
            if edge["from"] == agent_id and edge["type"] == "uses_command"
        ]
        self.assertIn((cmd_id, "inferred"), edges, edges)


if __name__ == "__main__":
    unittest.main()
