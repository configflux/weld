"""Tests for federated MCP tool extensions (brief/stale/callers/references).

Pins the behavior of the four MCP tool handlers that were extended to
work across federated children via :mod:`weld.federation_tools`:

* ``weld_brief`` -- includes child matches via ``FederatedGraph.query``.
* ``weld_stale`` -- reports per-child staleness or graceful degradation.
* ``weld_callers`` -- resolves prefixed symbol IDs within children.
* ``weld_references`` -- fans out bare-name search across all children.

Each handler must also work unchanged in a single-repo (non-federated)
workspace.
"""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from weld import mcp_server
from weld.contract import SCHEMA_VERSION
from weld.workspace import (
    UNIT_SEPARATOR, ChildEntry, WorkspaceConfig, dump_workspaces_yaml,
)

_TS = "2026-04-15T21:00:00+00:00"


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
    nodes: dict, edges: list[dict] | None = None, *, sv: int = 1,
) -> dict:
    return {
        "meta": {"version": SCHEMA_VERSION, "updated_at": _TS,
                 "schema_version": sv},
        "nodes": nodes, "edges": edges or [],
    }


def _write_graph(repo_root: Path, payload: dict) -> None:
    weld_dir = repo_root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    (weld_dir / "graph.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")


def _write_workspaces(root: Path, children: list[ChildEntry]) -> None:
    config = WorkspaceConfig(children=children, cross_repo_strategies=[])
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    dump_workspaces_yaml(config, root / ".weld" / "workspaces.yaml")


def _write_root_graph(
    root: Path, children: list[str], edges: list[dict] | None = None,
) -> None:
    nodes = {f"repo:{n}": {"type": "repo", "label": n,
                           "props": {"path": n}} for n in children}
    _write_graph(root, _graph_payload(nodes, edges, sv=2))


# -- Fixtures ---------------------------------------------------------------

def _build_store_workspace(root: Path) -> None:
    """Two-child workspace with Store entities (brief tests)."""
    for name in ("svc-api", "svc-auth"):
        child = _init_repo(root / name)
        _write_graph(child, _graph_payload({
            "entity:Store": {
                "type": "entity", "label": "Store",
                "props": {"file": f"src/{name}/store.py",
                           "description": f"{name} Store."}}}))
    _write_root_graph(root, ["svc-api", "svc-auth"])
    _write_workspaces(root, [
        ChildEntry(name="svc-api", path="svc-api"),
        ChildEntry(name="svc-auth", path="svc-auth")])


def _build_symbol_workspace(root: Path) -> None:
    """Two-child workspace with symbols and calls edges."""
    core = _init_repo(root / "lib-core")
    auth = _init_repo(root / "lib-auth")
    _write_graph(core, _graph_payload({
        "symbol:py:lib_core:load_config": {
            "type": "symbol", "label": "load_config",
            "props": {"file": "src/config.py",
                       "qualname": "lib_core.load_config"}},
        "symbol:py:lib_core:validate": {
            "type": "symbol", "label": "validate",
            "props": {"file": "src/validate.py",
                       "qualname": "lib_core.validate"}},
    }, edges=[{"from": "symbol:py:lib_core:load_config",
               "to": "symbol:py:lib_core:validate", "type": "calls"}]))
    _write_graph(auth, _graph_payload({
        "symbol:py:lib_auth:authenticate": {
            "type": "symbol", "label": "authenticate",
            "props": {"file": "src/auth.py",
                       "qualname": "lib_auth.authenticate"}},
        "symbol:unresolved:validate": {
            "type": "symbol", "label": "validate",
            "props": {"qualname": "validate"}},
    }, edges=[{"from": "symbol:py:lib_auth:authenticate",
               "to": "symbol:unresolved:validate", "type": "calls"}]))
    _write_root_graph(root, ["lib-core", "lib-auth"])
    _write_workspaces(root, [
        ChildEntry(name="lib-core", path="lib-core"),
        ChildEntry(name="lib-auth", path="lib-auth")])


def _build_sentinel_workspace(root: Path) -> None:
    """Workspace with all four child sentinel states."""
    present = _init_repo(root / "repo-present")
    _write_graph(present, _graph_payload({"entity:Store": {
        "type": "entity", "label": "Store",
        "props": {"file": "s.py", "description": "Present Store."}}}))
    _init_repo(root / "repo-uninitialized")  # no graph
    corrupt = _init_repo(root / "repo-corrupt")
    (corrupt / ".weld").mkdir(parents=True, exist_ok=True)
    (corrupt / ".weld" / "graph.json").write_text("{bad\n", encoding="utf-8")
    names = ["repo-present", "repo-uninitialized",
             "repo-missing", "repo-corrupt"]
    _write_root_graph(root, names)
    _write_workspaces(root, [ChildEntry(name=n, path=n) for n in names])


# -- Brief -------------------------------------------------------------------

