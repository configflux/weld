"""Root->child descent navigation for federated ``repo:<name>`` nodes.

ADR 0081: ``FederatedGraph`` synthesizes ``repo:<name> --contains-->
<child anchor>`` edges at read time so ``context``/``path`` can navigate
*down* from a repo meta-node into a child, without mutating the persisted
root ``graph.json`` (determinism, ADR 0012). Anchors are the child's
containment roots -- node ids never the ``to`` of a ``contains`` edge --
plus, for a root-less containment cycle, the min-id representative of that
strongly-connected component (ADR 0091).
"""

from __future__ import annotations

import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from weld._federation_descent import child_containment_roots, descent_edges_for
from weld._sqlite_reader import SqliteBackedGraph, open_sidecar_if_fresh
from weld._sqlite_writer import build_sidecar_for_bytes
from weld.federation import FederatedGraph, prefix_node_id
from weld.federation_support import CorruptChild, edge_key, sorted_edges
from weld.graph import main as graph_main
from weld.serializer import dumps_graph
from weld.tests._federation_descent_fixtures import (
    _child_payload,
    _init_repo,
    _write_graph,
    _write_root_graph,
    _write_workspaces,
)
from weld.workspace import ChildEntry

def _run_cli(root: Path, *args: str) -> dict:
    stdout = io.StringIO()
    with patch("sys.stdout", stdout):
        graph_main(["--root", str(root), *args, "--json"])
    return json.loads(stdout.getvalue())


# Child ``repo-a``: two files (containment roots) + one contained symbol.
_A_START = "file:src/a-start.py"
_A_BRIDGE = "file:src/a-bridge.py"
_A_SYMBOL = "symbol:alpha"


