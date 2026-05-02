"""Tests for the federated workspace query/context/path wrapper."""

from __future__ import annotations

import io
import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from weld.contract import SCHEMA_VERSION
from weld.federation import FederatedGraph, prefix_node_id
from weld.graph import Graph, main as graph_main
from weld.workspace import ChildEntry, WorkspaceConfig, dump_workspaces_yaml

_TS = "2026-04-15T20:30:00+00:00"


def _git(repo_root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _init_repo(repo_root: Path) -> Path:
    repo_root.mkdir(parents=True, exist_ok=True)
    _git(repo_root, "init", "-q")
    _git(repo_root, "config", "user.email", "test@example.com")
    _git(repo_root, "config", "user.name", "Weld Test")
    (repo_root / "README.md").write_text("# fixture\n", encoding="utf-8")
    _git(repo_root, "add", "README.md")
    _git(repo_root, "commit", "-q", "-m", "initial commit")
    return repo_root


def _graph_payload(nodes: dict, edges: list[dict] | None = None, *, schema_version: int = 1) -> dict:
    return {
        "meta": {
            "version": SCHEMA_VERSION,
            "updated_at": _TS,
            "schema_version": schema_version,
        },
        "nodes": nodes,
        "edges": edges or [],
    }


def _write_graph(repo_root: Path, payload: dict) -> None:
    weld_dir = repo_root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    (weld_dir / "graph.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_workspaces(root: Path, children: list[ChildEntry]) -> None:
    config = WorkspaceConfig(children=children, cross_repo_strategies=[])
    dump_workspaces_yaml(config, root / ".weld" / "workspaces.yaml")


def _write_root_graph(root: Path, children: list[str], edges: list[dict] | None = None) -> None:
    nodes = {
        f"repo:{name}": {
            "type": "repo",
            "label": name,
            "props": {"path": name},
        }
        for name in children
    }
    _write_graph(root, _graph_payload(nodes, edges, schema_version=2))


def _run_graph_cli(root: Path, *args: str) -> dict:
    """Drive the CLI with ``--json`` and parse the envelope.

    Per ADR 0040 the default output is human text; tests inspect the
    structured envelope by passing ``--json``. The flag is appended so
    callers can keep their existing positional + flag layout intact.
    """
    stdout = io.StringIO()
    with patch("sys.stdout", stdout):
        graph_main(["--root", str(root), *args, "--json"])
    return json.loads(stdout.getvalue())


class FederatedGraphStatusTest(unittest.TestCase):
    def test_children_status_reports_all_sentinel_states(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            present = _init_repo(root / "repo-a")
            _init_repo(root / "repo-b")
            corrupt = _init_repo(root / "repo-d")
            _write_graph(
                present,
                _graph_payload(
                    {
                        "file:src/a.py": {
                            "type": "file",
                            "label": "alpha",
                            "props": {"file": "src/a.py"},
                        },
                    }
                ),
            )
            (corrupt / ".weld").mkdir(parents=True, exist_ok=True)
            (corrupt / ".weld" / "graph.json").write_text("{bad json\n", encoding="utf-8")
            _write_workspaces(
                root,
                [
                    ChildEntry(name="repo-a", path="repo-a"),
                    ChildEntry(name="repo-b", path="repo-b"),
                    ChildEntry(name="repo-c", path="repo-c"),
                    ChildEntry(name="repo-d", path="repo-d"),
                ],
            )
            _write_root_graph(root, ["repo-a", "repo-b", "repo-c", "repo-d"])

            graph = FederatedGraph(root)
            status = graph.children_status()

            self.assertEqual(status["repo-a"]["status"], "present")
            self.assertEqual(status["repo-b"]["status"], "uninitialized")
            self.assertEqual(status["repo-c"]["status"], "missing")
            self.assertEqual(status["repo-d"]["status"], "corrupt")
            self.assertIn("JSONDecodeError", str(status["repo-d"]["error"]))


class FederatedGraphCliTest(unittest.TestCase):
    def _make_workspace(self) -> Path:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)

        repo_a = _init_repo(root / "repo-a")
        repo_b = _init_repo(root / "repo-b")
        repo_c = _init_repo(root / "repo-c")
        _init_repo(root / "repo-uninitialized")
        repo_corrupt = _init_repo(root / "repo-corrupt")

        _write_graph(
            repo_a,
            _graph_payload(
                {
                    "file:src/a-start.py": {
                        "type": "file",
                        "label": "alpha start",
                        "props": {"file": "src/a-start.py", "description": "alpha start"},
                    },
                    "file:src/a-bridge.py": {
                        "type": "file",
                        "label": "alpha bridge",
                        "props": {"file": "src/a-bridge.py", "description": "alpha bridge"},
                    },
                    "symbol:alpha": {
                        "type": "symbol",
                        "label": "alpha",
                        "props": {"qualname": "alpha"},
                    },
                },
                [
                    {"from": "file:src/a-start.py", "to": "file:src/a-bridge.py", "type": "depends_on", "props": {}},
                    {"from": "file:src/a-start.py", "to": "symbol:alpha", "type": "contains", "props": {}},
                ],
            ),
        )
        _write_graph(
            repo_b,
            _graph_payload(
                {
                    "file:src/b-mid.py": {
                        "type": "file",
                        "label": "beta mid",
                        "props": {"file": "src/b-mid.py", "description": "alpha beta mid"},
                    },
                    "file:src/b-target.py": {
                        "type": "file",
                        "label": "beta target",
                        "props": {"file": "src/b-target.py"},
                    },
                },
                [
                    {"from": "file:src/b-mid.py", "to": "file:src/b-target.py", "type": "depends_on", "props": {}},
                ],
            ),
        )
        _write_graph(
            repo_c,
            _graph_payload(
                {
                    "file:src/c-mid.py": {
                        "type": "file",
                        "label": "gamma mid",
                        "props": {"file": "src/c-mid.py"},
                    },
                }
            ),
        )
        (repo_corrupt / ".weld").mkdir(parents=True, exist_ok=True)
        (repo_corrupt / ".weld" / "graph.json").write_text("{oops\n", encoding="utf-8")

        _write_workspaces(
            root,
            [
                ChildEntry(name="repo-a", path="repo-a"),
                ChildEntry(name="repo-b", path="repo-b"),
                ChildEntry(name="repo-c", path="repo-c"),
                ChildEntry(name="repo-missing", path="repo-missing"),
                ChildEntry(name="repo-uninitialized", path="repo-uninitialized"),
                ChildEntry(name="repo-corrupt", path="repo-corrupt"),
            ],
        )
        _write_root_graph(
            root,
            [
                "repo-a",
                "repo-b",
                "repo-c",
                "repo-missing",
                "repo-uninitialized",
                "repo-corrupt",
            ],
            [
                {
                    "from": prefix_node_id("repo-a", "file:src/a-bridge.py"),
                    "to": prefix_node_id("repo-b", "file:src/b-mid.py"),
                    "type": "depends_on",
                    "props": {},
                },
                {
                    "from": prefix_node_id("repo-b", "file:src/b-target.py"),
                    "to": prefix_node_id("repo-c", "file:src/c-mid.py"),
                    "type": "depends_on",
                    "props": {},
                },
            ],
        )
        return root

    def test_query_prefixes_ids_and_exposes_display_form(self) -> None:
        root = self._make_workspace()

        payload = _run_graph_cli(root, "query", "alpha", "--limit", "5")

        bridge_id = prefix_node_id("repo-a", "file:src/a-bridge.py")
        matches_by_id = {match["id"]: match for match in payload["matches"]}
        self.assertIn(bridge_id, matches_by_id)
        self.assertEqual(matches_by_id[bridge_id]["display_id"], "repo-a::file:src/a-bridge.py")
        self.assertTrue(all("display_id" in match for match in payload["matches"]))

    def test_context_accepts_display_form_and_returns_cross_repo_neighbors(self) -> None:
        root = self._make_workspace()

        payload = _run_graph_cli(root, "context", "repo-a::file:src/a-bridge.py")
        neighbor_ids = {neighbor["id"] for neighbor in payload["neighbors"]}

        self.assertEqual(payload["node"]["id"], prefix_node_id("repo-a", "file:src/a-bridge.py"))
        self.assertIn(prefix_node_id("repo-a", "file:src/a-start.py"), neighbor_ids)
        self.assertIn(prefix_node_id("repo-b", "file:src/b-mid.py"), neighbor_ids)
        cross_edge = next(edge for edge in payload["edges"] if edge["to"] == prefix_node_id("repo-b", "file:src/b-mid.py"))
        self.assertEqual(cross_edge["from_display"], "repo-a::file:src/a-bridge.py")
        self.assertEqual(cross_edge["to_display"], "repo-b::file:src/b-mid.py")

    def test_context_free_form_string_surfaces_resolved_from_envelope(self) -> None:
        """tracked issue follow-up: exercise the CLI end-to-end for the free-form
        query fallback. A term that is not an exact node id must resolve
        through query() and return the matched node's context plus a
        ``resolved_from`` envelope identifying the query, matched id, and
        score. Unit-level coverage lives in weld_context_fallback_test.py;
        this guards the CLI seam end-to-end."""
        root = self._make_workspace()

        payload = _run_graph_cli(root, "context", "bridge")

        self.assertNotIn("error", payload)
        self.assertIn("resolved_from", payload)
        resolved = payload["resolved_from"]
        self.assertEqual(resolved["query"], "bridge")
        self.assertEqual(
            resolved["matched_id"],
            prefix_node_id("repo-a", "file:src/a-bridge.py"),
        )
        self.assertIn("score", resolved)
        # The envelope rides alongside a fully-resolved context payload, so
        # the node + neighbors from the matched id must be present.
        self.assertEqual(
            payload["node"]["id"],
            prefix_node_id("repo-a", "file:src/a-bridge.py"),
        )
        neighbor_ids = {neighbor["id"] for neighbor in payload["neighbors"]}
        self.assertIn(prefix_node_id("repo-a", "file:src/a-start.py"), neighbor_ids)

    def test_path_matrix(self) -> None:
        root = self._make_workspace()

        same_child = _run_graph_cli(
            root,
            "path",
            "repo-a::file:src/a-start.py",
            "repo-a::file:src/a-bridge.py",
        )
        self.assertEqual(
            [node["display_id"] for node in same_child["path"]],
            ["repo-a::file:src/a-start.py", "repo-a::file:src/a-bridge.py"],
        )

        direct_cross = _run_graph_cli(
            root,
            "path",
            "repo-a::file:src/a-bridge.py",
            "repo-b::file:src/b-mid.py",
        )
        self.assertEqual(
            [node["display_id"] for node in direct_cross["path"]],
            ["repo-a::file:src/a-bridge.py", "repo-b::file:src/b-mid.py"],
        )

        via_third = _run_graph_cli(
            root,
            "path",
            "repo-a::file:src/a-start.py",
            "repo-c::file:src/c-mid.py",
        )
        via_third_ids = [node["display_id"] for node in via_third["path"]]
        self.assertEqual(
            via_third_ids,
            [
                "repo-a::file:src/a-start.py",
                "repo-a::file:src/a-bridge.py",
                "repo-b::file:src/b-mid.py",
                "repo-b::file:src/b-target.py",
                "repo-c::file:src/c-mid.py",
            ],
        )
        self.assertEqual(len(via_third_ids), len(set(via_third_ids)))

        for endpoint in (
            "repo-missing::file:src/ghost.py",
            "repo-uninitialized::file:src/ghost.py",
            "repo-corrupt::file:src/ghost.py",
        ):
            with self.subTest(endpoint=endpoint):
                missing = _run_graph_cli(root, "path", "repo-a::file:src/a-start.py", endpoint)
                self.assertIsNone(missing["path"])
                self.assertEqual(missing["reason"], "node not found")


class FederatedGraphLoadTest(unittest.TestCase):
    def test_load_child_warns_when_graph_bytes_change_during_recheck(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo_a = _init_repo(root / "repo-a")
            _write_graph(
                repo_a,
                _graph_payload(
                    {
                        "file:src/a.py": {
                            "type": "file",
                            "label": "alpha",
                            "props": {"file": "src/a.py"},
                        },
                    }
                ),
            )
            _write_workspaces(root, [ChildEntry(name="repo-a", path="repo-a")])
            _write_root_graph(root, ["repo-a"])

            graph = FederatedGraph(root)
            first = (repo_a / ".weld" / "graph.json").read_bytes()
            second = first.replace(b'"label": "alpha"', b'"label": "beta" ')
            stderr = io.StringIO()
            with patch.object(graph, "_read_graph_bytes", side_effect=[first, second]):
                with patch("sys.stderr", stderr):
                    loaded = graph._load_child("repo-a")

            self.assertIsInstance(loaded, Graph)
            self.assertIn("changed during load", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
