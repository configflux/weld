"""Tests for MCP federation dispatch in :mod:`weld.mcp_server`.

Pins the behavior of ``weld.mcp_server._load_graph`` and the federated
tool handlers:

* With ``workspaces.yaml`` present, ``_load_graph`` returns a
  :class:`weld.federation.FederatedGraph`; otherwise a plain
  :class:`weld.graph.Graph`.
* Federated dispatch transparently fans out across children for
  ``weld_query``/``weld_context``/``weld_path``/``weld_brief``/
  ``weld_stale``/``weld_callers``/``weld_references``.
* Federated responses carry a ``children_status`` map surfacing per-child
  sentinel state (``present``/``missing``/``uninitialized``/``corrupt``) so
  agents see which repos are indexed vs degraded. A corrupt child must not
  block the server from serving the remaining children.
"""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from weld import mcp_server
from weld.contract import SCHEMA_VERSION
from weld.federation import FederatedGraph
from weld.graph import Graph
from weld.workspace import (
    UNIT_SEPARATOR, ChildEntry, WorkspaceConfig, dump_workspaces_yaml,
)

_TS = "2026-04-15T21:00:00+00:00"


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _git(repo_root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(repo_root),
        capture_output=True, text=True, check=True)
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


def _graph_payload(
    nodes: dict, edges: list[dict] | None = None, *, schema_version: int = 1,
) -> dict:
    return {
        "meta": {"version": SCHEMA_VERSION, "updated_at": _TS,
                 "schema_version": schema_version},
        "nodes": nodes, "edges": edges or [],
    }


