"""Tests for inferred-confidence reference extraction (slice 1).

Covers the three new body regexes added in weld/agent_graph_metadata_utils.py:
- subagent_type kwarg/colon form -> invokes_agent
- bare slash command (only when name is in known_commands) -> uses_command
- Skill(skill[_name]=...) call -> uses_skill
plus their integration through parse_agent_asset and discover_agent_graph.
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
from weld.agent_graph_metadata_utils import (  # noqa: E402
    extract_inferred_references,
)


def _write(root: Path, rel_path: str, text: str = "content\n") -> None:
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class ExtractInferredReferencesUnitTest(unittest.TestCase):
    """Unit tests for the helper that emits the three inferred-edge regexes."""

    def test_subagent_type_colon_form_emits_invokes_agent(self) -> None:
        text = '  subagent_type: "architect",\n'
        refs = extract_inferred_references(text, start_line=1, known_commands=None)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].target_type, "agent")
        self.assertEqual(refs[0].target_name, "architect")
        self.assertEqual(refs[0].edge_type, "invokes_agent")
        self.assertEqual(refs[0].confidence, "inferred")
        self.assertEqual(refs[0].line, 1)
        self.assertIn("architect", refs[0].raw)

    def test_subagent_type_kwarg_form_emits_invokes_agent(self) -> None:
        text = "Agent(subagent_type='reviewer', prompt='...')\n"
        refs = extract_inferred_references(text, start_line=10, known_commands=None)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].target_name, "reviewer")
        self.assertEqual(refs[0].edge_type, "invokes_agent")
        self.assertEqual(refs[0].confidence, "inferred")
        self.assertEqual(refs[0].line, 10)

    def test_subagent_type_template_placeholder_rejected(self) -> None:
        text = textwrap.dedent(
            """\
            subagent_type: "<implementer_type>"
            subagent_type: "${var}"
            subagent_type: "build-fixer"
            """
        )
        refs = extract_inferred_references(text, start_line=1, known_commands=None)
        names = [r.target_name for r in refs if r.edge_type == "invokes_agent"]
        # Only the literal "build-fixer" survives; placeholders are rejected
        # because their first character does not match [a-z].
        self.assertEqual(names, ["build-fixer"])

    def test_skill_call_emits_uses_skill(self) -> None:
        text = textwrap.dedent(
            """\
            Skill(skill_name="agent-system-maintainer", args="x")
            Skill(skill='simplify')
            """
        )
        refs = extract_inferred_references(text, start_line=5, known_commands=None)
        skills = sorted(r.target_name for r in refs if r.edge_type == "uses_skill")
        self.assertEqual(skills, ["agent-system-maintainer", "simplify"])
        for r in refs:
            self.assertEqual(r.confidence, "inferred")
            self.assertEqual(r.target_type, "skill")

    # Bare-slash-command (`_BARE_COMMAND_RE`) tests live in the sibling
    # weld_agent_graph_inferred_refs_bare_command_test.py file -- split out
    # in ukk8 to keep this file under the 400-line cap.

    def test_provenance_preserved_file_line_raw(self) -> None:
        text = textwrap.dedent(
            """\
            line one
            subagent_type = "tdd"
            line three
            """
        )
        refs = extract_inferred_references(text, start_line=100, known_commands=None)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].line, 101)  # start_line + offset (line 2 -> 101)
        self.assertEqual(refs[0].target_name, "tdd")
        self.assertIn("tdd", refs[0].raw)
        self.assertIn("subagent_type", refs[0].raw)


class ParseAgentAssetThreadingTest(unittest.TestCase):
    """Wiring: parse_agent_asset must accept and forward known_commands."""

    def test_parse_markdown_passes_known_commands_to_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(
                root,
                ".claude/agents/sample.md",
                textwrap.dedent(
                    """\
                    ---
                    name: sample
                    description: example.
                    ---

                    Body talks about /push and /unknown_command.
                    Also subagent_type: "reviewer".
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
        edges = {(r.edge_type, r.target_name) for r in asset.references}
        self.assertIn(("invokes_agent", "reviewer"), edges)
        self.assertIn(("uses_command", "push"), edges)
        # /unknown_command is filtered by the known_commands set.
        self.assertNotIn(("uses_command", "unknown_command"), edges)

    def test_parse_markdown_default_known_commands_none(self) -> None:
        # Backwards-compat: callers that omit known_commands still get
        # subagent_type / Skill() inferred edges; only /command needs a set.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(
                root,
                ".claude/agents/sample.md",
                textwrap.dedent(
                    """\
                    ---
                    name: sample
                    ---

                    subagent_type: "qa"
                    Skill(skill_name="simplify")
                    /push
                    """
                ),
            )
            asset = parse_agent_asset(root, ".claude/agents/sample.md", "agent", "claude")
        edges = {(r.edge_type, r.target_name) for r in asset.references}
        self.assertIn(("invokes_agent", "qa"), edges)
        self.assertIn(("uses_skill", "simplify"), edges)
        self.assertNotIn(("uses_command", "push"), edges)

    def test_named_ref_re_matches_remain_definite(self) -> None:
        """Existing typed-prefix matches must NOT be downgraded to inferred."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(
                root,
                ".claude/agents/sample.md",
                textwrap.dedent(
                    """\
                    ---
                    name: sample
                    ---

                    See agent:reviewer and skill:simplify and command:push.
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
        for ref in asset.references:
            if ref.target_name in {"reviewer", "simplify"}:
                # These were emitted by _NAMED_REF_RE -> definite.
                self.assertEqual(ref.confidence, "definite", repr(ref))


