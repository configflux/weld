"""Tests for ``weld.diff`` -- graph diff between discovery runs.

Covers the core diff engine (``compute_graph_diff``), the CLI surface
(``wd diff`` and ``wd diff --json``), and the MCP tool
(``weld_diff``).

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
_tests_dir = str(Path(__file__).resolve().parent)
if _tests_dir not in sys.path:
    sys.path.insert(0, _tests_dir)

from diff_fixtures import base_graph as _base_graph  # noqa: E402
from diff_fixtures import write_graphs as _write_graphs  # noqa: E402


# ---------------------------------------------------------------------------
# Unit tests: compute_graph_diff
# ---------------------------------------------------------------------------

class ComputeGraphDiffTest(unittest.TestCase):
    """Test the pure diff computation function."""

    def _import_diff(self):
        from weld import diff as diff_mod
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
        from weld import diff as diff_mod
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
        (root / ".weld").mkdir(parents=True, exist_ok=True)
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
        from weld import diff as diff_mod
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
# MCP tool: weld_diff
# ---------------------------------------------------------------------------

class WeldDiffMcpToolTest(unittest.TestCase):
    """Test weld_diff MCP tool adapter + dispatch."""

    def test_weld_diff_in_registry(self) -> None:
        from weld import mcp_server
        tools = mcp_server.build_tools()
        names = {t.name for t in tools}
        self.assertIn("weld_diff", names)

    def test_weld_diff_dispatch(self) -> None:
        from weld import mcp_server
        root = Path(tempfile.mkdtemp())
        prev = _base_graph()
        curr = json.loads(json.dumps(prev))
        curr["nodes"]["entity:New"] = {
            "type": "entity", "label": "New", "props": {},
        }
        _write_graphs(root, previous=prev, current=curr)
        result = mcp_server.dispatch("weld_diff", {}, root=root)
        self.assertIn("added_nodes", result)
        self.assertIn("removed_nodes", result)
        self.assertIn("modified_nodes", result)
        self.assertIn("added_edges", result)
        self.assertIn("removed_edges", result)

    def test_weld_diff_tool_schema(self) -> None:
        from weld import mcp_server
        by_name = {t.name: t for t in mcp_server.build_tools()}
        schema = by_name["weld_diff"].input_schema
        self.assertEqual(schema["type"], "object")
        # No required args -- diff takes no parameters
        self.assertEqual(schema.get("required", []), [])

    def test_weld_diff_no_previous_returns_all_added(self) -> None:
        from weld import mcp_server
        root = Path(tempfile.mkdtemp())
        _write_graphs(root, previous=None, current=_base_graph())
        result = mcp_server.dispatch("weld_diff", {}, root=root)
        self.assertEqual(len(result["added_nodes"]), 3)


# ---------------------------------------------------------------------------
# MCP registry count (after adding weld_diff)
# ---------------------------------------------------------------------------

class WeldMcpRegistryWithDiffTest(unittest.TestCase):
    """After adding weld_enrich, the registry should have 13 tools."""

    def test_tool_registry_lists_thirteen_tools(self) -> None:
        from weld import mcp_server
        tools = mcp_server.build_tools()
        self.assertEqual(len(tools), 13)
        names = {t.name for t in tools}
        self.assertEqual(
            names,
            {
                "weld_query",
                "weld_find",
                "weld_context",
                "weld_path",
                "weld_brief",
                "weld_stale",
                "weld_callers",
                "weld_references",
                "weld_trace",
                "weld_diff",
                "weld_export",
                "weld_impact",
                "weld_enrich",
            },
        )


if __name__ == "__main__":
    unittest.main()