def _write_graph(repo_root: Path, payload: dict) -> None:
    weld_dir = repo_root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    (weld_dir / "graph.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_workspaces(root: Path, children: list[ChildEntry]) -> None:
    config = WorkspaceConfig(children=children, cross_repo_strategies=[])
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    dump_workspaces_yaml(config, root / ".weld" / "workspaces.yaml")


def _write_root_graph(
    root: Path, children: list[str], edges: list[dict] | None = None,
) -> None:
    nodes = {f"repo:{name}": {"type": "repo", "label": name,
                              "props": {"path": name}} for name in children}
    _write_graph(root, _graph_payload(nodes, edges, schema_version=2))


# ---------------------------------------------------------------------------
# Unit test: _load_graph dispatch
# ---------------------------------------------------------------------------

class LoadGraphDispatchTest(unittest.TestCase):
    """``_load_graph`` returns the right type based on workspace registry."""

    def test_returns_plain_graph_when_registry_absent(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_graph(
                root,
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
            graph = mcp_server._load_graph(root)
            self.assertIsInstance(graph, Graph)
            self.assertNotIsInstance(graph, FederatedGraph)

    def test_returns_federated_graph_when_registry_present(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            child_a = _init_repo(root / "repo-a")
            _write_graph(
                child_a,
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
            _write_root_graph(root, ["repo-a"])
            _write_workspaces(
                root,
                [ChildEntry(name="repo-a", path="repo-a")],
            )

            graph = mcp_server._load_graph(root)
            self.assertIsInstance(graph, FederatedGraph)

    def test_registry_in_weld_subdirectory_is_honored(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            child_a = _init_repo(root / "repo-a")
            _write_graph(
                child_a,
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
            _write_root_graph(root, ["repo-a"])
            # The canonical location is .weld/workspaces.yaml -- ensure the
            # loader finds it there too.
            _write_workspaces(
                root,
                [ChildEntry(name="repo-a", path="repo-a")],
            )
            self.assertTrue((root / ".weld" / "workspaces.yaml").is_file())

            graph = mcp_server._load_graph(root)
            self.assertIsInstance(graph, FederatedGraph)


# ---------------------------------------------------------------------------
# End-to-end: MCP dispatch crosses a federated boundary
# ---------------------------------------------------------------------------

def _build_minimal_federated_workspace(root: Path) -> None:
    """Build a two-child federated workspace with unique node labels.

    - ``services-api`` holds a ``Store`` entity node.
    - ``services-auth`` holds a ``Store`` entity node with a distinct file.
    - Root graph registers both child repos (schema_version=2).
    - ``workspaces.yaml`` lives under ``.weld/`` at the root.
    """
    child_api = _init_repo(root / "services-api")
    child_auth = _init_repo(root / "services-auth")

    _write_graph(
        child_api,
        _graph_payload(
            {
                "entity:Store": {
                    "type": "entity",
                    "label": "Store",
                    "props": {
                        "file": "src/services-api/store.py",
                        "exports": ["Store"],
                        "description": "api-side Store model.",
                    },
                },
            }
        ),
    )
    _write_graph(
        child_auth,
        _graph_payload(
            {
                "entity:Store": {
                    "type": "entity",
                    "label": "Store",
                    "props": {
                        "file": "src/services-auth/store.py",
                        "exports": ["Store"],
                        "description": "auth-side Store model.",
                    },
                },
            }
        ),
    )
    _write_root_graph(root, ["services-api", "services-auth"])
    _write_workspaces(
        root,
        [
            ChildEntry(name="services-api", path="services-api"),
            ChildEntry(name="services-auth", path="services-auth"),
        ],
    )


class McpDispatchCrossesFederatedBoundaryTest(unittest.TestCase):
    """End-to-end: dispatch a tool via the MCP registry and verify fan-out."""

    def test_weld_query_dispatch_returns_matches_from_every_child(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _build_minimal_federated_workspace(root)

            result = mcp_server.dispatch(
                "weld_query",
                {"term": "Store", "limit": 20},
                root=root,
            )

            self.assertEqual(result["query"], "Store")
            match_ids = {match["id"] for match in result["matches"]}

            api_id = f"services-api{UNIT_SEPARATOR}entity:Store"
            auth_id = f"services-auth{UNIT_SEPARATOR}entity:Store"
            self.assertIn(api_id, match_ids)
            self.assertIn(auth_id, match_ids)

            # Every prefixed match carries a display id using the double-colon
            # cosmetic form for human readers.
            by_id = {match["id"]: match for match in result["matches"]}
            self.assertEqual(
                by_id[api_id].get("display_id"),
                "services-api::entity:Store",
            )
            self.assertEqual(
                by_id[auth_id].get("display_id"),
                "services-auth::entity:Store",
            )

    def test_weld_query_dispatch_on_single_repo_is_unchanged(self) -> None:
        """Without workspaces.yaml, dispatch must match pre-federation output."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_graph(
                root,
                _graph_payload(
                    {
                        "entity:Store": {
                            "type": "entity",
                            "label": "Store",
                            "props": {
                                "file": "src/store.py",
                                "exports": ["Store"],
                                "description": "Single-repo Store.",
                            },
                        },
                    }
                ),
            )

            result = mcp_server.dispatch(
                "weld_query",
                {"term": "Store", "limit": 20},
                root=root,
            )

            match_ids = {match["id"] for match in result["matches"]}
            self.assertIn("entity:Store", match_ids)
            # No prefixed IDs should appear in single-repo mode.
            self.assertTrue(
                all(UNIT_SEPARATOR not in mid for mid in match_ids),
                f"unexpected prefixed id in single-repo mode: {match_ids}",
            )
            # Single-repo responses must not carry a children_status field.
            self.assertNotIn("children_status", result)


# ---------------------------------------------------------------------------
# Sentinel-state surfacing through the MCP tool surface
# ---------------------------------------------------------------------------

_PRESENT_ID = f"repo-present{UNIT_SEPARATOR}entity:Store"
_NON_PRESENT = ("repo-corrupt", "repo-uninitialized", "repo-missing")


def _build_all_sentinel_workspace(root: Path) -> None:
    """Build a federated workspace exercising all four sentinel states."""
    present = _init_repo(root / "repo-present")
    _write_graph(present, _graph_payload({"entity:Store": {
        "type": "entity", "label": "Store", "props": {
            "file": "src/store.py", "exports": ["Store"],
            "description": "Present Store."}}}))
    _init_repo(root / "repo-uninitialized")  # no .weld/graph.json
    corrupt = _init_repo(root / "repo-corrupt")
    (corrupt / ".weld").mkdir(parents=True, exist_ok=True)
    (corrupt / ".weld" / "graph.json").write_text("{bad json\n", encoding="utf-8")
    # repo-missing is registered but never created on disk.
    names = ["repo-present", "repo-uninitialized", "repo-missing", "repo-corrupt"]
    _write_root_graph(root, names)
    _write_workspaces(root, [ChildEntry(name=n, path=n) for n in names])


class McpDispatchSurfacesSentinelStatusTest(unittest.TestCase):
    """``weld_query``/``weld_context``/``weld_path`` dispatch must surface the
    per-child sentinel state (present/missing/uninitialized/corrupt) via a
    structured ``children_status`` field."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        _build_all_sentinel_workspace(self.root)

    def _assert_all_four_sentinels(self, status: dict) -> None:
        self.assertEqual(set(status.keys()), {
            "repo-present", "repo-uninitialized", "repo-missing", "repo-corrupt"})
        self.assertEqual(status["repo-present"]["status"], "present")
        self.assertEqual(status["repo-uninitialized"]["status"], "uninitialized")
        self.assertEqual(status["repo-missing"]["status"], "missing")
        self.assertEqual(status["repo-corrupt"]["status"], "corrupt")
        # Only the corrupt entry carries an error message; others omit it.
        self.assertTrue(status["repo-corrupt"]["error"])
        for name in ("repo-present", "repo-uninitialized", "repo-missing"):
            self.assertNotIn("error", status[name])
        # Every entry carries the relative child graph path for display.
        for entry in status.values():
            self.assertTrue(entry["graph_path"].endswith("graph.json"), entry)

    def test_weld_query_dispatch_attaches_children_status(self) -> None:
        result = mcp_server.dispatch(
            "weld_query", {"term": "Store", "limit": 20}, root=self.root)
        match_ids = {m["id"] for m in result["matches"]}
        # Present child serves results even though a sibling is corrupt.
        self.assertIn(_PRESENT_ID, match_ids)
        for mid in match_ids:
            for bad in _NON_PRESENT:
                self.assertFalse(
                    mid.startswith(f"{bad}{UNIT_SEPARATOR}"),
                    f"query returned match from non-present child {bad!r}: {mid}")
        self.assertIn("children_status", result)
        self._assert_all_four_sentinels(result["children_status"])

    def test_weld_context_dispatch_attaches_children_status(self) -> None:
        result = mcp_server.dispatch(
            "weld_context", {"node_id": _PRESENT_ID}, root=self.root)
        self.assertEqual(result["node"]["id"], _PRESENT_ID)
        self.assertIn("children_status", result)
        self._assert_all_four_sentinels(result["children_status"])

    def test_weld_context_dispatch_attaches_status_even_on_node_not_found(
        self,
    ) -> None:
        """Even an error response must carry ``children_status`` so agents
        see which child might have held the missing node."""
        result = mcp_server.dispatch(
            "weld_context",
            {"node_id": f"repo-missing{UNIT_SEPARATOR}entity:Ghost"},
            root=self.root,
        )
        self.assertIn("error", result)
        self.assertEqual(
            result["children_status"]["repo-missing"]["status"], "missing")

    def test_weld_path_dispatch_attaches_children_status(self) -> None:
        result = mcp_server.dispatch(
            "weld_path",
            {"from_id": _PRESENT_ID, "to_id": _PRESENT_ID},
            root=self.root,
        )
        self.assertIn("children_status", result)
        self._assert_all_four_sentinels(result["children_status"])


if __name__ == "__main__":
    unittest.main()
