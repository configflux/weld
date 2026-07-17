"""CLI federation for the remaining read tools (callers/references/communities/find).

The CLI federation branch used to cover only query/context/path; the rest fell
through to the single-repo root meta-graph (repo nodes only). This pins that
``wd callers`` / ``wd references`` / ``wd communities`` / ``wd find`` now reach
child nodes in a polyrepo workspace, and that ``wd find`` == ``weld_find``
(ADR 0083 thin-wrapper invariant) via the shared federated fan-out.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from weld import mcp_server
from weld._graph_cli import main as cli_main
from weld.contract import SCHEMA_VERSION
from weld.workspace import (
    UNIT_SEPARATOR, ChildEntry, WorkspaceConfig, dump_workspaces_yaml,
)

_TS = "2026-07-12T00:00:00+00:00"


def _git(repo_root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo_root),
                   capture_output=True, text=True, check=True)


def _init_repo(repo_root: Path) -> Path:
    repo_root.mkdir(parents=True, exist_ok=True)
    _git(repo_root, "init", "-q")
    _git(repo_root, "config", "user.email", "test@example.com")
    _git(repo_root, "config", "user.name", "Weld Test")
    (repo_root / "README.md").write_text("# fixture\n", encoding="utf-8")
    _git(repo_root, "add", "README.md")
    _git(repo_root, "commit", "-q", "-m", "init")
    return repo_root


def _graph_payload(nodes: dict, edges: list[dict] | None = None, *, sv: int = 1) -> dict:
    return {"meta": {"version": SCHEMA_VERSION, "updated_at": _TS, "schema_version": sv},
            "nodes": nodes, "edges": edges or []}


def _write_graph(repo_root: Path, payload: dict) -> None:
    weld_dir = repo_root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    (weld_dir / "graph.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_file_index(repo_root: Path, files: dict[str, list[str]]) -> None:
    (repo_root / ".weld").mkdir(parents=True, exist_ok=True)
    (repo_root / ".weld" / "file-index.json").write_text(
        json.dumps({"meta": {"version": 1}, "files": files}, indent=2,
                   sort_keys=True) + "\n", encoding="utf-8")


def _sym(qual: str, file: str) -> dict:
    return {"type": "symbol", "label": qual.split(".")[-1],
            "props": {"qualname": qual, "file": file}}


def _build_workspace(root: Path) -> None:
    core = _init_repo(root / "lib-core")
    _write_graph(core, _graph_payload({
        "symbol:py:lib_core:load_config": _sym("lib_core.load_config", "src/config.py"),
        "symbol:py:lib_core:validate": _sym("lib_core.validate", "src/validate.py"),
    }, edges=[{"from": "symbol:py:lib_core:load_config",
               "to": "symbol:py:lib_core:validate", "type": "calls", "props": {}}]))
    _write_file_index(core, {"src/config.py": ["config", "load_config", "validate"]})

    auth = _init_repo(root / "lib-auth")
    _write_graph(auth, _graph_payload({
        "symbol:py:lib_auth:authenticate": _sym("lib_auth.authenticate", "src/auth.py"),
        "symbol:unresolved:validate": {
            "type": "symbol", "label": "validate", "props": {"qualname": "validate"}},
    }, edges=[{"from": "symbol:py:lib_auth:authenticate",
               "to": "symbol:unresolved:validate", "type": "calls", "props": {}}]))
    _write_file_index(auth, {"src/auth.py": ["auth", "authenticate", "validate"]})

    root_nodes = {
        "repo:lib-core": {"type": "repo", "label": "lib-core", "props": {"path": "lib-core"}},
        "repo:lib-auth": {"type": "repo", "label": "lib-auth", "props": {"path": "lib-auth"}},
    }
    _write_graph(root, _graph_payload(root_nodes, sv=2))
    dump_workspaces_yaml(
        WorkspaceConfig(children=[ChildEntry(name="lib-core", path="lib-core"),
                                  ChildEntry(name="lib-auth", path="lib-auth")],
                        cross_repo_strategies=[]),
        root / ".weld" / "workspaces.yaml")


class FederatedReadToolsCliTest(unittest.TestCase):

    def setUp(self) -> None:
        self._prev = os.environ.get("WELD_AUTO_REFRESH")
        os.environ["WELD_AUTO_REFRESH"] = "0"
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        if self._prev is None:
            os.environ.pop("WELD_AUTO_REFRESH", None)
        else:
            os.environ["WELD_AUTO_REFRESH"] = self._prev

    def _cli(self, *args: str) -> dict:
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli_main([*args])
        return json.loads(buf.getvalue())

    def test_callers_reaches_child(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _build_workspace(root)
            target = f"lib-core{UNIT_SEPARATOR}symbol:py:lib_core:validate"
            env = self._cli("--root", str(root), "callers", target, "--json")
            caller_ids = {c["id"] for c in env["callers"]}
            self.assertIn(
                f"lib-core{UNIT_SEPARATOR}symbol:py:lib_core:load_config", caller_ids)

    def test_references_reaches_children(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _build_workspace(root)
            env = self._cli("--root", str(root), "references", "validate", "--json")
            match_ids = {m["id"] for m in env["matches"]}
            self.assertIn(
                f"lib-core{UNIT_SEPARATOR}symbol:py:lib_core:validate", match_ids)
            self.assertIn(
                f"lib-auth{UNIT_SEPARATOR}symbol:unresolved:validate", match_ids)

    def test_communities_spans_children(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _build_workspace(root)
            env = self._cli("--root", str(root), "communities", "--json")
            # child (prefixed) nodes present in the flattened community analysis.
            self.assertTrue(
                any(UNIT_SEPARATOR in nid for nid in env["assignments"]))
            self.assertGreaterEqual(env["summary"]["total_nodes"], 4)

    def test_find_reaches_child_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _build_workspace(root)
            env = self._cli("--root", str(root), "find", "config", "--json")
            paths = {f["path"] for f in env["files"]}
            self.assertIn("lib-core/src/config.py", paths)

    def test_find_cli_equals_mcp(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _build_workspace(root)
            cli_env = self._cli("--root", str(root), "find", "auth", "--json")
            mcp_env = mcp_server.weld_find("auth", root=str(root))
            self.assertEqual(cli_env, mcp_env)
            self.assertIn("lib-auth/src/auth.py", {f["path"] for f in mcp_env["files"]})


if __name__ == "__main__":
    unittest.main()