class FederatedDescentTest(unittest.TestCase):
    def _workspace(self) -> Path:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)

        repo_a = _init_repo(root / "repo-a")
        _write_graph(
            repo_a,
            _child_payload(
                {
                    _A_START: {"type": "file", "label": "alpha start", "props": {"file": _A_START}},
                    _A_BRIDGE: {"type": "file", "label": "alpha bridge", "props": {"file": _A_BRIDGE}},
                    _A_SYMBOL: {"type": "symbol", "label": "alpha", "props": {"qualname": "alpha"}},
                },
                [
                    {"from": _A_START, "to": _A_SYMBOL, "type": "contains", "props": {}},
                    {"from": _A_START, "to": _A_BRIDGE, "type": "depends_on", "props": {}},
                ],
            ),
        )

        # Child ``repo-c``: a single, edge-less node (its own containment root).
        repo_c = _init_repo(root / "repo-c")
        _write_graph(
            repo_c,
            _child_payload(
                {"file:src/c-only.py": {"type": "file", "label": "gamma", "props": {"file": "src/c-only.py"}}}
            ),
        )

        _init_repo(root / "repo-uninit")  # git repo, no graph.json -> uninitialized
        _write_workspaces(
            root,
            [
                ChildEntry(name="repo-a", path="repo-a"),
                ChildEntry(name="repo-c", path="repo-c"),
                ChildEntry(name="repo-missing", path="repo-missing"),
                ChildEntry(name="repo-uninit", path="repo-uninit"),
            ],
        )
        _write_root_graph(root, ["repo-a", "repo-c", "repo-missing", "repo-uninit"])
        return root

    def test_context_repo_node_returns_containment_root_neighbors(self) -> None:
        root = self._workspace()
        with FederatedGraph(root) as graph:
            payload = graph.context("repo:repo-a")

        neighbor_ids = {n["id"] for n in payload["neighbors"]}
        self.assertIn(prefix_node_id("repo-a", _A_START), neighbor_ids)
        self.assertIn(prefix_node_id("repo-a", _A_BRIDGE), neighbor_ids)
        # The contained symbol is reachable *through* its file, not a direct
        # descent neighbor of the repo node.
        self.assertNotIn(prefix_node_id("repo-a", _A_SYMBOL), neighbor_ids)

        contains = [
            e for e in payload["edges"]
            if e["type"] == "contains" and e["from"] == "repo:repo-a"
        ]
        self.assertEqual(
            {e["to"] for e in contains},
            {prefix_node_id("repo-a", _A_START), prefix_node_id("repo-a", _A_BRIDGE)},
        )
        # Display metadata rides on the synthetic edge like any other.
        self.assertTrue(all(e["from_display"] == "repo:repo-a" for e in contains))
        self.assertIn("repo-a::file:src/a-start.py", {e["to_display"] for e in contains})

    def test_path_descends_from_repo_node_into_contained_symbol(self) -> None:
        root = self._workspace()
        with FederatedGraph(root) as graph:
            result = graph.path("repo:repo-a", prefix_node_id("repo-a", _A_SYMBOL))

        self.assertIsNotNone(result["path"])
        self.assertEqual(
            [node["display_id"] for node in result["path"]],
            ["repo:repo-a", "repo-a::file:src/a-start.py", "repo-a::symbol:alpha"],
        )

    def test_isolated_child_node_is_its_own_descent_root(self) -> None:
        root = self._workspace()
        target = prefix_node_id("repo-c", "file:src/c-only.py")
        with FederatedGraph(root) as graph:
            ctx = graph.context("repo:repo-c")
            result = graph.path("repo:repo-c", target)

        self.assertEqual({n["id"] for n in ctx["neighbors"]}, {target})
        self.assertEqual(
            [node["display_id"] for node in result["path"]],
            ["repo:repo-c", "repo-c::file:src/c-only.py"],
        )

    def test_missing_and_uninitialized_children_have_no_descent(self) -> None:
        root = self._workspace()
        with FederatedGraph(root) as graph:
            for name in ("repo-missing", "repo-uninit"):
                with self.subTest(child=name):
                    payload = graph.context(f"repo:{name}")
                    self.assertEqual(payload["node"]["id"], f"repo:{name}")
                    self.assertEqual(payload["neighbors"], [])
                    self.assertEqual(payload["edges"], [])

    def test_descent_edges_are_not_persisted_to_root_graph(self) -> None:
        root = self._workspace()
        root_graph_path = root / ".weld" / "graph.json"
        before = root_graph_path.read_bytes()

        with FederatedGraph(root) as graph:
            graph.context("repo:repo-a")
            graph.path("repo:repo-a", prefix_node_id("repo-a", _A_SYMBOL))
            # The persisted root graph carries no synthetic descent edges.
            self.assertEqual(graph.dump().get("edges", []), [])

        self.assertEqual(root_graph_path.read_bytes(), before)

    def test_cli_context_repo_node_descends(self) -> None:
        root = self._workspace()
        payload = _run_cli(root, "context", "repo:repo-a")

        neighbor_ids = {n["id"] for n in payload["neighbors"]}
        self.assertIn(prefix_node_id("repo-a", _A_START), neighbor_ids)
        self.assertIn(prefix_node_id("repo-a", _A_BRIDGE), neighbor_ids)
        self.assertTrue(
            any(e["type"] == "contains" and e["from"] == "repo:repo-a" for e in payload["edges"])
        )

    def test_cli_path_repo_node_into_child_symbol(self) -> None:
        root = self._workspace()
        payload = _run_cli(root, "path", "repo:repo-a", "repo-a::symbol:alpha")

        self.assertEqual(
            [node["display_id"] for node in payload["path"]],
            ["repo:repo-a", "repo-a::file:src/a-start.py", "repo-a::symbol:alpha"],
        )

    def test_containment_roots_over_sqlite_backed_child(self) -> None:
        # A fresh sidecar is the production load path (ADR 0058), so the
        # sqlite branch of ``child_containment_roots`` must produce the same
        # roots as the JSON path. Build a real sidecar and read it back.
        with TemporaryDirectory() as tmp:
            weld_dir = Path(tmp) / ".weld"
            weld_dir.mkdir(parents=True)
            payload = _child_payload(
                {
                    _A_START: {"type": "file", "label": "s", "props": {"file": _A_START}},
                    _A_BRIDGE: {"type": "file", "label": "b", "props": {"file": _A_BRIDGE}},
                    _A_SYMBOL: {"type": "symbol", "label": "a", "props": {"qualname": "a"}},
                },
                [{"from": _A_START, "to": _A_SYMBOL, "type": "contains", "props": {}}],
            )
            body = dumps_graph(payload).encode("utf-8")
            graph_path = weld_dir / "graph.json"
            graph_path.write_bytes(body)
            build_sidecar_for_bytes(payload, body, weld_dir / "graph.db", generated_at="t")

            handle = open_sidecar_if_fresh(graph_path)
            self.assertIsInstance(handle, SqliteBackedGraph)
            try:
                # The contained symbol is excluded; the two files are roots.
                self.assertEqual(
                    child_containment_roots(handle), sorted([_A_START, _A_BRIDGE]),
                )
            finally:
                handle.close()

    # Item 1: a CORRUPT child (present but unparseable graph.json) descends to
    # nothing via the isinstance(child, (Graph, SqliteBackedGraph)) guard.
    def _corrupt_workspace(self) -> Path:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        repo = _init_repo(root / "repo-corrupt")
        weld_dir = repo / ".weld"
        weld_dir.mkdir(parents=True, exist_ok=True)
        # Present but unparseable -> CorruptChild (not missing/uninitialized).
        (weld_dir / "graph.json").write_text("not valid json {", encoding="utf-8")
        _write_workspaces(root, [ChildEntry(name="repo-corrupt", path="repo-corrupt")])
        _write_root_graph(root, ["repo-corrupt"])
        return root

    def test_corrupt_child_descends_to_nothing(self) -> None:
        root = self._corrupt_workspace()
        with FederatedGraph(root) as graph:
            # The fixture genuinely produces the corrupt sentinel, so the
            # isinstance guard -- not an unregistered/missing branch -- is what
            # returns []. Break the guard and this call raises AttributeError.
            self.assertIsInstance(graph._load_child("repo-corrupt"), CorruptChild)
            self.assertEqual(descent_edges_for(graph, "repo:repo-corrupt"), [])
            payload = graph.context("repo:repo-corrupt")
        self.assertEqual(payload["node"]["id"], "repo:repo-corrupt")
        self.assertEqual(payload["neighbors"], [])
        self.assertEqual(payload["edges"], [])

    # Item 2: descent edges COEXISTING with persisted cross_repo:* root edges on
    # one repo:<name> node -- exercises ``_root_edges_for(id) + _descent(id)``
    # with BOTH non-empty. e1: distinct external neighbor. e2: cross-repo edge
    # onto a descent-target anchor (neighbor-union). e3: a contains edge
    # byte-identical to a synthesized descent edge -- pins the concat's edge_key
    # dedup (ADR 0081 never persists these; seeding one exercises the guard).
    def _coexistence_workspace(self) -> Path:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        repo_a = _init_repo(root / "repo-a")
        # Two edge-less files -> both containment roots -> two descent edges.
        _write_graph(repo_a, _child_payload({
            _A_START: {"type": "file", "label": "s", "props": {"file": _A_START}},
            _A_BRIDGE: {"type": "file", "label": "b", "props": {"file": _A_BRIDGE}},
        }))
        a_start = prefix_node_id("repo-a", _A_START)
        edges = [
            {"from": "repo:repo-a", "to": "repo:repo-b", "type": "cross_repo:depends_on", "props": {}},
            {"from": "repo:repo-a", "to": a_start, "type": "cross_repo:calls", "props": {}},
            {"from": "repo:repo-a", "to": a_start, "type": "contains", "props": {}},
        ]
        _write_workspaces(root, [ChildEntry(name="repo-a", path="repo-a")])
        # repo:repo-b: a resolvable root node; needs no child graph.
        _write_root_graph(root, ["repo-a", "repo-b"], edges)
        return root

    def test_descent_and_cross_repo_edges_coexist(self) -> None:
        root = self._coexistence_workspace()
        a_start = prefix_node_id("repo-a", _A_START)
        a_bridge = prefix_node_id("repo-a", _A_BRIDGE)
        with FederatedGraph(root) as graph:
            ctx = graph.context("repo:repo-a")
            ctx_again = graph.context("repo:repo-a")
        edges = ctx["edges"]
        cross = {(e["to"], e["type"]) for e in edges if e["type"].startswith("cross_repo")}
        contains = {e["to"] for e in edges if e["type"] == "contains"}
        # Both sources appear: the persisted cross_repo:* edge and both descent edges.
        self.assertIn(("repo:repo-b", "cross_repo:depends_on"), cross)
        self.assertEqual(contains, {a_start, a_bridge})
        # Neighbor-union across both sources; the shared anchor appears once.
        self.assertEqual({n["id"] for n in ctx["neighbors"]}, {"repo:repo-b", a_start, a_bridge})
        self.assertEqual(len([n for n in ctx["neighbors"] if n["id"] == a_start]), 1)
        # edge_key dedup: e3 collapses into the descent edge (one contains->a_start);
        # the distinct-type cross_repo:calls edge to that anchor survives.
        self.assertEqual(len([e for e in edges if e["type"] == "contains" and e["to"] == a_start]), 1)
        self.assertTrue(any(e["type"] == "cross_repo:calls" and e["to"] == a_start for e in edges))
        # No duplicate edges, deterministic (edge_key-sorted) order, stable calls.
        self.assertEqual(len(edges), len({edge_key(e) for e in edges}))
        self.assertEqual(edges, sorted_edges(edges))
        self.assertEqual(edges, ctx_again["edges"])



if __name__ == "__main__":
    unittest.main()
