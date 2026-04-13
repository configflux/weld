"""Tests for description-coverage metrics in Graph.stats().

Verifies that:
- stats() reports overall nodes_with_description and description_coverage_pct
- stats() reports per-node-type description coverage in description_coverage_by_type
- Empty graphs produce zero coverage with no division errors
- Mixed graphs (some nodes with, some without descriptions) count correctly
- Backward compatibility: existing stats keys (total_nodes, total_edges,
  nodes_by_type, edges_by_type) are preserved

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

from cortex.graph import Graph  # noqa: E402

def _make_graph(nodes: dict, edges: list | None = None) -> Graph:
    """Create an in-memory Graph with the given nodes and edges."""
    tmp = tempfile.mkdtemp()
    g = Graph(Path(tmp))
    g._data = {
        "meta": {"version": 1},
        "nodes": copy.deepcopy(nodes),
        "edges": copy.deepcopy(edges) if edges else [],
    }
    g._build_inverted_index()
    return g

class TestDescriptionCoverageEmptyGraph(unittest.TestCase):
    """Empty graph should report zero coverage without errors."""

    def test_empty_graph_stats(self):
        g = _make_graph({})
        s = g.stats()

        self.assertEqual(s["total_nodes"], 0)
        self.assertEqual(s["nodes_with_description"], 0)
        self.assertAlmostEqual(s["description_coverage_pct"], 0.0)
        self.assertEqual(s["description_coverage_by_type"], {})

    def test_backward_compat_keys_present(self):
        g = _make_graph({})
        s = g.stats()

        # Original keys must still be present
        self.assertIn("total_nodes", s)
        self.assertIn("total_edges", s)
        self.assertIn("nodes_by_type", s)
        self.assertIn("edges_by_type", s)

class TestDescriptionCoverageAllDescribed(unittest.TestCase):
    """All nodes have descriptions -- 100% coverage."""

    def setUp(self):
        self.nodes = {
            "entity:Store": {
                "type": "entity",
                "label": "Store",
                "props": {"description": "A retail store location."},
            },
            "entity:Product": {
                "type": "entity",
                "label": "Product",
                "props": {"description": "A purchasable product."},
            },
            "file:main.py": {
                "type": "file",
                "label": "main.py",
                "props": {"file": "main.py", "description": "Application entrypoint."},
            },
        }

    def test_full_coverage(self):
        g = _make_graph(self.nodes)
        s = g.stats()

        self.assertEqual(s["total_nodes"], 3)
        self.assertEqual(s["nodes_with_description"], 3)
        self.assertAlmostEqual(s["description_coverage_pct"], 100.0)

    def test_per_type_coverage(self):
        g = _make_graph(self.nodes)
        s = g.stats()
        by_type = s["description_coverage_by_type"]

        self.assertIn("entity", by_type)
        self.assertEqual(by_type["entity"]["total"], 2)
        self.assertEqual(by_type["entity"]["with_description"], 2)
        self.assertAlmostEqual(by_type["entity"]["coverage_pct"], 100.0)

        self.assertIn("file", by_type)
        self.assertEqual(by_type["file"]["total"], 1)
        self.assertEqual(by_type["file"]["with_description"], 1)
        self.assertAlmostEqual(by_type["file"]["coverage_pct"], 100.0)

class TestDescriptionCoverageMixed(unittest.TestCase):
    """Mix of described and undescribed nodes."""

    def setUp(self):
        self.nodes = {
            "entity:Store": {
                "type": "entity",
                "label": "Store",
                "props": {"description": "A retail store."},
            },
            "entity:Product": {
                "type": "entity",
                "label": "Product",
                "props": {},
            },
            "file:main.py": {
                "type": "file",
                "label": "main.py",
                "props": {"file": "main.py"},
            },
            "file:utils.py": {
                "type": "file",
                "label": "utils.py",
                "props": {"file": "utils.py", "description": "Utility helpers."},
            },
        }

    def test_partial_coverage(self):
        g = _make_graph(self.nodes)
        s = g.stats()

        self.assertEqual(s["total_nodes"], 4)
        self.assertEqual(s["nodes_with_description"], 2)
        self.assertAlmostEqual(s["description_coverage_pct"], 50.0)

    def test_per_type_mixed(self):
        g = _make_graph(self.nodes)
        s = g.stats()
        by_type = s["description_coverage_by_type"]

        self.assertEqual(by_type["entity"]["total"], 2)
        self.assertEqual(by_type["entity"]["with_description"], 1)
        self.assertAlmostEqual(by_type["entity"]["coverage_pct"], 50.0)

        self.assertEqual(by_type["file"]["total"], 2)
        self.assertEqual(by_type["file"]["with_description"], 1)
        self.assertAlmostEqual(by_type["file"]["coverage_pct"], 50.0)

class TestDescriptionCoverageEdgeCases(unittest.TestCase):
    """Edge cases: empty-string descriptions, None, missing key."""

    def test_empty_string_not_counted(self):
        """An empty-string description should NOT count as described."""
        nodes = {
            "entity:X": {
                "type": "entity",
                "label": "X",
                "props": {"description": ""},
            },
        }
        g = _make_graph(nodes)
        s = g.stats()
        self.assertEqual(s["nodes_with_description"], 0)
        self.assertAlmostEqual(s["description_coverage_pct"], 0.0)

    def test_none_description_not_counted(self):
        """A None description should NOT count as described."""
        nodes = {
            "entity:Y": {
                "type": "entity",
                "label": "Y",
                "props": {"description": None},
            },
        }
        g = _make_graph(nodes)
        s = g.stats()
        self.assertEqual(s["nodes_with_description"], 0)

    def test_whitespace_only_not_counted(self):
        """A whitespace-only description should NOT count as described."""
        nodes = {
            "entity:Z": {
                "type": "entity",
                "label": "Z",
                "props": {"description": "   "},
            },
        }
        g = _make_graph(nodes)
        s = g.stats()
        self.assertEqual(s["nodes_with_description"], 0)

    def test_no_props_key(self):
        """A node with no 'props' at all should not crash."""
        nodes = {
            "entity:W": {
                "type": "entity",
                "label": "W",
            },
        }
        g = _make_graph(nodes)
        s = g.stats()
        self.assertEqual(s["nodes_with_description"], 0)
        self.assertEqual(s["total_nodes"], 1)

if __name__ == "__main__":
    unittest.main()
