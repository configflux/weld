"""Tests for the viz API ``GET /api/diff`` wiring (bd h6z0.9).

Split out of ``weld_viz_test.py`` so the diff coverage stays cohesive
with the static-asset assertions (``weld_viz_static_test.py``) and the
search-suggest tests (``weld_viz_search_test.py``), and so neither
file pushes past the 400-line default cap (see CLAUDE.md "Line-Count
Policy").
"""

from __future__ import annotations

import json
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import urlopen

from weld.contract import SCHEMA_VERSION
from weld.viz.api import VizApi
from weld.viz.server import make_server

_TS = "2026-04-16T19:30:00+00:00"


def _graph_payload(nodes: dict, edges: list[dict] | None = None) -> dict:
    return {
        "meta": {"version": SCHEMA_VERSION, "updated_at": _TS, "schema_version": 1},
        "nodes": nodes,
        "edges": edges or [],
    }


def _write_graph(root: Path, payload: dict) -> None:
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    (root / ".weld" / "graph.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_previous(root: Path, payload: dict) -> None:
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    (root / ".weld" / "graph-previous.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _simple_current_only() -> TemporaryDirectory:
    """Tempdir with only ``graph.json`` (no previous snapshot)."""
    tmp = TemporaryDirectory()
    root = Path(tmp.name)
    nodes = {
        "service:api": {"type": "service", "label": "api", "props": {}},
        "entity:Store": {"type": "entity", "label": "Store", "props": {}},
    }
    edges = [
        {"from": "service:api", "to": "entity:Store", "type": "exposes", "props": {}},
    ]
    _write_graph(root, _graph_payload(nodes, edges))
    return tmp


class VizApiDiffContractTest(unittest.TestCase):
    """Direct ``VizApi.diff`` calls -- exercises the stable contract."""

    def test_returns_stable_contract_keys(self) -> None:
        with _simple_current_only() as tmp:
            payload = VizApi(tmp).diff({})
        for key in (
            "added_nodes",
            "removed_nodes",
            "modified_nodes",
            "added_edges",
            "removed_edges",
        ):
            self.assertIn(key, payload, f"missing key {key!r}")
        self.assertEqual(payload["viz_api_version"], 1)

    def test_no_previous_treats_all_as_added(self) -> None:
        # ``compute_graph_diff(None, current)`` branch: every current
        # node lands in added_nodes and removed/modified stay empty.
        with _simple_current_only() as tmp:
            payload = VizApi(tmp).diff({})
        added_ids = {entry["id"] for entry in payload["added_nodes"]}
        self.assertEqual(added_ids, {"service:api", "entity:Store"})
        self.assertEqual(payload["removed_nodes"], [])
        self.assertEqual(payload["modified_nodes"], [])

    def test_missing_current_returns_empty(self) -> None:
        # Neither graph.json nor graph-previous.json on disk: the diff
        # is empty across the board (the friendly empty-state path).
        with TemporaryDirectory() as tmp:
            payload = VizApi(tmp).diff({})
        self.assertEqual(payload["added_nodes"], [])
        self.assertEqual(payload["removed_nodes"], [])
        self.assertEqual(payload["modified_nodes"], [])
        self.assertEqual(payload["added_edges"], [])
        self.assertEqual(payload["removed_edges"], [])


class VizApiTwoSnapshotDiffTest(unittest.TestCase):
    """Two-snapshot fixture exercising add / remove / modify together."""

    def _two_snapshots(self, root: Path) -> None:
        # Snapshot A (previous): service + Store + helper symbol.
        # Snapshot B (current): drops helper, adds Customer, mutates
        # the Store label. Edges shift to match.
        nodes_a = {
            "service:api": {"type": "service", "label": "api", "props": {}},
            "entity:Store": {"type": "entity", "label": "Store", "props": {}},
            "symbol:helper": {"type": "symbol", "label": "helper", "props": {}},
        }
        edges_a = [
            {"from": "service:api", "to": "entity:Store", "type": "exposes", "props": {}},
            {"from": "symbol:helper", "to": "entity:Store", "type": "calls", "props": {}},
        ]
        nodes_b = {
            "service:api": {"type": "service", "label": "api", "props": {}},
            "entity:Store": {"type": "entity", "label": "Store v2", "props": {}},
            "entity:Customer": {"type": "entity", "label": "Customer", "props": {}},
        }
        edges_b = [
            {"from": "service:api", "to": "entity:Store", "type": "exposes", "props": {}},
            {"from": "service:api", "to": "entity:Customer", "type": "exposes", "props": {}},
        ]
        _write_previous(root, _graph_payload(nodes_a, edges_a))
        _write_graph(root, _graph_payload(nodes_b, edges_b))

    def test_reports_node_and_edge_changes(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._two_snapshots(root)
            payload = VizApi(root).diff({})
        added_ids = [entry["id"] for entry in payload["added_nodes"]]
        removed_ids = [entry["id"] for entry in payload["removed_nodes"]]
        modified_ids = [entry["id"] for entry in payload["modified_nodes"]]
        self.assertEqual(added_ids, ["entity:Customer"])
        self.assertEqual(removed_ids, ["symbol:helper"])
        self.assertEqual(modified_ids, ["entity:Store"])
        added_keys = {(e["from"], e["to"]) for e in payload["added_edges"]}
        removed_keys = {(e["from"], e["to"]) for e in payload["removed_edges"]}
        self.assertIn(("service:api", "entity:Customer"), added_keys)
        self.assertIn(("symbol:helper", "entity:Store"), removed_keys)


class VizHttpDiffEndpointTest(unittest.TestCase):
    """End-to-end check that ``GET /api/diff`` is wired through the server."""

    def _with_server(self, root: Path) -> str:
        server = make_server(str(root), host="127.0.0.1", port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}"

    def test_http_diff_endpoint_returns_diff_contract(self) -> None:
        with _simple_current_only() as tmp:
            base = self._with_server(Path(tmp))
            payload = json.loads(urlopen(f"{base}/api/diff", timeout=5).read())
        for key in (
            "added_nodes",
            "removed_nodes",
            "modified_nodes",
            "added_edges",
            "removed_edges",
        ):
            self.assertIn(key, payload)
        # No previous snapshot on disk -> every current node is added.
        self.assertGreater(len(payload["added_nodes"]), 0)


if __name__ == "__main__":
    unittest.main()
