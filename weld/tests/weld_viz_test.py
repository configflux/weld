"""Tests for the local read-only graph visualizer."""

from __future__ import annotations

import io
import json
import subprocess
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from weld.cli import main as cli_main
from weld.contract import SCHEMA_VERSION
from weld.federation_support import prefix_node_id
from weld.viz.adapter import neighborhood_from_data, normalize_graph_data
from weld.viz.api import VizApi
from weld.viz import server as viz_server
from weld.viz.server import make_server
from weld.workspace import ChildEntry, WorkspaceConfig, dump_workspaces_yaml

_TS = "2026-04-16T19:30:00+00:00"


def _graph_payload(nodes: dict, edges: list[dict] | None = None, schema_version: int = 1) -> dict:
    return {
        "meta": {"version": SCHEMA_VERSION, "updated_at": _TS, "schema_version": schema_version},
        "nodes": nodes,
        "edges": edges or [],
    }


def _write_graph(root: Path, payload: dict) -> None:
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    (root / ".weld" / "graph.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


def _init_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Weld Test")
    (root / "README.md").write_text("# fixture\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-q", "-m", "initial commit")


def _simple_root() -> TemporaryDirectory:
    tmp = TemporaryDirectory()
    root = Path(tmp.name)
    nodes = {
        "service:api": {"type": "service", "label": "api", "props": {"file": "src/api.py"}},
        "route:GET:/stores": {"type": "route", "label": "GET /stores", "props": {}},
        "entity:Store": {"type": "entity", "label": "Store", "props": {}},
        "symbol:helper": {"type": "symbol", "label": "helper", "props": {}},
    }
    edges = [
        {"from": "service:api", "to": "route:GET:/stores", "type": "exposes", "props": {}},
        {"from": "route:GET:/stores", "to": "entity:Store", "type": "responds_with", "props": {}},
        {"from": "symbol:helper", "to": "entity:Store", "type": "calls", "props": {}},
    ]
    _write_graph(root, _graph_payload(nodes, edges))
    return tmp


class VizAdapterTest(unittest.TestCase):
    def test_normalize_graph_data_enforces_caps(self) -> None:
        nodes = {f"file:{i}": {"type": "file", "label": str(i), "props": {}} for i in range(5)}
        payload = normalize_graph_data({"nodes": nodes, "edges": []}, max_nodes=2)
        self.assertEqual(len(payload["elements"]["nodes"]), 2)
        self.assertTrue(payload["truncated"]["nodes"])

    def test_normalize_graph_data_filters_node_and_edge_types(self) -> None:
        with _simple_root() as tmp:
            data = json.loads((Path(tmp) / ".weld" / "graph.json").read_text())
        payload = normalize_graph_data(
            data,
            node_types={"service", "route"},
            edge_types={"exposes"},
        )
        self.assertEqual({n["data"]["type"] for n in payload["elements"]["nodes"]}, {"service", "route"})
        self.assertEqual({e["data"]["type"] for e in payload["elements"]["edges"]}, {"exposes"})

    def test_neighborhood_extracts_depth(self) -> None:
        with _simple_root() as tmp:
            data = json.loads((Path(tmp) / ".weld" / "graph.json").read_text())
        result = neighborhood_from_data(data, "service:api", 2)
        neighbor_ids = {node["id"] for node in result["neighbors"]}
        self.assertIn("route:GET:/stores", neighbor_ids)
        self.assertIn("entity:Store", neighbor_ids)


class VizApiTest(unittest.TestCase):
    def test_summary_handles_missing_graph(self) -> None:
        with TemporaryDirectory() as tmp:
            summary = VizApi(tmp).summary()
        self.assertFalse(summary["graph_exists"])
        self.assertEqual(summary["counts"]["total_nodes"], 0)

    def test_query_slice_and_context_are_normalized(self) -> None:
        with _simple_root() as tmp:
            api = VizApi(tmp)
            query = api.slice({"q": "stores", "max_nodes": 10, "max_edges": 10})
            ids = {node["data"]["id"] for node in query["elements"]["nodes"]}
            self.assertIn("route:GET:/stores", ids)
            context = api.context({"node_id": "entity:Store", "max_nodes": 10})
            self.assertTrue(context["elements"]["edges"])

    def test_path_slice_contains_path_ids(self) -> None:
        with _simple_root() as tmp:
            payload = VizApi(tmp).path({
                "from_id": "service:api",
                "to_id": "entity:Store",
                "max_nodes": 10,
            })
        self.assertEqual(payload["path"], ["service:api", "route:GET:/stores", "entity:Store"])

    def test_federated_summary_and_scopes(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = root / "repo-a"
            _init_repo(child)
            _write_graph(child, _graph_payload({
                "file:src/a.py": {"type": "file", "label": "alpha", "props": {"file": "src/a.py"}},
            }))
            _write_graph(root, _graph_payload({
                "repo:repo-a": {"type": "repo", "label": "repo-a", "props": {"path": "repo-a"}},
                "repo:repo-missing": {"type": "repo", "label": "repo-missing", "props": {"path": "repo-missing"}},
            }, schema_version=2))
            config = WorkspaceConfig(children=[
                ChildEntry(name="repo-a", path="repo-a"),
                ChildEntry(name="repo-missing", path="repo-missing"),
            ], cross_repo_strategies=[])
            dump_workspaces_yaml(config, root / ".weld" / "workspaces.yaml")

            summary = VizApi(root).summary()
            self.assertEqual(summary["children_status"]["repo-a"]["status"], "present")
            self.assertEqual(summary["children_status"]["repo-missing"]["status"], "missing")
            self.assertIn("child:repo-a", summary["scopes"])

    def test_federated_child_scope_prefixes_nodes(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = root / "repo-a"
            _init_repo(child)
            _write_graph(child, _graph_payload({
                "file:src/a.py": {"type": "file", "label": "alpha", "props": {"file": "src/a.py"}},
            }))
            _write_graph(root, _graph_payload({
                "repo:repo-a": {"type": "repo", "label": "repo-a", "props": {"path": "repo-a"}},
            }, schema_version=2))
            dump_workspaces_yaml(
                WorkspaceConfig(children=[ChildEntry(name="repo-a", path="repo-a")], cross_repo_strategies=[]),
                root / ".weld" / "workspaces.yaml",
            )

            payload = VizApi(root).slice({"scope": "child:repo-a", "max_nodes": 10})
            ids = {node["data"]["id"] for node in payload["elements"]["nodes"]}
            self.assertIn(prefix_node_id("repo-a", "file:src/a.py"), ids)


class VizServerTest(unittest.TestCase):
    def _with_server(self, root: Path):
        server = make_server(str(root), host="127.0.0.1", port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def test_http_summary_and_static_asset(self) -> None:
        with _simple_root() as tmp:
            base = self._with_server(Path(tmp))
            summary = json.loads(urlopen(f"{base}/api/summary", timeout=5).read())
            self.assertEqual(summary["counts"]["total_nodes"], 4)
            html = urlopen(f"{base}/", timeout=5).read().decode("utf-8")
            self.assertIn("Weld Graph", html)

    def test_http_rejects_mutation_and_path_traversal(self) -> None:
        with _simple_root() as tmp:
            base = self._with_server(Path(tmp))
            with self.assertRaises(HTTPError) as post_err:
                urlopen(Request(f"{base}/api/summary", method="POST"), timeout=5)
            self.assertEqual(post_err.exception.code, 405)
            with self.assertRaises(HTTPError) as traversal_err:
                urlopen(f"{base}/../server.py", timeout=5)
            self.assertEqual(traversal_err.exception.code, 400)

    def test_cli_viz_help(self) -> None:
        stdout = io.StringIO()
        with patch("sys.stdout", stdout), self.assertRaises(SystemExit) as cm:
            cli_main(["viz", "--help"])
        self.assertEqual(cm.exception.code, 0)
        self.assertIn("--no-open", stdout.getvalue())
        self.assertIn("--allow-remote", stdout.getvalue())


class VizHostGuardTest(unittest.TestCase):
    def test_loopback_helper_accepts_loopback_hosts(self) -> None:
        for host in ("127.0.0.1", "127.0.0.5", "::1", "localhost"):
            self.assertTrue(
                viz_server._is_loopback_host(host),
                f"expected loopback for {host!r}",
            )

    def test_loopback_helper_rejects_public_hosts(self) -> None:
        for host in ("0.0.0.0", "192.168.1.1", "8.8.8.8", "::", "2001:db8::1"):
            self.assertFalse(
                viz_server._is_loopback_host(host),
                f"expected non-loopback for {host!r}",
            )

    def test_loopback_helper_rejects_empty_and_unparseable(self) -> None:
        for host in ("", "not a host", "example.com"):
            self.assertFalse(
                viz_server._is_loopback_host(host),
                f"expected non-loopback for {host!r}",
            )

    def test_main_refuses_non_loopback_without_allow_remote(self) -> None:
        stderr = io.StringIO()
        with patch("weld.viz.server.serve") as mock_serve, patch("sys.stderr", stderr):
            exit_code = viz_server.main(["--host", "0.0.0.0", "--no-open"])
        self.assertNotEqual(exit_code, 0)
        mock_serve.assert_not_called()
        self.assertIn("--allow-remote", stderr.getvalue())

    def test_main_accepts_loopback_default(self) -> None:
        with patch("weld.viz.server.serve", return_value=0) as mock_serve:
            exit_code = viz_server.main(["--host", "127.0.0.1", "--no-open"])
        self.assertEqual(exit_code, 0)
        mock_serve.assert_called_once()
        _, kwargs = mock_serve.call_args
        self.assertEqual(kwargs["host"], "127.0.0.1")

    def test_main_accepts_non_loopback_with_allow_remote(self) -> None:
        with patch("weld.viz.server.serve", return_value=0) as mock_serve:
            exit_code = viz_server.main(
                ["--host", "0.0.0.0", "--allow-remote", "--no-open"]
            )
        self.assertEqual(exit_code, 0)
        mock_serve.assert_called_once()
        _, kwargs = mock_serve.call_args
        self.assertEqual(kwargs["host"], "0.0.0.0")


class VizSummaryPathDisclosureTest(unittest.TestCase):
    def test_summary_returns_relative_paths_missing_graph(self) -> None:
        with TemporaryDirectory() as tmp:
            summary = VizApi(tmp).summary()
        # root should be the repo-root placeholder, not the absolute path.
        self.assertEqual(summary["root"], ".")
        # graph_path should be posix-relative, never absolute.
        self.assertEqual(summary["graph_path"], ".weld/graph.json")

    def test_summary_returns_relative_paths_with_graph(self) -> None:
        with _simple_root() as tmp:
            summary = VizApi(tmp).summary()
        self.assertEqual(summary["root"], ".")
        self.assertEqual(summary["graph_path"], ".weld/graph.json")
        # Sanity: nothing in summary should start with "/" (unix absolute)
        # or look like "C:\..." (windows absolute).
        for key in ("root", "graph_path"):
            value = summary[key]
            self.assertIsInstance(value, str)
            self.assertFalse(
                value.startswith("/"),
                f"summary[{key!r}]={value!r} is absolute",
            )
            self.assertFalse(
                len(value) >= 2 and value[1] == ":",
                f"summary[{key!r}]={value!r} looks like a windows absolute path",
            )


if __name__ == "__main__":
    unittest.main()
