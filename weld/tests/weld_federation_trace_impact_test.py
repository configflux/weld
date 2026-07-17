"""Cross-child reverse adjacency for federated trace / impact (ADR 0089).

``FederatedGraph.dump()`` returns only the root meta-graph, so the whole-graph
tools ``trace`` and ``impact`` historically missed every child-internal edge.
:func:`weld.federation_tools.federated_trace` / ``federated_impact`` flatten the
federation at read time (union of root + every child, child ids prefixed) and
run the unchanged pure engines, so:

* ``impact`` reverse-BFS reaches a **child-internal** dependent (edge lives in
  the child graph) *and* a **cross-repo** dependent (edge lives in the root
  meta-graph) -- both in one reverse-adjacency map.
* ``trace`` reaches child-internal and cross-child interaction nodes.

The flatten must also be deterministic (ADR 0012).

Fixture style follows ``weld_mcp_federation_tools_test`` / ``weld_impact_
federation_smoke_test``: a real ``git init`` per child plus hand-shaped
``.weld/graph.json`` so the test fails for the same reasons production would.
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
from weld.contract import SCHEMA_VERSION
from weld.federation import FederatedGraph
from weld.federation_support import prefix_node_id
from weld.federation_tools import federated_impact, federated_trace
from weld.impact_cli import main as impact_main
from weld.trace import main as trace_main
from weld.workspace import ChildEntry, WorkspaceConfig, dump_workspaces_yaml

_TS = "2026-07-12T00:00:00+00:00"


def _git(repo_root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=str(repo_root),
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
    return {
        "meta": {"version": SCHEMA_VERSION, "updated_at": _TS, "schema_version": sv},
        "nodes": nodes, "edges": edges or [],
    }


def _write_graph(repo_root: Path, payload: dict) -> None:
    weld_dir = repo_root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    (weld_dir / "graph.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_workspaces(root: Path, names: list[str]) -> None:
    config = WorkspaceConfig(
        children=[ChildEntry(name=n, path=n) for n in names],
        cross_repo_strategies=[])
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    dump_workspaces_yaml(config, root / ".weld" / "workspaces.yaml")


def _sym(qual: str) -> dict:
    return {"type": "symbol", "label": qual.split(".")[-1],
            "props": {"qualname": qual, "file": "src/x.py"}}


def _build_impact_workspace(root: Path) -> dict:
    """child-a: target + a child-internal caller; child-b: a cross-repo caller."""
    a = _init_repo(root / "child-a")
    _write_graph(a, _graph_payload({
        "symbol:py:a:target": _sym("a.target"),
        "symbol:py:a:local_caller": _sym("a.local_caller"),
    }, edges=[{"from": "symbol:py:a:local_caller",
               "to": "symbol:py:a:target", "type": "calls", "props": {}}]))
    b = _init_repo(root / "child-b")
    _write_graph(b, _graph_payload({
        "symbol:py:b:cross_caller": _sym("b.cross_caller"),
    }))
    target = prefix_node_id("child-a", "symbol:py:a:target")
    cross = prefix_node_id("child-b", "symbol:py:b:cross_caller")
    root_nodes = {
        "repo:child-a": {"type": "repo", "label": "child-a", "props": {"path": "child-a"}},
        "repo:child-b": {"type": "repo", "label": "child-b", "props": {"path": "child-b"}},
    }
    root_edges = [{"from": cross, "to": target, "type": "calls", "props": {}}]
    _write_graph(root, _graph_payload(root_nodes, root_edges, sv=2))
    _write_workspaces(root, ["child-a", "child-b"])
    return {
        "target": target,
        "local_caller": prefix_node_id("child-a", "symbol:py:a:local_caller"),
        "cross_caller": cross,
    }


def _build_trace_workspace(root: Path) -> dict:
    """child-a service -> child-internal contract; root edge -> child-b contract."""
    a = _init_repo(root / "svc-a")
    _write_graph(a, _graph_payload({
        "service:a:core": {"type": "service", "label": "core", "props": {}},
        "contract:a:local": {"type": "contract", "label": "local", "props": {}},
    }, edges=[{"from": "service:a:core", "to": "contract:a:local",
               "type": "produces", "props": {}}]))
    b = _init_repo(root / "svc-b")
    _write_graph(b, _graph_payload({
        "contract:b:remote": {"type": "contract", "label": "remote", "props": {}},
    }))
    anchor = prefix_node_id("svc-a", "service:a:core")
    local = prefix_node_id("svc-a", "contract:a:local")
    remote = prefix_node_id("svc-b", "contract:b:remote")
    root_nodes = {
        "repo:svc-a": {"type": "repo", "label": "svc-a", "props": {"path": "svc-a"}},
        "repo:svc-b": {"type": "repo", "label": "svc-b", "props": {"path": "svc-b"}},
    }
    root_edges = [{"from": anchor, "to": remote, "type": "depends_on", "props": {}}]
    _write_graph(root, _graph_payload(root_nodes, root_edges, sv=2))
    _write_workspaces(root, ["svc-a", "svc-b"])
    return {"anchor": anchor, "local": local, "remote": remote}


class ImpactCrossChildTest(unittest.TestCase):

    def test_impact_reaches_child_internal_and_cross_repo_dependents(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            ids = _build_impact_workspace(root)
            result = federated_impact(FederatedGraph(root), ids["target"], depth=3)
            direct = {d["id"] for d in result["direct_dependents"]}
            # child-internal dependent -- the capability that was missing.
            self.assertIn(ids["local_caller"], direct)
            # cross-repo dependent via the root meta-graph edge.
            self.assertIn(ids["cross_caller"], direct)
            self.assertNotIn(ids["target"], direct)

    def test_impact_is_deterministic(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            ids = _build_impact_workspace(root)
            a = federated_impact(FederatedGraph(root), ids["target"], depth=3)
            b = federated_impact(FederatedGraph(root), ids["target"], depth=3)
            self.assertEqual(
                json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))


class TraceCrossChildTest(unittest.TestCase):

    def test_trace_reaches_child_internal_and_cross_child_nodes(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            ids = _build_trace_workspace(root)
            result = federated_trace(
                FederatedGraph(root), node_id=ids["anchor"], depth=2)
            services = {n["id"] for n in result["services"]}
            contracts = {n["id"] for n in result["contracts"]}
            self.assertIn(ids["anchor"], services)
            # child-internal contract (edge lives in svc-a's graph).
            self.assertIn(ids["local"], contracts)
            # cross-child contract (edge lives in the root meta-graph).
            self.assertIn(ids["remote"], contracts)


class TraceImpactSurfaceParityTest(unittest.TestCase):
    """CLI (`wd trace` / `wd impact`) == MCP (weld_trace / weld_impact), and both
    reach child nodes (ADR 0089 + the ADR 0083 thin-wrapper invariant)."""

    def setUp(self) -> None:
        self._prev = os.environ.get("WELD_AUTO_REFRESH")
        os.environ["WELD_AUTO_REFRESH"] = "0"
        from weld._mcp_read import clear_graph_cache
        clear_graph_cache()
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        from weld._mcp_read import clear_graph_cache
        clear_graph_cache()
        if self._prev is None:
            os.environ.pop("WELD_AUTO_REFRESH", None)
        else:
            os.environ["WELD_AUTO_REFRESH"] = self._prev

    @staticmethod
    def _cli(fn, argv: list[str]) -> dict:
        buf = io.StringIO()
        with redirect_stdout(buf):
            fn(argv)
        return json.loads(buf.getvalue())

    def test_impact_cli_reaches_children_and_equals_mcp(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            ids = _build_impact_workspace(root)
            cli = self._cli(
                impact_main, ["--root", str(root), ids["target"], "--json"])
            mcp = mcp_server.weld_impact(ids["target"], root=str(root))
            direct = {d["id"] for d in mcp["direct_dependents"]}
            self.assertIn(ids["local_caller"], direct)   # child-internal
            self.assertIn(ids["cross_caller"], direct)   # cross-repo
            self.assertEqual(cli, mcp)

    def test_trace_cli_reaches_children_and_equals_mcp(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            ids = _build_trace_workspace(root)
            cli = self._cli(
                trace_main, ["--root", str(root), "--node", ids["anchor"]])
            mcp = mcp_server.weld_trace(node_id=ids["anchor"], root=str(root))
            contracts = {n["id"] for n in mcp["contracts"]}
            self.assertIn(ids["local"], contracts)    # child-internal
            self.assertIn(ids["remote"], contracts)   # cross-child
            self.assertEqual(cli, mcp)


if __name__ == "__main__":
    unittest.main()
