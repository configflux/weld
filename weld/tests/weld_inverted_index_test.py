"""Tests for the load-time inverted index in Graph.query().

Verifies that:
- The inverted index is built at graph load time
- The index maps lowercased tokens from node ID, label, props.file,
  and props.exports to sets of node IDs
- Query uses the index to get candidate nodes in O(1) instead of O(N) scan
- Existing query behavior and results are preserved (same results as linear scan)
- Edge cases: empty graph, nodes with missing fields, reload rebuilds index

"""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

import sys
_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.graph import Graph  # noqa: E402

def _make_graph(nodes: dict, edges: list | None = None) -> Graph:
    """Create an in-memory Graph with the given nodes and edges."""
    tmp = tempfile.mkdtemp()
    g = Graph(Path(tmp))
    # Deep-copy to avoid mutations leaking between tests
    g._data = {
        "meta": {"version": 1},
        "nodes": copy.deepcopy(nodes),
        "edges": copy.deepcopy(edges) if edges else [],
    }
    # Trigger index build since we bypassed load()
    g._build_inverted_index()
    return g

# ---------------------------------------------------------------------------
# Fixture nodes
# ---------------------------------------------------------------------------

_TEST_NODES: dict[str, dict] = {
    "file:web/app/stores/page": {
        "type": "file",
        "label": "page",
        "props": {
            "file": "apps/web/app/stores/page.tsx",
            "exports": ["StoresPage"],
        },
    },
    "file:web/components/shell": {
        "type": "file",
        "label": "shell",
        "props": {
            "file": "apps/web/components/shell.tsx",
            "exports": ["SiteHeader", "SiteFooter"],
        },
    },
    "entity:Store": {
        "type": "entity",
        "label": "Store",
        "props": {"table": "store"},
    },
    "file:api/routes/health": {
        "type": "file",
        "label": "health",
        "props": {
            "file": "services/api/routes/health.py",
            "exports": ["health_check"],
        },
    },
    "agent:qa": {
        "type": "agent",
        "label": "qa",
        "props": {
            "description": "Black-box verification agent that validates completed tasks",
        },
    },
}

class InvertedIndexBuildTest(unittest.TestCase):
    """Tests that the inverted index is built correctly."""

    def test_index_exists_after_load(self) -> None:
        """Graph should have an _inverted_index attribute after construction."""
        g = _make_graph(_TEST_NODES)
        self.assertTrue(
            hasattr(g, "_inverted_index"),
            "Graph should have _inverted_index after build",
        )

    def test_index_is_dict(self) -> None:
        """The inverted index should be a plain dict."""
        g = _make_graph(_TEST_NODES)
        self.assertIsInstance(g._inverted_index, dict)

    def test_index_contains_id_tokens(self) -> None:
        """Tokens from node IDs should map to the correct node IDs."""
        g = _make_graph(_TEST_NODES)
        idx = g._inverted_index
        # "stores" is a segment in "file:web/app/stores/page"
        self.assertIn("stores", idx)
        self.assertIn("file:web/app/stores/page", idx["stores"])

    def test_index_contains_label_tokens(self) -> None:
        """Tokens from labels should be indexed."""
        g = _make_graph(_TEST_NODES)
        idx = g._inverted_index
        self.assertIn("shell", idx)
        self.assertIn("file:web/components/shell", idx["shell"])

    def test_index_contains_file_tokens(self) -> None:
        """Tokens from props.file should be indexed."""
        g = _make_graph(_TEST_NODES)
        idx = g._inverted_index
        # "health.py" contains "health"
        self.assertIn("health", idx)
        self.assertIn("file:api/routes/health", idx["health"])

    def test_index_contains_export_tokens(self) -> None:
        """Tokens from props.exports should be indexed."""
        g = _make_graph(_TEST_NODES)
        idx = g._inverted_index
        # "storespage" from lowercased "StoresPage"
        self.assertIn("storespage", idx)
        self.assertIn("file:web/app/stores/page", idx["storespage"])

    def test_index_values_are_sets(self) -> None:
        """Each index entry should be a set of node IDs."""
        g = _make_graph(_TEST_NODES)
        for token, node_ids in g._inverted_index.items():
            self.assertIsInstance(
                node_ids, set,
                f"index entry for '{token}' should be a set",
            )

    def test_index_contains_description_tokens(self) -> None:
        """Tokens from props.description should be indexed (tracked project)."""
        g = _make_graph(_TEST_NODES)
        idx = g._inverted_index
        # "verification" comes only from agent:qa's description
        self.assertIn("verification", idx)
        self.assertIn("agent:qa", idx["verification"])

    def test_empty_graph_index(self) -> None:
        """Empty graph should have an empty index."""
        g = _make_graph({})
        self.assertEqual(len(g._inverted_index), 0)

