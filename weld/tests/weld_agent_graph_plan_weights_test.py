"""Edge-weighted secondary-asset filtering for impact / plan-change.

Customer-reported v0.8.2 noise: a session-index file
(`.ai/sessions/INDEX.md`) connected to the change set only by an
incidental ``references_file`` edge surfaced as a secondary asset
alongside semantically coupled assets. ADR 0030 introduces edge-type
weights and a 1.0 secondary threshold; this test pins that behavior
on a synthetic graph that mirrors the customer scenario.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.agent_graph_inventory import impact_asset  # noqa: E402
from weld.agent_graph_plan import plan_change  # noqa: E402


def _asset_props(name: str, file: str, description: str = "") -> dict:
    return {
        "name": name,
        "file": file,
        "description": description,
        "platform": "github-copilot",
        "platform_name": "GitHub Copilot",
        "source_strategy": "agent_graph_static",
    }


def _customer_graph() -> dict:
    """Build a graph that mirrors the customer scenario.

    - ``agent:planner`` is the change target (primary asset).
    - ``skill:architecture-decision`` is connected by ``uses_skill``
      (strong, weight 5).
    - ``file:session-index`` is connected only by ``references_file``
      (incidental, weight 0.5) -- this is the noise.
    - ``file:install-script`` is connected only by ``references_file``
      (incidental) -- second piece of noise.
    """
    nodes = {
        "agent:planner": {
            "type": "agent",
            "label": "planner",
            "props": _asset_props(
                "planner",
                ".github/agents/planner.agent.md",
                "Plans implementation work.",
            ),
        },
        "skill:architecture-decision": {
            "type": "skill",
            "label": "architecture-decision",
            "props": _asset_props(
                "architecture-decision",
                ".claude/skills/architecture-decision/SKILL.md",
                "Records ADRs.",
            ),
        },
        "file:session-index": {
            "type": "file",
            "label": "session-index",
            "props": {
                "name": "INDEX.md",
                "file": ".ai/sessions/INDEX.md",
                "platform": "generic",
                "platform_name": "generic",
            },
        },
        "file:install-script": {
            "type": "file",
            "label": "install-beads.sh",
            "props": {
                "name": "install-beads.sh",
                "file": "scripts/install-beads.sh",
                "platform": "generic",
                "platform_name": "generic",
            },
        },
    }
    edges = [
        # Strong semantic edge -- weight 5.
        {
            "from": "agent:planner",
            "to": "skill:architecture-decision",
            "type": "uses_skill",
            "props": {},
        },
        # Incidental text mention -- weight 0.5, below threshold.
        {
            "from": "file:session-index",
            "to": "agent:planner",
            "type": "references_file",
            "props": {},
        },
        # Second incidental mention from a different file.
        {
            "from": "file:install-script",
            "to": "agent:planner",
            "type": "references_file",
            "props": {},
        },
    ]
    return {"nodes": nodes, "edges": edges}


class PlanWeightsTest(unittest.TestCase):
    def test_incidental_only_assets_filtered_from_secondary(self) -> None:
        graph = _customer_graph()
        plan = plan_change(graph, "planner consolidate copilot instructions")

        primary_ids = {asset["id"] for asset in plan["primary_assets"]}
        self.assertIn("agent:planner", primary_ids)

        secondary_ids = {asset["id"] for asset in plan["secondary_assets"]}
        # Strong-edge sibling kept.
        self.assertIn("skill:architecture-decision", secondary_ids)
        # Incidental-only siblings filtered out (the customer-reported
        # noise).
        self.assertNotIn("file:session-index", secondary_ids)
        self.assertNotIn("file:install-script", secondary_ids)

        # Same filtering visible on the file path layer.
        self.assertIn(
            ".claude/skills/architecture-decision/SKILL.md",
            plan["secondary_files"],
        )
        self.assertNotIn(".ai/sessions/INDEX.md", plan["secondary_files"])
        self.assertNotIn(
            "scripts/install-beads.sh", plan["secondary_files"],
        )

    def test_impact_carries_weight_and_strong_outranks_incidental(self) -> None:
        graph = _customer_graph()
        impact = impact_asset(graph, "agent:planner")
        self.assertIsNotNone(impact)
        assert impact is not None  # narrow Optional for the type checker

        affected = {entry["id"]: entry for entry in impact["affected_nodes"]}

        # Strong edge: weight >= 5.
        self.assertGreaterEqual(
            affected["skill:architecture-decision"]["impact_weight"], 5.0,
        )
        # Each incidental-only asset stays in affected_nodes (explain /
        # impact still surfaces them) but carries the small weight that
        # plan-change uses to filter.
        self.assertLess(affected["file:session-index"]["impact_weight"], 1.0)
        self.assertLess(affected["file:install-script"]["impact_weight"], 1.0)
        # Strong sibling outranks the incidental siblings.
        self.assertGreater(
            affected["skill:architecture-decision"]["impact_weight"],
            affected["file:session-index"]["impact_weight"],
        )

    def test_two_incidental_edges_clear_threshold(self) -> None:
        # If the same asset is mentioned twice as text, accept it as a
        # plausible secondary -- aggregate weight 1.0 hits the cutoff.
        graph = _customer_graph()
        graph["edges"].append({
            "from": "file:session-index",
            "to": "agent:planner",
            "type": "references_file",
            "props": {"raw": "second mention"},
        })

        plan = plan_change(graph, "planner consolidate copilot instructions")
        secondary_ids = {asset["id"] for asset in plan["secondary_assets"]}
        self.assertIn("file:session-index", secondary_ids)
        # Single-mention asset still filtered.
        self.assertNotIn("file:install-script", secondary_ids)


def _canonical_asset_props(name: str, file: str, description: str = "") -> dict:
    """Asset props that mark the node as the canonical authority."""
    props = _asset_props(name, file, description)
    props["authority"] = "canonical"
    return props


def _canonical_bypass_graph() -> dict:
    """Graph where a canonical asset is reachable only via text-mention.

    - ``agent:planner`` is the change target (primary asset).
    - ``skill:incidental-canonical`` is canonical authority but
      connected ONLY by an incidental ``references_file`` edge
      (weight 0.5, below the 1.0 cutoff). Without the bypass, the
      operator would lose visibility on the authoritative source.
    - ``skill:incidental-noise`` is the matching control: same
      sub-threshold incidental edge, but not canonical -- it must
      stay filtered.
    """
    nodes = {
        "agent:planner": {
            "type": "agent",
            "label": "planner",
            "props": _asset_props(
                "planner",
                ".github/agents/planner.agent.md",
                "Plans implementation work.",
            ),
        },
        "skill:incidental-canonical": {
            "type": "skill",
            "label": "incidental-canonical",
            "props": _canonical_asset_props(
                "incidental-canonical",
                ".claude/skills/incidental-canonical/SKILL.md",
                "Authoritative source reachable only by text mention.",
            ),
        },
        "skill:incidental-noise": {
            "type": "skill",
            "label": "incidental-noise",
            "props": _asset_props(
                "incidental-noise",
                ".claude/skills/incidental-noise/SKILL.md",
                "Non-canonical sibling on the same incidental edge.",
            ),
        },
    }
    edges = [
        {
            "from": "agent:planner",
            "to": "skill:incidental-canonical",
            "type": "references_file",
            "props": {},
        },
        {
            "from": "agent:planner",
            "to": "skill:incidental-noise",
            "type": "references_file",
            "props": {},
        },
    ]
    return {"nodes": nodes, "edges": edges}


class CanonicalBypassTest(unittest.TestCase):
    """Audit follow-up to ADR 0030: canonical-authority assets stay
    visible to the operator even when reachable only via low-weight
    (incidental) edges."""

    def test_canonical_only_text_mention_surfaces_in_secondary(self) -> None:
        graph = _canonical_bypass_graph()
        plan = plan_change(graph, "planner consolidate copilot instructions")

        primary_ids = {asset["id"] for asset in plan["primary_assets"]}
        self.assertIn("agent:planner", primary_ids)

        secondary_ids = {asset["id"] for asset in plan["secondary_assets"]}
        # Canonical asset on a sub-threshold edge bypasses the cutoff.
        self.assertIn("skill:incidental-canonical", secondary_ids)
        # Non-canonical sibling on the same edge stays filtered.
        self.assertNotIn("skill:incidental-noise", secondary_ids)

        # File-path layer mirrors the same decision.
        self.assertIn(
            ".claude/skills/incidental-canonical/SKILL.md",
            plan["secondary_files"],
        )
        self.assertNotIn(
            ".claude/skills/incidental-noise/SKILL.md",
            plan["secondary_files"],
        )

    def test_canonical_bypass_preserves_status_field(self) -> None:
        # Sanity: the surfaced canonical asset keeps its status so
        # downstream consumers can render it as authoritative.
        graph = _canonical_bypass_graph()
        plan = plan_change(graph, "planner consolidate copilot instructions")
        by_id = {asset["id"]: asset for asset in plan["secondary_assets"]}
        self.assertEqual(by_id["skill:incidental-canonical"]["status"], "canonical")

    def test_canonical_bypass_works_for_incoming_edge_direction(self) -> None:
        # Both `_secondary_assets` paths (downstream and incoming) feed
        # the same candidate dict. Pin that the bypass applies regardless
        # of which direction the incidental edge points.
        graph = _canonical_bypass_graph()
        # Flip the canonical asset's edge so it is an incoming reference
        # into the primary asset rather than a downstream one.
        graph["edges"] = [
            {
                "from": "skill:incidental-canonical",
                "to": "agent:planner",
                "type": "references_file",
                "props": {},
            },
            {
                "from": "skill:incidental-noise",
                "to": "agent:planner",
                "type": "references_file",
                "props": {},
            },
        ]
        plan = plan_change(graph, "planner consolidate copilot instructions")
        secondary_ids = {asset["id"] for asset in plan["secondary_assets"]}
        self.assertIn("skill:incidental-canonical", secondary_ids)
        self.assertNotIn("skill:incidental-noise", secondary_ids)


if __name__ == "__main__":
    unittest.main()
