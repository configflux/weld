"""Tests for the ADR-0042 ``hide_origins`` filter wired through ``wd viz``.

Covers the adapter kwarg, the ``/api/slice?hide_origins=...`` query param,
and the ``nodes_by_origin`` summary counts. Co-located in a sibling test
file rather than ``weld_viz_test.py`` to keep the legacy file under the
line-count cap; both modules share the small graph fixture conventions.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from weld.contract import SCHEMA_VERSION
from weld.viz.adapter import normalize_graph_data
from weld.viz.api import VizApi

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


class HideOriginsAdapterTest(unittest.TestCase):
    def test_drops_stdlib_nodes(self) -> None:
        nodes = {
            "symbol:py:app:run": {
                "type": "symbol", "label": "run",
                "props": {"origin": "project"},
            },
            "symbol:py:builtins:print": {
                "type": "symbol", "label": "print",
                "props": {"origin": "stdlib"},
            },
        }
        data = _graph_payload(nodes)
        kept = {
            node["data"]["id"]
            for node in normalize_graph_data(
                data, hide_origins={"stdlib"})["elements"]["nodes"]
        }
        self.assertIn("symbol:py:app:run", kept)
        self.assertNotIn("symbol:py:builtins:print", kept)

    def test_drops_external_nodes(self) -> None:
        nodes = {
            "symbol:py:app:run": {
                "type": "symbol", "label": "run",
                "props": {"origin": "project"},
            },
            "symbol:py:numpy:array": {
                "type": "symbol", "label": "array",
                "props": {"origin": "external"},
            },
        }
        data = _graph_payload(nodes)
        kept = {
            node["data"]["id"]
            for node in normalize_graph_data(
                data, hide_origins={"external"})["elements"]["nodes"]
        }
        self.assertIn("symbol:py:app:run", kept)
        self.assertNotIn("symbol:py:numpy:array", kept)

    def test_combined_drops_stdlib_and_external(self) -> None:
        nodes = {
            "symbol:py:app:run": {
                "type": "symbol", "label": "run",
                "props": {"origin": "project"},
            },
            "symbol:py:builtins:print": {
                "type": "symbol", "label": "print",
                "props": {"origin": "stdlib"},
            },
            "symbol:py:numpy:array": {
                "type": "symbol", "label": "array",
                "props": {"origin": "external"},
            },
        }
        data = _graph_payload(nodes)
        kept = {
            node["data"]["id"]
            for node in normalize_graph_data(
                data, hide_origins={"stdlib", "external"})["elements"]["nodes"]
        }
        self.assertEqual(kept, {"symbol:py:app:run"})

    def test_explicit_empty_set_keeps_unresolved(self) -> None:
        # Explicit empty set means "no origin filter" -- caller is explicit
        # and does not want the legacy default to fire.
        nodes = {
            "entity:Store": {"type": "entity", "label": "Store", "props": {}},
            "symbol:unresolved:append": {
                "type": "symbol", "label": "append",
                "props": {"resolved": False},
            },
        }
        data = _graph_payload(nodes)
        kept = {
            node["data"]["id"]
            for node in normalize_graph_data(
                data, hide_origins=set())["elements"]["nodes"]
        }
        self.assertIn("symbol:unresolved:append", kept)

    def test_legacy_overview_still_hides_unresolved_by_default(self) -> None:
        # No hide_origins, no node_types -> legacy default of hiding
        # unresolved sentinels in the overview slice.
        nodes = {
            "entity:Store": {"type": "entity", "label": "Store", "props": {}},
            "symbol:unresolved:append": {
                "type": "symbol", "label": "append",
                "props": {"resolved": False},
            },
        }
        data = _graph_payload(nodes)
        kept = {
            node["data"]["id"]
            for node in normalize_graph_data(data)["elements"]["nodes"]
        }
        self.assertNotIn("symbol:unresolved:append", kept)
        self.assertIn("entity:Store", kept)


class HideOriginsApiTest(unittest.TestCase):
    def test_summary_counts_include_nodes_by_origin(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_graph(root, _graph_payload({
                "symbol:py:app:run": {
                    "type": "symbol", "label": "run",
                    "props": {"origin": "project"},
                },
                "symbol:py:builtins:print": {
                    "type": "symbol", "label": "print",
                    "props": {"origin": "stdlib"},
                },
                "symbol:py:numpy:array": {
                    "type": "symbol", "label": "array",
                    "props": {"origin": "external"},
                },
                "symbol:unresolved:foo": {
                    "type": "symbol", "label": "foo",
                    "props": {"resolved": False},
                },
            }))
            counts = VizApi(root).summary()["counts"]
        self.assertIn("nodes_by_origin", counts)
        self.assertEqual(counts["nodes_by_origin"]["project"], 1)
        self.assertEqual(counts["nodes_by_origin"]["stdlib"], 1)
        self.assertEqual(counts["nodes_by_origin"]["external"], 1)
        self.assertEqual(counts["nodes_by_origin"]["unresolved"], 1)

    def test_slice_hide_origins_csv_filters_stdlib(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_graph(root, _graph_payload({
                "symbol:py:app:run": {
                    "type": "symbol", "label": "run",
                    "props": {"origin": "project"},
                },
                "symbol:py:builtins:print": {
                    "type": "symbol", "label": "print",
                    "props": {"origin": "stdlib"},
                },
                "symbol:py:numpy:array": {
                    "type": "symbol", "label": "array",
                    "props": {"origin": "external"},
                },
            }))
            payload = VizApi(root).slice({
                "hide_origins": "stdlib",
                "max_nodes": 50,
            })
        ids = {node["data"]["id"] for node in payload["elements"]["nodes"]}
        self.assertIn("symbol:py:app:run", ids)
        self.assertIn("symbol:py:numpy:array", ids)
        self.assertNotIn("symbol:py:builtins:print", ids)


if __name__ == "__main__":
    unittest.main()
