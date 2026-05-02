"""Tests for ``weld:`` frontmatter ``invokes_agents`` declaration support (slice 2).

Slice 2 of the agent-graph reality repair epic adds authoritative frontmatter
declarations for orchestrator agents and commands whose dynamic-dispatch
indirection (``subagent_type: "<implementer_type>"``) the body regex cannot
resolve. The new keys are::

    weld:
      invokes_agents: [name1, name2, ...]
      # subagents: [...]      # alias
      # dispatches_to: [...]  # alias

Edges emitted from these keys carry ``confidence=definite`` and dedupe-win
over inferred-confidence edges produced by body regex on the same file.
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

from weld.agent_graph_audit import audit_graph  # noqa: E402
from weld.agent_graph_discovery import discover_agent_graph  # noqa: E402
from weld.agent_graph_metadata import parse_agent_asset  # noqa: E402


def _write(root: Path, rel_path: str, text: str = "content\n") -> None:
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _agent_stub(name: str) -> str:
    return f"---\nname: {name}\n---\n\nbody\n"


class FrontmatterInvokesAgentsParseTest(unittest.TestCase):
    """Unit-level: parse_agent_asset reads weld.invokes_agents (and aliases)."""

    def test_invokes_agents_list_emits_definite_invokes_agent_edges(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(
                root,
                ".claude/agents/orchestrator.md",
                textwrap.dedent(
                    """\
                    ---
                    name: orchestrator
                    description: example orchestrator.
                    weld:
                      invokes_agents:
                        - architect
                        - reviewer
                        - qa
                    ---

                    Body has nothing relevant.
                    """
                ),
            )
            asset = parse_agent_asset(
                root, ".claude/agents/orchestrator.md", "agent", "claude",
            )
        edges = [r for r in asset.references if r.edge_type == "invokes_agent"]
        names_to_conf = {(r.target_name, r.confidence) for r in edges}
        self.assertEqual(
            names_to_conf,
            {
                ("architect", "definite"),
                ("reviewer", "definite"),
                ("qa", "definite"),
            },
        )
        for r in edges:
            self.assertEqual(r.target_type, "agent")

    def test_subagents_alias_emits_definite_invokes_agent_edges(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(
                root,
                ".claude/agents/sample.md",
                textwrap.dedent(
                    """\
                    ---
                    name: sample
                    weld:
                      subagents: [tdd, build-fixer]
                    ---
                    """
                ),
            )
            asset = parse_agent_asset(
                root, ".claude/agents/sample.md", "agent", "claude",
            )
        edges = {
            (r.target_name, r.confidence)
            for r in asset.references if r.edge_type == "invokes_agent"
        }
        self.assertEqual(
            edges,
            {("tdd", "definite"), ("build-fixer", "definite")},
        )

    def test_dispatches_to_alias_emits_definite_invokes_agent_edges(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(
                root,
                ".claude/commands/dispatch.md",
                textwrap.dedent(
                    """\
                    ---
                    name: dispatch
                    weld:
                      dispatches_to:
                        - worker
                    ---
                    """
                ),
            )
            asset = parse_agent_asset(
                root, ".claude/commands/dispatch.md", "command", "claude",
            )
        edges = {
            (r.target_name, r.confidence)
            for r in asset.references if r.edge_type == "invokes_agent"
        }
        self.assertEqual(edges, {("worker", "definite")})


class FrontmatterWinsOverRegexTest(unittest.TestCase):
    """Mixed regex+frontmatter on the same file: frontmatter wins on dedupe."""

    def test_frontmatter_definite_wins_over_body_inferred(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # Subagent target node so the materialized graph has it.
            _write(root, ".claude/agents/architect.md", _agent_stub("architect"))
            _write(
                root,
                ".claude/agents/orchestrator.md",
                textwrap.dedent(
                    """\
                    ---
                    name: orchestrator
                    description: orchestrator that uses architect.
                    weld:
                      invokes_agents: [architect]
                    ---

                    The body also says: subagent_type: "architect"
                    in pseudocode -- regex would emit inferred.
                    """
                ),
            )
            graph = discover_agent_graph(
                root,
                git_sha="abc123",
                updated_at="2026-04-30T00:00:00+00:00",
            )

        source_id = "agent:claude:orchestrator"
        target_id = "agent:claude:architect"
        invokes_edges = [
            edge for edge in graph["edges"]
            if edge["from"] == source_id and edge["to"] == target_id
            and edge["type"] == "invokes_agent"
        ]
        # Exactly one edge survives dedupe, and it must be definite (the
        # frontmatter declaration), not inferred (the body regex match).
        self.assertEqual(len(invokes_edges), 1, invokes_edges)
        self.assertEqual(
            invokes_edges[0]["props"].get("confidence"),
            "definite",
            "frontmatter-declared invokes_agent must dedupe-win over body regex",
        )


class BrokenDeclarationFlaggedTest(unittest.TestCase):
    """Audit must flag a frontmatter declaration whose target agent doesn't exist.

    Today, a command declaring a non-existent agent surfaces via the existing
    ``missing_agent`` audit code (``_commands_missing_agents``); the materialized
    target carries ``status="referenced"`` because no asset defines it. The new
    frontmatter key must feed that same machinery -- not introduce new audit
    codes.
    """

    def test_command_frontmatter_declares_missing_agent_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # Note: NO agent named "ghost" exists anywhere in the tree.
            _write(
                root,
                ".claude/commands/probe.md",
                textwrap.dedent(
                    """\
                    ---
                    name: probe
                    weld:
                      invokes_agents:
                        - ghost
                    ---
                    """
                ),
            )
            graph = discover_agent_graph(
                root,
                git_sha="abc123",
                updated_at="2026-04-30T00:00:00+00:00",
            )
            findings = audit_graph(graph)["findings"]

        target_id = "agent:claude:ghost"
        # The ghost agent must materialize as a referenced (not canonical)
        # node so existing checks can act on it.
        self.assertIn(target_id, graph["nodes"])
        self.assertEqual(
            graph["nodes"][target_id]["props"].get("status"),
            "referenced",
        )
        # And the audit must surface this via the existing missing_agent code,
        # not require a new code.
        codes = {f["code"] for f in findings}
        self.assertIn("missing_agent", codes)
        ghost_findings = [
            f for f in findings
            if f["code"] == "missing_agent"
            and any("ghost" in (n.get("name") or "") for n in f.get("nodes") or [])
        ]
        self.assertTrue(
            ghost_findings,
            f"expected missing_agent finding referencing ghost; got {findings}",
        )


class WorkerFrontmatterIntegrationTest(unittest.TestCase):
    """Repo-realistic: a worker-style agent declares its full pipeline.

    Confidence must be definite for every declared edge, and dedupe must
    eliminate any inferred copy from body pseudocode on the same edge.
    """

    def test_worker_full_pipeline_definite(self) -> None:
        pipeline = (
            "architect", "challenger", "tdd", "migration", "build-fixer",
            "reviewer", "qa", "security", "analyze",
        )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name in pipeline:
                _write(root, f".claude/agents/{name}.md", _agent_stub(name))
            agents_yaml = textwrap.dedent(
                """\
                ---
                name: worker
                description: orchestrator.
                weld:
                  invokes_agents: [{names}]
                ---

                ```
                Agent(subagent_type: "architect")  # body regex would mark inferred
                ```
                """
            ).format(names=", ".join(pipeline))
            _write(root, ".claude/agents/worker.md", agents_yaml)
            graph = discover_agent_graph(
                root,
                git_sha="abc123",
                updated_at="2026-04-30T00:00:00+00:00",
            )

        worker_id = "agent:claude:worker"
        seen: dict[str, str] = {}
        for edge in graph["edges"]:
            if edge["from"] != worker_id or edge["type"] != "invokes_agent":
                continue
            seen[edge["to"]] = edge["props"].get("confidence", "")
        for name in pipeline:
            target_id = f"agent:claude:{name}"
            self.assertIn(target_id, seen, f"missing invokes_agent->{name}")
            self.assertEqual(
                seen[target_id],
                "definite",
                f"{name} expected definite, got {seen[target_id]}",
            )


if __name__ == "__main__":
    unittest.main()
