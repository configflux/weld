"""Cross-platform Agent Graph demo coverage assertions.

Slice 2 dogfood (bd c5x2): the demo must reflect observed real-app
complexity. These assertions guarantee the (platform x asset-type x
edge-type) coverage promised in the audit at
``docs/agent-graph-real-app-audit.md``.

Each test is a column of the platform-support matrix in the slice-1
audit. If an assertion regresses, the demo no longer proves
cross-platform parity for the affected pattern.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_repo_root = Path(__file__).resolve().parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from weld.agent_graph_discovery import discover_agent_graph  # noqa: E402

_DEMO_ROOT = _repo_root / "examples" / "agent-graph-demo"

_EXPECTED_DEMO_PLATFORMS = frozenset({
    "claude",
    "codex",
    "cursor",
    "gemini",
    "generic",
    "github-copilot",
    "opencode",
})

# 9 asset types from the audit's coverage matrix. `tool` nodes are
# emitted from `tools:` lists / permission allowlists rather than
# from a file pattern, so they appear without a corresponding file.
_EXPECTED_DEMO_ASSET_TYPES = frozenset({
    "agent",
    "command",
    "skill",
    "instruction",
    "config",
    "hook",
    "mcp-server",
    "prompt",
    "tool",
})

_EXPECTED_DEMO_EDGE_TYPES = frozenset({
    "invokes_agent",
    "uses_skill",
    "uses_command",
    "handoff_to",
    "provides_tool",
    "restricts_tool",
    "applies_to_path",
    "references_file",
    "triggers_on_event",
    "configures",
    "generated_from",
})


class AgentGraphDemoCoverageTest(unittest.TestCase):
    """The demo is the non-Claude proof harness for slice-3 patterns."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._graph = discover_agent_graph(
            _DEMO_ROOT,
            git_sha="demo",
            updated_at="2026-04-24T00:00:00+00:00",
        )

    def test_demo_covers_seven_platforms(self) -> None:
        platforms = {
            n.get("props", {}).get("platform")
            for n in self._graph["nodes"].values()
            if n.get("type") in {
                "agent", "command", "config", "hook", "instruction",
                "mcp-server", "prompt", "skill",
            }
        }
        platforms.discard(None)
        missing = _EXPECTED_DEMO_PLATFORMS - platforms
        self.assertEqual(missing, set(), f"missing platforms: {missing}")

    def test_demo_covers_nine_asset_types(self) -> None:
        types = {n.get("type") for n in self._graph["nodes"].values()}
        missing = _EXPECTED_DEMO_ASSET_TYPES - types
        self.assertEqual(missing, set(), f"missing asset types: {missing}")

    def test_demo_emits_eleven_edge_types(self) -> None:
        types = {e.get("type") for e in self._graph["edges"]}
        missing = _EXPECTED_DEMO_EDGE_TYPES - types
        self.assertEqual(missing, set(), f"missing edge types: {missing}")

    def test_demo_has_codex_platform_assets(self) -> None:
        # The codex platform is the largest gap noted in the slice-1
        # audit (no .codex/ tree in the demo before slice 2). Discovery
        # surfaces codex via AGENTS.override.md and any .codex/agents/*.md
        # under the codex platform key.
        codex_assets = [
            n for n in self._graph["nodes"].values()
            if n.get("props", {}).get("platform") == "codex"
        ]
        self.assertGreater(
            len(codex_assets), 0,
            "no codex-platform nodes -- Codex tree missing from demo",
        )

    def test_demo_has_orchestrator_with_placeholder_indirection(self) -> None:
        # The worker-shaped orchestrator agent must declare its
        # placeholder targets via `weld: invokes_agents:` frontmatter
        # (slice-2 hw6j convention) so the resolved invokes_agent edges
        # land. The slice-1 q8rl regex contributes the literal calls.
        orchestrator_id = None
        for node_id, node in self._graph["nodes"].items():
            props = node.get("props", {})
            if (
                node.get("type") == "agent"
                and props.get("name") == "orchestrator"
                and props.get("platform") == "claude"
            ):
                orchestrator_id = node_id
                break
        self.assertIsNotNone(
            orchestrator_id,
            "orchestrator agent missing from demo .claude/agents/",
        )
        invokes = {
            edge.get("to") for edge in self._graph["edges"]
            if edge.get("from") == orchestrator_id
            and edge.get("type") == "invokes_agent"
        }
        # The worker-shaped pattern dispatches to multiple specialist
        # agents; we want at least 5 distinct invokes_agent targets to
        # mirror the real worker.md (tdd / migration / build-fixer /
        # reviewer / qa).
        self.assertGreaterEqual(
            len(invokes), 5,
            f"orchestrator should invoke >=5 agents; got {len(invokes)}",
        )

    def test_demo_codex_config_toml_emits_mcp_servers_with_codex_platform(self) -> None:
        # Slice-3 (a5) asuh cross-platform proof: the demo's
        # .codex/config.toml must surface mcp-server nodes with
        # ``platform=codex`` and a ``configures`` edge from the codex
        # config asset (not collapse onto generic .mcp.json nodes).
        config_id = "config:codex:codex-config"
        self.assertIn(config_id, self._graph["nodes"])
        self.assertEqual(self._graph["nodes"][config_id]["props"].get("platform"), "codex")
        codex_targets = {
            edge["to"] for edge in self._graph["edges"]
            if edge.get("from") == config_id and edge.get("type") == "configures"
            and edge["to"].startswith("mcp-server:codex:")
        }
        self.assertGreater(len(codex_targets), 0, f"no codex mcp targets: {codex_targets}")

    def test_demo_opencode_agent_description_emits_inferred_uses_command(self) -> None:
        # Slice-3 (a1) k58t cross-platform proof: opencode (non-Claude) agent
        # with a bare-/command in its frontmatter ``description`` must mint a
        # confidence=inferred uses_command edge against a discovered command.
        agent_id = "agent:opencode:release-notes-writer"
        cmd_id = "command:opencode:ship"
        edges = {
            (edge.get("type"), edge.get("to"), edge.get("props", {}).get("confidence"))
            for edge in self._graph["edges"]
            if edge.get("from") == agent_id
        }
        self.assertIn(("uses_command", cmd_id, "inferred"), edges, edges)

    def test_demo_has_skill_with_multiple_explicit_in_edges(self) -> None:
        # `uses_skill` is an explicit static edge. The demo must prove the
        # edge through declared metadata or typed references rather than
        # relying on platform runtime routing or prose-only mentions.
        skill_in_edges: dict[str, int] = {}
        for edge in self._graph["edges"]:
            if edge.get("type") != "uses_skill":
                continue
            provenance = edge.get("props", {}).get("provenance", {})
            self.assertIn("raw", provenance, edge)
            target = edge.get("to")
            if not target:
                continue
            skill_in_edges[target] = skill_in_edges.get(target, 0) + 1
        multi_used = [tgt for tgt, n in skill_in_edges.items() if n > 1]
        self.assertGreater(
            len(multi_used), 0,
            f"no skill has >1 uses_skill in-edge; counts={skill_in_edges}",
        )


if __name__ == "__main__":
    unittest.main()
