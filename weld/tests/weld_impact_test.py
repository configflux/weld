"""Tests for ``weld.impact`` -- blast-radius analysis for graph nodes/files."""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.contract import SCHEMA_VERSION  # noqa: E402


_FIXTURE_NODES: dict[str, dict] = {
    "file:weld/graph.py": {
        "type": "file",
        "label": "weld/graph.py",
        "props": {"file": "weld/graph.py"},
    },
    "symbol:py:weld.graph:Graph.query": {
        "type": "symbol",
        "label": "Graph.query",
        "props": {
            "file": "weld/graph.py",
            "module": "weld.graph",
            "qualname": "Graph.query",
            "language": "python",
        },
    },
    "command:wd query": {
        "type": "command",
        "label": "wd query",
        "props": {"file": "weld/cli.py"},
    },
    "tool:weld_query": {
        "type": "tool",
        "label": "weld_query",
        "props": {"file": "weld/mcp_server.py"},
    },
    "route:GET:/api/search": {
        "type": "route",
        "label": "GET /api/search",
        "props": {
            "file": "api/routes/search.py",
            "protocol": "http",
            "surface_kind": "request_response",
            "boundary_kind": "inbound",
        },
    },
    "entrypoint:cli": {
        "type": "entrypoint",
        "label": "cli entrypoint",
        "props": {"file": "weld/__main__.py"},
    },
    "boundary:public-api": {
        "type": "boundary",
        "label": "public api",
        "props": {"file": "api/__init__.py", "boundary_kind": "inbound"},
    },
    "service:search": {
        "type": "service",
        "label": "search service",
        "props": {"file": "services/search.py"},
    },
}

_FIXTURE_EDGES: list[dict] = [
    {
        "from": "command:wd query",
        "to": "symbol:py:weld.graph:Graph.query",
        "type": "invokes",
        "props": {},
    },
    {
        "from": "tool:weld_query",
        "to": "symbol:py:weld.graph:Graph.query",
        "type": "invokes",
        "props": {},
    },
    {
        "from": "route:GET:/api/search",
        "to": "symbol:py:weld.graph:Graph.query",
        "type": "invokes",
        "props": {},
    },
    {
        "from": "entrypoint:cli",
        "to": "command:wd query",
        "type": "contains",
        "props": {},
    },
    {
        "from": "boundary:public-api",
        "to": "route:GET:/api/search",
        "type": "contains",
        "props": {},
    },
    {
        "from": "service:search",
        "to": "route:GET:/api/search",
        "type": "exposes",
        "props": {},
    },
    {
        "from": "service:search",
        "to": "boundary:public-api",
        "type": "exposes",
        "props": {},
    },
]


def _make_root() -> Path:
    root = Path(tempfile.mkdtemp())
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    (root / ".weld" / "graph.json").write_text(
        json.dumps(
            {
                "meta": {
                    "version": SCHEMA_VERSION,
                    "git_sha": "deadbeef",
                    "updated_at": "2026-04-13T00:00:00+00:00",
                },
                "nodes": _FIXTURE_NODES,
                "edges": _FIXTURE_EDGES,
            }
        ),
        encoding="utf-8",
    )
    return root


class ImpactAnalysisTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = _make_root()

    def test_node_target_returns_direct_and_transitive_dependents(self) -> None:
        from weld.graph import Graph
        from weld.impact import impact

        graph = Graph(self.root)
        graph.load()

        result = impact(graph, target="symbol:py:weld.graph:Graph.query", depth=2)

        self.assertEqual(result["impact_version"], 1)
        self.assertEqual(result["target"]["kind"], "node")
        direct = {node["id"] for node in result["direct_dependents"]}
        transitive = {node["id"] for node in result["transitive_dependents"]}
        self.assertEqual(
            direct,
            {
                "command:wd query",
                "tool:weld_query",
                "route:GET:/api/search",
            },
        )
        self.assertEqual(
            transitive,
            {
                "entrypoint:cli",
                "boundary:public-api",
                "service:search",
            },
        )
        self.assertEqual(result["risk_level"], "HIGH")

    def test_path_target_resolves_file_and_symbol_nodes(self) -> None:
        from weld.graph import Graph
        from weld.impact import impact

        graph = Graph(self.root)
        graph.load()

        result = impact(graph, target="weld/graph.py", depth=1)

        self.assertEqual(result["target"]["kind"], "path")
        self.assertEqual(
            result["target"]["resolved_nodes"],
            ["file:weld/graph.py", "symbol:py:weld.graph:Graph.query"],
        )

    def test_depth_limit_and_cycle_dedup(self) -> None:
        from weld.graph import Graph
        from weld.impact import impact

        graph = Graph(self.root)
        graph.load()

        result = impact(graph, target="symbol:py:weld.graph:Graph.query", depth=3)

        dependents = result["direct_dependents"] + result["transitive_dependents"]
        ids = [node["id"] for node in dependents]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids.count("service:search"), 1)

    def test_unknown_target_returns_warning(self) -> None:
        from weld.graph import Graph
        from weld.impact import impact

        graph = Graph(self.root)
        graph.load()

        result = impact(graph, target="missing.py", depth=2)

        self.assertEqual(result["direct_dependents"], [])
        self.assertEqual(result["transitive_dependents"], [])
        self.assertEqual(result["warnings"], ["no nodes matched target: missing.py"])

    def test_symbol_only_dependents_still_surface_cli_and_mcp_risk(self) -> None:
        from weld.graph import Graph
        from weld.impact import impact

        root = Path(tempfile.mkdtemp())
        (root / ".weld").mkdir(parents=True, exist_ok=True)
        (root / ".weld" / "graph.json").write_text(
            json.dumps(
                {
                    "meta": {
                        "version": SCHEMA_VERSION,
                        "git_sha": "deadbeef",
                        "updated_at": "2026-04-13T00:00:00+00:00",
                    },
                    "nodes": {
                        "symbol:py:weld.trace:trace": {
                            "type": "symbol",
                            "label": "trace",
                            "props": {
                                "file": "weld/trace.py",
                                "module": "weld.trace",
                                "qualname": "trace",
                                "language": "python",
                            },
                        },
                        "symbol:py:weld.mcp_helpers:weld_trace": {
                            "type": "symbol",
                            "label": "weld_trace",
                            "props": {
                                "file": "weld/mcp_helpers.py",
                                "module": "weld.mcp_helpers",
                                "qualname": "weld_trace",
                                "language": "python",
                            },
                        },
                        "symbol:py:weld.cli:main": {
                            "type": "symbol",
                            "label": "main",
                            "props": {
                                "file": "weld/cli.py",
                                "module": "weld.cli",
                                "qualname": "main",
                                "language": "python",
                            },
                        },
                    },
                    "edges": [
                        {
                            "from": "symbol:py:weld.mcp_helpers:weld_trace",
                            "to": "symbol:py:weld.trace:trace",
                            "type": "calls",
                            "props": {},
                        },
                        {
                            "from": "symbol:py:weld.cli:main",
                            "to": "symbol:py:weld.mcp_helpers:weld_trace",
                            "type": "calls",
                            "props": {},
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )

        graph = Graph(root)
        graph.load()

        result = impact(graph, target="symbol:py:weld.trace:trace", depth=2)

        self.assertEqual(
            [node["label"] for node in result["affected_surfaces"]["mcp_tools"]],
            ["weld_trace"],
        )
        self.assertEqual(
            [node["label"] for node in result["affected_surfaces"]["entrypoints"]],
            ["wd entrypoint"],
        )
        self.assertEqual(result["risk_level"], "HIGH")


class ImpactCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = _make_root()

    def test_cli_json_dispatches_impact(self) -> None:
        from weld.cli import main as cli_main

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            rc = cli_main(
                ["impact", "weld/graph.py", "--depth", "2", "--json", "--root", str(self.root)]
            )

        self.assertEqual(rc, 0)
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["target"]["kind"], "path")
        self.assertEqual(result["risk_level"], "HIGH")

    def test_help_text_mentions_impact(self) -> None:
        from weld.cli import _HELP

        self.assertIn("impact", _HELP)


class ImpactMcpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = _make_root()

    def test_tool_registered_and_dispatch_matches_helper(self) -> None:
        from weld import mcp_server

        names = {tool.name for tool in mcp_server.build_tools()}
        self.assertIn("weld_impact", names)

        dispatch_result = mcp_server.dispatch(
            "weld_impact",
            {"target": "symbol:py:weld.graph:Graph.query", "depth": 2},
            root=self.root,
        )
        helper_result = mcp_server.weld_impact(
            "symbol:py:weld.graph:Graph.query",
            depth=2,
            root=self.root,
        )
        self.assertEqual(dispatch_result, helper_result)

    def test_tool_schema_requires_target(self) -> None:
        from weld import mcp_server

        by_name = {tool.name: tool for tool in mcp_server.build_tools()}
        schema = by_name["weld_impact"].input_schema
        self.assertEqual(schema["required"], ["target"])
        self.assertIn("depth", schema["properties"])


if __name__ == "__main__":
    unittest.main()
