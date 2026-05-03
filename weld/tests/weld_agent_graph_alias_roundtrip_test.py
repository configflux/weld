"""Round-trip closure test for agent-graph skill IDs (ADR 0041 PR 2).

Two paths to the same logical skill -- one Phase 1 SKILL.md asset and one
Phase 3 agent reference (``uses_skills: foo``) -- must merge into a single
canonical node ``skill:generic:foo`` rather than splitting into a
SHA1-suffixed pair (the historical
``agent_graph_materialize._node_id_for_values`` behaviour). This regression
guards the user-reported symptom: two ``skill:generic:architecture-decision``
nodes with different SHA1 tails, one of them orphan.

The closure contract is documented in
``docs/adrs/0041-graph-closure-determinism.md`` (Layer 1 + Layer 2).
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


def _write(root: Path, rel_path: str, text: str) -> None:
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class SkillAliasRoundtripTest(unittest.TestCase):
    """ADR 0041 PR 2: Phase 1 SKILL.md + Phase 3 agent ref merge to one node."""

    def test_two_paths_to_same_skill_merge_into_one_canonical_node(self) -> None:
        """A SKILL.md asset and an agent ``uses_skills: foo`` reference for
        the same logical skill must yield exactly one ``skill:generic:foo``
        node, with both source paths recorded under ``sources``.
        """
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # Phase 1: SKILL.md for the canonical "foo" skill.
            _write(
                root,
                "skills/foo/SKILL.md",
                textwrap.dedent(
                    """\
                    ---
                    name: foo
                    description: A test skill discovered as a generic SKILL.md asset.
                    ---

                    # Foo

                    Reference body.
                    """
                ),
            )
            # Phase 3: agent that references the skill via frontmatter
            # ``uses_skills``. The reference resolves through the skill index;
            # if no canonical node exists for the index lookup, the
            # reference path falls through to the registry's merge primitive
            # and must NOT mint a SHA1-suffixed sibling node.
            _write(
                root,
                ".claude/agents/caller.md",
                textwrap.dedent(
                    """\
                    ---
                    name: caller
                    description: Test agent that uses the foo skill.
                    uses_skills:
                      - foo
                    ---

                    # Caller

                    Body content.
                    """
                ),
            )

            graph = discover_agent_graph(
                root,
                git_sha="fixture",
                updated_at="2026-05-02T00:00:00+00:00",
            )

        nodes = graph["nodes"]

        # Exactly one skill node for foo (canonical, no SHA1 suffix).
        skill_ids = [
            node_id for node_id, node in nodes.items()
            if node["type"] == "skill"
            and node["props"].get("name") == "foo"
        ]
        self.assertEqual(
            skill_ids,
            ["skill:generic:foo"],
            f"expected exactly one canonical skill:generic:foo node, got {skill_ids}",
        )

        # No SHA1-suffixed skill IDs anywhere in the graph.
        for node_id in nodes:
            if node_id.startswith("skill:") and node_id != "skill:generic:foo":
                self.assertNotRegex(
                    node_id,
                    r"^skill:[^:]+:[^:]+:[0-9a-f]{8}$",
                    f"unexpected SHA1-suffixed skill ID: {node_id}",
                )

        # The canonical node's sources record both discovery paths.
        canonical = nodes["skill:generic:foo"]
        sources = canonical.get("props", {}).get("sources") or []
        self.assertTrue(
            any("skills/foo/SKILL.md" in str(s) for s in sources),
            f"expected SKILL.md path in sources, got {sources}",
        )

        # Inbound edge from the agent resolves to the one canonical skill ID.
        agent_ids = [
            node_id for node_id, node in nodes.items()
            if node["type"] == "agent"
            and node["props"].get("name") == "caller"
        ]
        self.assertEqual(len(agent_ids), 1, f"expected one caller agent, got {agent_ids}")
        agent_id = agent_ids[0]

        skill_inbound = [
            edge for edge in graph["edges"]
            if edge["to"] == "skill:generic:foo"
            and edge["from"] == agent_id
        ]
        self.assertTrue(
            skill_inbound,
            f"expected an edge from {agent_id} to skill:generic:foo; "
            f"all edges to skill: "
            f"{[e for e in graph['edges'] if e['to'].startswith('skill:')]}",
        )

    def test_two_skill_md_assets_for_same_skill_merge(self) -> None:
        """Two SKILL.md files at different paths classified as the same
        generic skill (the user-reported ``architecture-decision``
        symptom) must merge into one canonical node, not split via a
        SHA1-suffix disambiguator.
        """
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(
                root,
                "skills/architecture-decision/SKILL.md",
                "---\nname: architecture-decision\n---\nbody\n",
            )
            _write(
                root,
                "examples/demo/skills/architecture-decision/SKILL.md",
                "---\nname: architecture-decision\n---\nbody\n",
            )

            graph = discover_agent_graph(
                root,
                git_sha="fixture",
                updated_at="2026-05-02T00:00:00+00:00",
            )

        # Exactly one skill node; no SHA1-suffixed siblings.
        skill_ids = sorted(
            node_id for node_id, node in graph["nodes"].items()
            if node["type"] == "skill"
            and node["props"].get("name") == "architecture-decision"
        )
        self.assertEqual(skill_ids, ["skill:generic:architecture-decision"])

        canonical = graph["nodes"]["skill:generic:architecture-decision"]
        sources = canonical.get("props", {}).get("sources") or []
        # Both source paths recorded under sources.
        joined = " ".join(str(s) for s in sources)
        self.assertIn("skills/architecture-decision/SKILL.md", joined)
        self.assertIn(
            "examples/demo/skills/architecture-decision/SKILL.md",
            joined,
        )


if __name__ == "__main__":
    unittest.main()
