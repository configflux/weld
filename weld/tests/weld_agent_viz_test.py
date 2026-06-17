"""Tests for Agent Graph support in the shared visualizer."""

from __future__ import annotations

import io
import json
import threading
import unittest
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

from weld.cli import main as cli_main
from weld.contract import SCHEMA_VERSION
from weld.viz.agent_api import AgentVizApi
from weld.viz.server import make_server

_TS = "2026-04-28T18:00:00+00:00"


def _agent_graph_payload() -> dict:
    nodes = {
        "agent:github-copilot:planner": {
            "type": "agent",
            "label": "planner",
            "props": {
                "description": "Plans implementation work.",
                "file": ".github/agents/planner.agent.md",
                "name": "planner",
                "platform": "github-copilot",
                "platform_name": "GitHub Copilot / VS Code",
                "source_strategy": "agent_graph_static",
            },
        },
        "platform:github-copilot": {
            "type": "platform",
            "label": "GitHub Copilot / VS Code",
            "props": {"platform": "github-copilot"},
        },
        "skill:claude:review": {
            "type": "skill",
            "label": "review",
            "props": {
                "description": "Reviews implementation plans.",
                "file": ".claude/skills/review/SKILL.md",
                "name": "review",
                "platform": "claude",
                "platform_name": "Claude Code",
                "source_strategy": "agent_graph_static",
            },
        },
    }
    edges = [
        {
            "from": "agent:github-copilot:planner",
            "to": "platform:github-copilot",
            "type": "part_of_platform",
            "props": {},
        },
        {
            "from": "agent:github-copilot:planner",
            "to": "skill:claude:review",
            "type": "uses_skill",
            "props": {},
        },
    ]
    return {
        "meta": {"version": SCHEMA_VERSION, "updated_at": _TS},
        "nodes": nodes,
        "edges": edges,
    }


def _write_agent_graph(root: Path) -> None:
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    (root / ".weld" / "agent-graph.json").write_text(
        json.dumps(_agent_graph_payload(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


class AgentVizApiTest(unittest.TestCase):
    def test_summary_reports_agent_graph_metadata(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_agent_graph(root)
            summary = AgentVizApi(root).summary()
        self.assertEqual(summary["graph_kind"], "agent")
        self.assertEqual(summary["title"], "Weld Agent Graph")
        self.assertEqual(summary["graph_path"], ".weld/agent-graph.json")
        self.assertEqual(summary["scopes"], ["root"])
        self.assertEqual(summary["counts"]["total_nodes"], 3)

    def test_agent_query_matches_core_asset_fields(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_agent_graph(root)
            api = AgentVizApi(root)
            for query in (
                "planner",
                "implementation work",
                "github-copilot",
                ".github/agents/planner.agent.md",
            ):
                payload = api.slice({"q": query, "max_nodes": 10, "max_edges": 10})
                ids = {node["data"]["id"] for node in payload["elements"]["nodes"]}
                self.assertIn("agent:github-copilot:planner", ids, query)

    def test_agent_context_and_path_are_normalized(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_agent_graph(root)
            api = AgentVizApi(root)
            context = api.context({"node_id": "agent:github-copilot:planner"})
            path = api.path({
                "from_id": "platform:github-copilot",
                "to_id": "skill:claude:review",
            })
        self.assertEqual(context["stats"]["visible_edges"], 2)
        self.assertEqual(
            path["path"],
            [
                "platform:github-copilot",
                "agent:github-copilot:planner",
                "skill:claude:review",
            ],
        )

    def test_agent_trace_is_unsupported(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_agent_graph(root)
            with self.assertRaisesRegex(ValueError, "not supported"):
                AgentVizApi(root).trace({"term": "planner"})


class AgentVizServerTest(unittest.TestCase):
    def test_http_agent_summary_uses_same_static_asset(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_agent_graph(root)
            server = make_server(str(root), host="127.0.0.1", port=0, graph_kind="agent")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            base = f"http://127.0.0.1:{server.server_address[1]}"
            summary = json.loads(urlopen(f"{base}/api/summary", timeout=5).read())
            self.assertEqual(summary["graph_kind"], "agent")
            html = urlopen(f"{base}/", timeout=5).read().decode("utf-8")
            self.assertIn("graph-title", html)

    def test_cli_agents_viz_help(self) -> None:
        stdout = io.StringIO()
        with patch("sys.stdout", stdout), self.assertRaises(SystemExit) as cm:
            cli_main(["agents", "viz", "--help"])
        self.assertEqual(cm.exception.code, 0)
        self.assertIn("wd agents viz", stdout.getvalue())
        self.assertIn("--no-open", stdout.getvalue())
        self.assertIn("--allow-remote", stdout.getvalue())

    def test_cli_agents_viz_requires_persisted_agent_graph(self) -> None:
        with TemporaryDirectory() as tmp:
            stderr = io.StringIO()
            with patch("sys.stderr", stderr):
                exit_code = cli_main(["agents", "viz", "--root", tmp, "--no-open"])
        self.assertEqual(exit_code, 2)
        self.assertIn("Run `wd agents discover`", stderr.getvalue())

    def test_cli_agents_viz_reuses_host_guard(self) -> None:
        stderr = io.StringIO()
        with patch("weld.viz.server.serve") as mock_serve, patch("sys.stderr", stderr):
            exit_code = cli_main(["agents", "viz", "--host", "0.0.0.0", "--no-open"])
        self.assertNotEqual(exit_code, 0)
        mock_serve.assert_not_called()
        self.assertIn("--allow-remote", stderr.getvalue())


class AgentVizExportDisabledTest(unittest.TestCase):
    """Export is code-graph only; agent viz must not silently emit empty.

    The ``/api/export`` route loads ``.weld/graph.json`` directly via
    :func:`weld.export.export`. Under ``wd agents viz`` the code graph
    may not exist and is irrelevant, so the route returns an explicit
    400 instead of an empty payload. The frontend hides the export
    menu when ``summary.graph_kind == "agent"`` so the user never
    sees a path that lies.
    """

    def _with_agent_server(self, root: Path) -> str:
        server = make_server(str(root), host="127.0.0.1", port=0, graph_kind="agent")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def test_export_returns_400_under_agent_mode(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_agent_graph(root)
            base = self._with_agent_server(root)
            with self.assertRaises(HTTPError) as cm:
                urlopen(f"{base}/api/export?format=mermaid", timeout=5)
        self.assertEqual(cm.exception.code, 400)
        payload = json.loads(cm.exception.read())
        # Error mentions agent so the caller knows why this surface is
        # off and where to look (no silent empty document).
        self.assertIn("error", payload)
        self.assertIn("agent", payload["error"].lower())

    def test_app_js_hides_export_menu_when_graph_kind_is_agent(self) -> None:
        js = files("weld.viz").joinpath("static", "app.js").read_text(encoding="utf-8")
        # The frontend must consult summary.graph_kind and toggle the
        # export-wrap visibility off. Both the kind sentinel and the
        # DOM hook must appear in the same module so the wiring is
        # discoverable from one search.
        self.assertIn("graph_kind", js)
        self.assertIn("export-wrap", js)


if __name__ == "__main__":
    unittest.main()