class InvertedIndexQueryTest(unittest.TestCase):
    """Tests that query() uses the inverted index and produces correct results."""

    def setUp(self) -> None:
        self.graph = _make_graph(_TEST_NODES)

    def test_single_token_query_uses_index(self) -> None:
        """Query for 'stores' should find stores/page node."""
        result = self.graph.query("stores")
        ids = [m["id"] for m in result["matches"]]
        self.assertIn("file:web/app/stores/page", ids)

    def test_single_token_query_via_export(self) -> None:
        """Query for 'SiteFooter' should find shell node."""
        result = self.graph.query("SiteFooter")
        ids = [m["id"] for m in result["matches"]]
        self.assertIn("file:web/components/shell", ids)

    def test_multi_token_query(self) -> None:
        """Query 'web stores' should find stores/page."""
        result = self.graph.query("web stores")
        ids = [m["id"] for m in result["matches"]]
        self.assertIn("file:web/app/stores/page", ids)

    def test_no_match_returns_empty(self) -> None:
        """Query for nonexistent term returns empty."""
        result = self.graph.query("zzzznonexistent42")
        self.assertEqual(len(result["matches"]), 0)

    def test_one_token_misses_falls_back_to_or(self) -> None:
        """If any token misses, OR fallback returns the matching token's hits.

        The strict-AND path zeroes (because 'xyznonexistent' has no
        candidates) and ``Graph.query`` retries via the OR fallback,
        tagging the envelope with ``degraded_match=or_fallback``.
        """
        result = self.graph.query("stores xyznonexistent")
        ids = [m["id"] for m in result["matches"]]
        self.assertIn(
            "file:web/app/stores/page", ids,
            "OR fallback should return 'stores' match when "
            "'xyznonexistent' has no candidates",
        )
        self.assertEqual(result.get("degraded_match"), "or_fallback")

    def test_no_token_matches_returns_empty(self) -> None:
        """If neither token matches, the result is honestly empty."""
        result = self.graph.query("zzznonexistent xyznonexistent")
        self.assertEqual(len(result["matches"]), 0)
        self.assertNotIn("degraded_match", result)

    def test_results_match_linear_scan(self) -> None:
        """Index-based query should return identical results to a linear scan."""
        # Build a graph without index, do linear scan
        tmp = tempfile.mkdtemp()
        g_linear = Graph(Path(tmp))
        g_linear._data = {
            "meta": {"version": 1},
            "nodes": copy.deepcopy(_TEST_NODES),
            "edges": [],
        }
        # Force linear scan by not building index
        g_linear._inverted_index = {}

        # Build indexed version
        g_indexed = _make_graph(_TEST_NODES)

        for term in ["stores", "shell", "health", "store", "page", "web"]:
            r_indexed = g_indexed.query(term)
            r_linear = g_linear.query(term)
            ids_indexed = [m["id"] for m in r_indexed["matches"]]
            ids_linear = [m["id"] for m in r_linear["matches"]]
            self.assertEqual(
                ids_indexed, ids_linear,
                f"Results for '{term}' should match between indexed and linear",
            )

    def test_case_insensitive(self) -> None:
        """Query is case-insensitive via the index."""
        result = self.graph.query("STORES")
        ids = [m["id"] for m in result["matches"]]
        self.assertIn("file:web/app/stores/page", ids)

    def test_index_rebuilt_on_load(self) -> None:
        """Calling load() should rebuild the index."""
        import json
        import os
        tmp = tempfile.mkdtemp()
        weld_dir = os.path.join(tmp, ".weld")
        os.makedirs(weld_dir)
        graph_path = os.path.join(weld_dir, "graph.json")
        with open(graph_path, "w") as f:
            json.dump({
                "meta": {"version": 1},
                "nodes": _TEST_NODES,
                "edges": [],
            }, f)

        g = Graph(Path(tmp))
        g.load()
        self.assertTrue(len(g._inverted_index) > 0)
        result = g.query("stores")
        ids = [m["id"] for m in result["matches"]]
        self.assertIn("file:web/app/stores/page", ids)

    def test_add_node_updates_index(self) -> None:
        """Adding a node should update the inverted index."""
        g = _make_graph(_TEST_NODES)
        g.add_node("file:new/module", "file", "new_module", {
            "file": "new/module.py",
            "exports": ["NewClass"],
        })
        # The index should now contain entries for the new node
        self.assertIn("file:new/module", g._inverted_index.get("newclass", set()))

class InvertedIndexMutationTest(unittest.TestCase):
    """Tests that mutations keep the index consistent."""

    def test_rm_node_updates_index(self) -> None:
        """Removing a node should remove it from the index."""
        g = _make_graph(_TEST_NODES)
        # Verify node is in index before removal
        self.assertIn("file:web/app/stores/page", g._inverted_index.get("stores", set()))
        g.rm_node("file:web/app/stores/page")
        # After removal, node should not appear in any index entry
        for token, node_ids in g._inverted_index.items():
            self.assertNotIn(
                "file:web/app/stores/page", node_ids,
                f"Removed node should not appear in index under '{token}'",
            )

    def test_merge_import_updates_index(self) -> None:
        """merge_import should update the index with new nodes."""
        g = _make_graph({})
        g.merge_import({
            "nodes": {
                "file:imported/thing": {
                    "type": "file",
                    "label": "imported thing",
                    "props": {"file": "imported/thing.py"},
                },
            },
            "edges": [],
        })
        self.assertIn(
            "file:imported/thing",
            g._inverted_index.get("imported", set()),
        )

if __name__ == "__main__":
    unittest.main()
