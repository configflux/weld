"""Tests for persisted Agent Graph storage."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.agent_graph_storage import (  # noqa: E402
    AGENT_GRAPH_VERSION,
    AgentGraphNotFoundError,
    agent_graph_path,
    build_agent_graph,
    load_agent_graph,
    write_agent_graph,
)


class AgentGraphStorageTest(unittest.TestCase):
    def test_missing_graph_error_names_path_and_discovery_command(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with self.assertRaises(AgentGraphNotFoundError) as ctx:
                load_agent_graph(root)

        message = str(ctx.exception)
        self.assertIn(".weld/agent-graph.json", message)
        self.assertIn("wd agents discover", message)

    def test_build_stamps_required_metadata_and_hashes_sources(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".github" / "agents").mkdir(parents=True)
            source = root / ".github" / "agents" / "planner.agent.md"
            source.write_text("planner\n", encoding="utf-8")

            graph = build_agent_graph(
                root=root,
                nodes={"agent:planner": {"type": "agent", "label": "planner", "props": {}}},
                edges=[],
                discovered_from=[
                    ".github/agents/planner.agent.md",
                    ".github/agents/planner.agent.md",
                ],
                diagnostics=[{"severity": "warning", "message": "example"}],
                git_sha="abc123",
                updated_at="2026-04-24T00:00:00+00:00",
            )

        self.assertEqual(graph["meta"]["version"], AGENT_GRAPH_VERSION)
        self.assertEqual(
            graph["meta"]["discovered_from"],
            [".github/agents/planner.agent.md"],
        )
        self.assertIn(
            ".github/agents/planner.agent.md",
            graph["meta"]["source_hashes"],
        )
        self.assertEqual(graph["meta"]["git_sha"], "abc123")
        self.assertEqual(
            graph["meta"]["diagnostics"],
            [{"severity": "warning", "message": "example"}],
        )

    def test_write_and_load_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            graph = build_agent_graph(
                root=root,
                nodes={
                    "skill:review": {"type": "skill", "label": "review", "props": {}},
                    "agent:planner": {"type": "agent", "label": "planner", "props": {}},
                },
                edges=[
                    {
                        "from": "skill:review",
                        "to": "agent:planner",
                        "type": "references_file",
                        "props": {"z": 1},
                    },
                    {
                        "from": "agent:planner",
                        "to": "skill:review",
                        "type": "uses_skill",
                        "props": {"a": 1},
                    },
                ],
                discovered_from=[],
                source_hashes={"b.md": "2", "a.md": "1"},
                git_sha="abc123",
                updated_at="2026-04-24T00:00:00+00:00",
            )

            path = write_agent_graph(root, graph)
            first = path.read_text(encoding="utf-8")
            loaded = load_agent_graph(root)
            write_agent_graph(root, loaded)
            second = path.read_text(encoding="utf-8")

        self.assertEqual(path.name, "agent-graph.json")
        self.assertEqual(first, second)
        parsed = json.loads(first)
        self.assertEqual(list(parsed["nodes"].keys()), ["agent:planner", "skill:review"])
        self.assertEqual(
            [(e["from"], e["to"], e["type"]) for e in parsed["edges"]],
            [
                ("agent:planner", "skill:review", "uses_skill"),
                ("skill:review", "agent:planner", "references_file"),
            ],
        )

    def test_build_does_not_mutate_inputs(self) -> None:
        nodes = {"agent:planner": {"type": "agent", "label": "planner", "props": {}}}
        edges = [{"from": "agent:planner", "to": "skill:x", "type": "uses_skill", "props": {}}]
        diagnostics = [{"message": "before"}]

        with tempfile.TemporaryDirectory() as td:
            graph = build_agent_graph(
                root=Path(td),
                nodes=nodes,
                edges=edges,
                discovered_from=[],
                diagnostics=diagnostics,
                source_hashes={},
                updated_at="2026-04-24T00:00:00+00:00",
            )

        graph["nodes"]["agent:planner"]["props"]["changed"] = True
        graph["edges"][0]["props"]["changed"] = True
        graph["meta"]["diagnostics"][0]["message"] = "after"

        self.assertEqual(nodes["agent:planner"]["props"], {})
        self.assertEqual(edges[0]["props"], {})
        self.assertEqual(diagnostics, [{"message": "before"}])

    def test_agent_graph_path_is_repo_local(self) -> None:
        root = Path("/repo")
        self.assertEqual(agent_graph_path(root), root / ".weld" / "agent-graph.json")


if __name__ == "__main__":
    unittest.main()