class McpBriefFederationTest(unittest.TestCase):

    def test_brief_returns_matches_from_children(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _build_store_workspace(root)
            result = mcp_server.dispatch(
                "weld_brief", {"area": "Store", "limit": 20}, root=root)
            self.assertEqual(result["brief_version"], 2)
            all_ids = set()
            for b in ("primary", "interfaces", "docs", "build",
                      "boundaries"):
                all_ids.update(n["id"] for n in result[b])
            self.assertIn(f"svc-api{UNIT_SEPARATOR}entity:Store", all_ids)
            self.assertIn(f"svc-auth{UNIT_SEPARATOR}entity:Store", all_ids)

    def test_brief_single_repo_unchanged(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_graph(root, _graph_payload({"entity:Store": {
                "type": "entity", "label": "Store",
                "props": {"file": "s.py"}}}))
            result = mcp_server.dispatch(
                "weld_brief", {"area": "Store", "limit": 20}, root=root)
            all_ids = set()
            for b in ("primary", "interfaces", "docs", "build",
                      "boundaries"):
                all_ids.update(n["id"] for n in result[b])
            self.assertIn("entity:Store", all_ids)
            self.assertTrue(
                all(UNIT_SEPARATOR not in i for i in all_ids))


# -- Stale -------------------------------------------------------------------

class McpStaleFederationTest(unittest.TestCase):

    def test_stale_federated_reports_children(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            _build_symbol_workspace(root)
            (root / "m.txt").write_text("x\n", encoding="utf-8")
            _git(root, "add", "m.txt")
            _git(root, "commit", "-q", "-m", "m")
            result = mcp_server.dispatch("weld_stale", {}, root=root)
            self.assertIn("stale", result)
            self.assertIn("children", result)
            for name in ("lib-core", "lib-auth"):
                self.assertIn(name, result["children"])
                self.assertIn("stale", result["children"][name])

    def test_stale_single_repo_unchanged(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            _write_graph(root, _graph_payload({"entity:Foo": {
                "type": "entity", "label": "Foo",
                "props": {"file": "f.py"}}}))
            (root / "m.txt").write_text("x\n", encoding="utf-8")
            _git(root, "add", "m.txt")
            _git(root, "commit", "-q", "-m", "m")
            result = mcp_server.dispatch("weld_stale", {}, root=root)
            self.assertIn("stale", result)
            self.assertNotIn("children", result)

    def test_stale_federated_missing_child_degrades(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            _build_sentinel_workspace(root)
            result = mcp_server.dispatch("weld_stale", {}, root=root)
            ch = result["children"]
            self.assertEqual(ch["repo-missing"]["status"], "missing")
            self.assertEqual(ch["repo-uninitialized"]["status"],
                             "uninitialized")
            self.assertEqual(ch["repo-corrupt"]["status"], "corrupt")
            self.assertIn("stale", ch["repo-present"])


# -- Callers -----------------------------------------------------------------

class McpCallersFederationTest(unittest.TestCase):

    def test_callers_federated_finds_child_callers(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _build_symbol_workspace(root)
            target = f"lib-core{UNIT_SEPARATOR}symbol:py:lib_core:validate"
            result = mcp_server.dispatch(
                "weld_callers", {"symbol_id": target, "depth": 1},
                root=root)
            caller_ids = {c["id"] for c in result["callers"]}
            expected = (f"lib-core{UNIT_SEPARATOR}"
                        "symbol:py:lib_core:load_config")
            self.assertIn(expected, caller_ids)

    def test_callers_single_repo_unchanged(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_graph(root, _graph_payload({
                "symbol:py:m:foo": {"type": "symbol", "label": "foo",
                                    "props": {"qualname": "m.foo"}},
                "symbol:py:m:bar": {"type": "symbol", "label": "bar",
                                    "props": {"qualname": "m.bar"}},
            }, edges=[{"from": "symbol:py:m:bar",
                       "to": "symbol:py:m:foo", "type": "calls"}]))
            result = mcp_server.dispatch(
                "weld_callers",
                {"symbol_id": "symbol:py:m:foo", "depth": 1}, root=root)
            caller_ids = {c["id"] for c in result["callers"]}
            self.assertIn("symbol:py:m:bar", caller_ids)
            self.assertTrue(
                all(UNIT_SEPARATOR not in c for c in caller_ids))

    def test_callers_federated_not_found(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _build_symbol_workspace(root)
            result = mcp_server.dispatch(
                "weld_callers",
                {"symbol_id": "nonexistent:symbol"}, root=root)
            self.assertIn("error", result)


# -- References --------------------------------------------------------------

class McpReferencesFederationTest(unittest.TestCase):

    def test_references_federated_finds_across_children(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _build_symbol_workspace(root)
            result = mcp_server.dispatch(
                "weld_references", {"symbol_name": "validate"},
                root=root)
            match_ids = {m["id"] for m in result["matches"]}
            self.assertIn(
                f"lib-core{UNIT_SEPARATOR}symbol:py:lib_core:validate",
                match_ids)
            self.assertIn(
                f"lib-auth{UNIT_SEPARATOR}symbol:unresolved:validate",
                match_ids)
            caller_ids = {c["id"] for c in result["callers"]}
            self.assertIn(
                f"lib-auth{UNIT_SEPARATOR}"
                "symbol:py:lib_auth:authenticate", caller_ids)

    def test_references_single_repo_unchanged(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_graph(root, _graph_payload({
                "symbol:py:m:validate": {
                    "type": "symbol", "label": "validate",
                    "props": {"qualname": "m.validate"}},
                "symbol:py:m:caller": {
                    "type": "symbol", "label": "caller",
                    "props": {"qualname": "m.caller"}},
            }, edges=[{"from": "symbol:py:m:caller",
                       "to": "symbol:py:m:validate",
                       "type": "calls"}]))
            result = mcp_server.dispatch(
                "weld_references", {"symbol_name": "validate"},
                root=root)
            match_ids = {m["id"] for m in result["matches"]}
            self.assertIn("symbol:py:m:validate", match_ids)
            self.assertTrue(
                all(UNIT_SEPARATOR not in i for i in match_ids))


if __name__ == "__main__":
    unittest.main()