class DiscoverAgentGraphIntegrationTest(unittest.TestCase):
    """End-to-end: two-pass discovery wires known_commands through automatically."""

    def test_worker_style_pseudocode_emits_inferred_invokes_agent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # Define agents that will be the targets.
            for name in ("architect", "reviewer", "qa", "build-fixer"):
                _write(
                    root,
                    f".claude/agents/{name}.md",
                    f"---\nname: {name}\n---\nbody\n",
                )
            # Worker uses subagent_type pseudocode in body.
            _write(
                root,
                ".claude/agents/worker.md",
                textwrap.dedent(
                    """\
                    ---
                    name: worker
                    description: orchestrator.
                    ---

                    ```
                    Agent(
                      subagent_type: "architect",
                      prompt: "..."
                    )
                    Agent(
                      subagent_type: "reviewer",
                    )
                    subagent_type: "qa"
                    subagent_type: "<implementer_type>"
                    subagent_type: "build-fixer"
                    ```
                    """
                ),
            )
            graph = discover_agent_graph(
                root,
                git_sha="abc123",
                updated_at="2026-04-30T00:00:00+00:00",
            )
        worker_id = "agent:claude:worker"
        invokes = []
        for edge in graph["edges"]:
            if edge["from"] != worker_id or edge["type"] != "invokes_agent":
                continue
            invokes.append((edge["to"], edge["props"].get("confidence")))
        names_to_conf = dict(invokes)
        for target in ("architect", "reviewer", "qa", "build-fixer"):
            edge_id = f"agent:claude:{target}"
            self.assertIn(edge_id, names_to_conf, f"missing invokes_agent->{target}")
            self.assertEqual(
                names_to_conf[edge_id],
                "inferred",
                f"{target} expected confidence=inferred, got {names_to_conf[edge_id]}",
            )
        # Template placeholder must NOT have created any edge.
        for edge_id in names_to_conf:
            self.assertNotIn("implementer_type", edge_id)

    def test_bare_slash_command_resolves_against_discovered_commands(self) -> None:
        # opencode.json mints command nodes today, so use that path to seed
        # the known_commands set. The contract under test is that two-pass
        # discovery wires the discovered names through to body extraction
        # and that bare-slash lookalikes (like /tmp/foo) are filtered out.
        import json

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(
                root,
                "opencode.json",
                json.dumps({
                    "agents": {
                        "orchestrator": {
                            "description": "Orchestrates work.",
                            "prompt": (
                                "Run /plan first then /cycle."
                                " Mention /tmp/foo as a path, not a command."
                                " Trailing /push."
                            ),
                        }
                    },
                    "commands": {
                        "plan": {"description": "Plan."},
                        "cycle": {"description": "Cycle."},
                        "push": {"description": "Push."},
                    },
                }),
            )
            graph = discover_agent_graph(
                root,
                git_sha="abc123",
                updated_at="2026-04-30T00:00:00+00:00",
            )

        agent_id = "agent:opencode:orchestrator"
        edge_targets = {
            (edge["type"], edge["to"], edge["props"].get("confidence"))
            for edge in graph["edges"]
            if edge["from"] == agent_id
        }
        # All three known commands resolved with confidence=inferred.
        for cmd in ("plan", "cycle", "push"):
            self.assertIn(
                ("uses_command", f"command:opencode:{cmd}", "inferred"),
                edge_targets,
                f"orchestrator->{cmd} uses_command(inferred) missing; got {edge_targets}",
            )
        # /tmp/foo must NOT have produced any uses_command edge.
        for _t, to, _c in edge_targets:
            if "command:" in to:
                self.assertNotIn("tmp", to)
                self.assertNotIn("foo", to)

    def test_skill_call_in_body_resolves_to_skill_node(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(
                root,
                ".claude/skills/simplify/SKILL.md",
                "# Simplify\n\nDoes things.\n",
            )
            _write(
                root,
                ".claude/agents/orchestrator.md",
                textwrap.dedent(
                    """\
                    ---
                    name: orchestrator
                    ---

                    Body says: Skill(skill_name="simplify").
                    """
                ),
            )
            graph = discover_agent_graph(
                root,
                git_sha="abc123",
                updated_at="2026-04-30T00:00:00+00:00",
            )
        agent_id = "agent:claude:orchestrator"
        skill_edges = [
            (edge["to"], edge["props"].get("confidence"))
            for edge in graph["edges"]
            if edge["from"] == agent_id and edge["type"] == "uses_skill"
        ]
        self.assertEqual(len(skill_edges), 1, skill_edges)
        self.assertEqual(skill_edges[0][1], "inferred")
        self.assertEqual(skill_edges[0][0], "skill:claude:simplify")


if __name__ == "__main__":
    unittest.main()
