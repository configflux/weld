"""Tests for ``cortex.diff`` -- graph diff between discovery runs.

Covers the core diff engine (``compute_graph_diff``), the CLI surface
(``cortex diff`` and ``cortex diff --json``), and the MCP tool
(``cortex_diff``).

Scenarios: empty diff (no changes), add-only, remove-only, modify-only,
edge changes, and mixed scenarios.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from cortex.contract import SCHEMA_VERSION  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _base_graph() -> dict:
    """Return a minimal baseline graph."""
    return {
        "meta": {
            "version": SCHEMA_VERSION,
            "git_sha": "aaa111",
            "updated_at": "2026-04-13T00:00:00+00:00",
        },
        "nodes": {
            "entity:Store": {
                "type": "entity",
                "label": "Store",
                "props": {
                    "file": "models/store.py",
                    "exports": ["Store"],
                },
            },
            "entity:Offer": {
                "type": "entity",
                "label": "Offer",
                "props": {
                    "file": "models/offer.py",
                    "exports": ["Offer"],
                },
            },
            "route:GET:/stores": {
                "type": "route",
                "label": "list_stores",
                "props": {
                    "file": "routes/stores.py",
                    "exports": ["list_stores"],
                },
            },
        },
        "edges": [
            {
                "from": "entity:Offer",
                "to": "entity:Store",
                "type": "depends_on",
                "props": {},
            },
            {
                "from": "route:GET:/stores",
                "to": "entity:Store",
                "type": "responds_with",
                "props": {},
            },
        ],
    }


def _write_graphs(root: Path, previous: dict | None, current: dict) -> None:
    """Write previous and current graph files into a temp .cortex dir."""
    cortex_dir = root / ".cortex"
    cortex_dir.mkdir(parents=True, exist_ok=True)
    cortex_dir.joinpath("graph.json").write_text(
        json.dumps(current, indent=2), encoding="utf-8"
    )
    if previous is not None:
        cortex_dir.joinpath("graph-previous.json").write_text(
            json.dumps(previous, indent=2), encoding="utf-8"
        )


# ---------------------------------------------------------------------------
# Unit tests: compute_graph_diff
# ---------------------------------------------------------------------------

class ComputeGraphDiffTest(unittest.TestCase):
    """Test the pure diff computation function."""

    def _import_diff(self):
        from cortex import diff as diff_mod
        return diff_mod

    def test_empty_diff_identical_graphs(self) -> None:
        diff_mod = self._import_diff()
        g = _base_graph()
        result = diff_mod.compute_graph_diff(g, g)
        self.assertEqual(result["added_nodes"], [])
        self.assertEqual(result["removed_nodes"], [])
        self.assertEqual(result["modified_nodes"], [])
        self.assertEqual(result["added_edges"], [])
        self.assertEqual(result["removed_edges"], [])

    def test_add_only_nodes(self) -> None:
        diff_mod = self._import_diff()
        prev = _base_graph()
        curr = json.loads(json.dumps(prev))
        curr["nodes"]["entity:Product"] = {
            "type": "entity",
            "label": "Product",
            "props": {"file": "models/product.py"},
        }
        result = diff_mod.compute_graph_diff(prev, curr)
        added_ids = [n["id"] for n in result["added_nodes"]]
        self.assertEqual(added_ids, ["entity:Product"])
        self.assertEqual(result["removed_nodes"], [])
        self.assertEqual(result["modified_nodes"], [])

    def test_remove_only_nodes(self) -> None:
        diff_mod = self._import_diff()
        prev = _base_graph()
        curr = json.loads(json.dumps(prev))
        del curr["nodes"]["entity:Offer"]
        # Also remove edges referencing the deleted node
        curr["edges"] = [e for e in curr["edges"] if e["from"] != "entity:Offer"]
        result = diff_mod.compute_graph_diff(prev, curr)
        removed_ids = [n["id"] for n in result["removed_nodes"]]
        self.assertEqual(removed_ids, ["entity:Offer"])
        self.assertEqual(result["added_nodes"], [])
        self.assertEqual(result["modified_nodes"], [])

    def test_modify_node_props_changed(self) -> None:
        diff_mod = self._import_diff()
        prev = _base_graph()
        curr = json.loads(json.dumps(prev))
        curr["nodes"]["entity:Store"]["props"]["exports"] = ["Store", "StoreConfig"]
        result = diff_mod.compute_graph_diff(prev, curr)
        self.assertEqual(len(result["modified_nodes"]), 1)
        mod = result["modified_nodes"][0]
        self.assertEqual(mod["id"], "entity:Store")
        self.assertIn("before", mod)
        self.assertIn("after", mod)
        self.assertEqual(mod["before"]["props"]["exports"], ["Store"])
        self.assertEqual(mod["after"]["props"]["exports"], ["Store", "StoreConfig"])

    def test_modify_node_label_changed(self) -> None:
        diff_mod = self._import_diff()
        prev = _base_graph()
        curr = json.loads(json.dumps(prev))
        curr["nodes"]["route:GET:/stores"]["label"] = "get_stores"
        result = diff_mod.compute_graph_diff(prev, curr)
        self.assertEqual(len(result["modified_nodes"]), 1)
        self.assertEqual(result["modified_nodes"][0]["id"], "route:GET:/stores")

    def test_add_edges(self) -> None:
        diff_mod = self._import_diff()
        prev = _base_graph()
        curr = json.loads(json.dumps(prev))
        curr["edges"].append({
            "from": "route:GET:/stores",
            "to": "entity:Offer",
            "type": "depends_on",
            "props": {},
        })
        result = diff_mod.compute_graph_diff(prev, curr)
        self.assertEqual(len(result["added_edges"]), 1)
        ae = result["added_edges"][0]
        self.assertEqual(ae["from"], "route:GET:/stores")
        self.assertEqual(ae["to"], "entity:Offer")

    def test_remove_edges(self) -> None:
        diff_mod = self._import_diff()
        prev = _base_graph()
        curr = json.loads(json.dumps(prev))
        curr["edges"] = [e for e in curr["edges"] if e["type"] != "depends_on"]
        result = diff_mod.compute_graph_diff(prev, curr)
        self.assertEqual(len(result["removed_edges"]), 1)
        re_ = result["removed_edges"][0]
        self.assertEqual(re_["from"], "entity:Offer")
        self.assertEqual(re_["to"], "entity:Store")
        self.assertEqual(re_["type"], "depends_on")

    def test_mixed_changes(self) -> None:
        diff_mod = self._import_diff()
        prev = _base_graph()
        curr = json.loads(json.dumps(prev))

        # Add a node
        curr["nodes"]["symbol:py:auth:login"] = {
            "type": "symbol",
            "label": "login",
            "props": {"file": "auth/login.py"},
        }
        # Remove a node
        del curr["nodes"]["entity:Offer"]
        curr["edges"] = [e for e in curr["edges"] if
                         e["from"] != "entity:Offer" and e["to"] != "entity:Offer"]
        # Modify a node
        curr["nodes"]["entity:Store"]["props"]["exports"] = ["Store", "StoreV2"]
        # Add an edge
        curr["edges"].append({
            "from": "symbol:py:auth:login",
            "to": "entity:Store",
            "type": "depends_on",
            "props": {},
        })

        result = diff_mod.compute_graph_diff(prev, curr)
        self.assertEqual(len(result["added_nodes"]), 1)
        self.assertEqual(len(result["removed_nodes"]), 1)
        self.assertEqual(len(result["modified_nodes"]), 1)
        self.assertGreaterEqual(len(result["added_edges"]), 1)
        self.assertGreaterEqual(len(result["removed_edges"]), 1)

    def test_no_previous_graph_treats_all_as_added(self) -> None:
        diff_mod = self._import_diff()
        curr = _base_graph()
        result = diff_mod.compute_graph_diff(None, curr)
        self.assertEqual(len(result["added_nodes"]), 3)
        self.assertEqual(result["removed_nodes"], [])
        self.assertEqual(result["modified_nodes"], [])
        self.assertEqual(len(result["added_edges"]), 2)
        self.assertEqual(result["removed_edges"], [])


# ---------------------------------------------------------------------------
# Integration: load_and_diff from disk
# ---------------------------------------------------------------------------

class LoadAndDiffTest(unittest.TestCase):
    """Test loading graphs from disk and computing the diff."""

    def _import_diff(self):
        from cortex import diff as diff_mod
        return diff_mod

    def test_load_and_diff_no_previous(self) -> None:
        diff_mod = self._import_diff()
        root = Path(tempfile.mkdtemp())
        _write_graphs(root, previous=None, current=_base_graph())
        result = diff_mod.load_and_diff(root)
        # No previous graph -> all nodes are added
        self.assertEqual(len(result["added_nodes"]), 3)

    def test_load_and_diff_with_previous(self) -> None:
        diff_mod = self._import_diff()
        root = Path(tempfile.mkdtemp())
        prev = _base_graph()
        curr = json.loads(json.dumps(prev))
        curr["nodes"]["entity:New"] = {
            "type": "entity", "label": "New", "props": {},
        }
        _write_graphs(root, previous=prev, current=curr)
        result = diff_mod.load_and_diff(root)
        added_ids = [n["id"] for n in result["added_nodes"]]
        self.assertIn("entity:New", added_ids)

    def test_load_and_diff_no_graph_at_all(self) -> None:
        diff_mod = self._import_diff()
        root = Path(tempfile.mkdtemp())
        (root / ".cortex").mkdir(parents=True, exist_ok=True)
        # No graph.json at all
        result = diff_mod.load_and_diff(root)
        self.assertEqual(result["added_nodes"], [])
        self.assertEqual(result["removed_nodes"], [])


# ---------------------------------------------------------------------------
# Human-readable formatting
# ---------------------------------------------------------------------------

class FormatHumanDiffTest(unittest.TestCase):
    """Test human-readable output formatting."""

    def _import_diff(self):
        from cortex import diff as diff_mod
        return diff_mod

    def test_empty_diff_message(self) -> None:
        diff_mod = self._import_diff()
        empty = diff_mod.compute_graph_diff(_base_graph(), _base_graph())
        text = diff_mod.format_human(empty)
        self.assertIn("No changes", text)

    def test_added_nodes_shown(self) -> None:
        diff_mod = self._import_diff()
        prev = _base_graph()
        curr = json.loads(json.dumps(prev))
        curr["nodes"]["symbol:py:auth:login"] = {
            "type": "symbol", "label": "login", "props": {},
        }
        curr["nodes"]["symbol:py:auth:logout"] = {
            "type": "symbol", "label": "logout", "props": {},
        }
        curr["nodes"]["entity:Product"] = {
            "type": "entity", "label": "Product", "props": {},
        }
        result = diff_mod.compute_graph_diff(prev, curr)
        text = diff_mod.format_human(result)
        self.assertIn("+ 3 nodes added", text)
        self.assertIn("symbol:py:auth:login", text)

    def test_removed_nodes_shown(self) -> None:
        diff_mod = self._import_diff()
        prev = _base_graph()
        curr = json.loads(json.dumps(prev))
        del curr["nodes"]["entity:Offer"]
        curr["edges"] = [e for e in curr["edges"] if e["from"] != "entity:Offer"]
        result = diff_mod.compute_graph_diff(prev, curr)
        text = diff_mod.format_human(result)
        self.assertIn("- 1 node removed", text)
        self.assertIn("entity:Offer", text)

    def test_modified_nodes_shown(self) -> None:
        diff_mod = self._import_diff()
        prev = _base_graph()
        curr = json.loads(json.dumps(prev))
        curr["nodes"]["entity:Store"]["props"]["exports"] = ["Store", "StoreV2"]
        result = diff_mod.compute_graph_diff(prev, curr)
        text = diff_mod.format_human(result)
        self.assertIn("~ 1 node modified", text)

    def test_edge_changes_shown(self) -> None:
        diff_mod = self._import_diff()
        prev = _base_graph()
        curr = json.loads(json.dumps(prev))
        curr["edges"].append({
            "from": "route:GET:/stores",
            "to": "entity:Offer",
            "type": "depends_on",
            "props": {},
        })
        result = diff_mod.compute_graph_diff(prev, curr)
        text = diff_mod.format_human(result)
        self.assertIn("+ 1 edge added", text)


# ---------------------------------------------------------------------------
# MCP tool: cortex_diff
# ---------------------------------------------------------------------------

class CortexDiffMcpToolTest(unittest.TestCase):
    """Test cortex_diff MCP tool adapter + dispatch."""

    def test_cortex_diff_in_registry(self) -> None:
        from cortex import mcp_server
        tools = mcp_server.build_tools()
        names = {t.name for t in tools}
        self.assertIn("cortex_diff", names)

    def test_cortex_diff_dispatch(self) -> None:
        from cortex import mcp_server
        root = Path(tempfile.mkdtemp())
        prev = _base_graph()
        curr = json.loads(json.dumps(prev))
        curr["nodes"]["entity:New"] = {
            "type": "entity", "label": "New", "props": {},
        }
        _write_graphs(root, previous=prev, current=curr)
        result = mcp_server.dispatch("cortex_diff", {}, root=root)
        self.assertIn("added_nodes", result)
        self.assertIn("removed_nodes", result)
        self.assertIn("modified_nodes", result)
        self.assertIn("added_edges", result)
        self.assertIn("removed_edges", result)

    def test_cortex_diff_tool_schema(self) -> None:
        from cortex import mcp_server
        by_name = {t.name: t for t in mcp_server.build_tools()}
        schema = by_name["cortex_diff"].input_schema
        self.assertEqual(schema["type"], "object")
        # No required args -- diff takes no parameters
        self.assertEqual(schema.get("required", []), [])

    def test_cortex_diff_no_previous_returns_all_added(self) -> None:
        from cortex import mcp_server
        root = Path(tempfile.mkdtemp())
        _write_graphs(root, previous=None, current=_base_graph())
        result = mcp_server.dispatch("cortex_diff", {}, root=root)
        self.assertEqual(len(result["added_nodes"]), 3)


# ---------------------------------------------------------------------------
# MCP registry count (after adding cortex_diff)
# ---------------------------------------------------------------------------

class CortexMcpRegistryWithDiffTest(unittest.TestCase):
    """After adding cortex_diff + cortex_export, the registry should have 11 tools."""

    def test_tool_registry_lists_eleven_tools(self) -> None:
        from cortex import mcp_server
        tools = mcp_server.build_tools()
        self.assertEqual(len(tools), 11)
        names = {t.name for t in tools}
        self.assertEqual(
            names,
            {
                "cortex_query",
                "cortex_find",
                "cortex_context",
                "cortex_path",
                "cortex_brief",
                "cortex_stale",
                "cortex_callers",
                "cortex_references",
                "cortex_trace",
                "cortex_diff",
                "cortex_export",
            },
        )


if __name__ == "__main__":
    unittest.main()
