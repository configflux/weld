"""Tests for the ``top_authority_nodes`` field in :meth:`weld.graph.Graph.stats`.

PM audit (tracked issue) wants ``wd stats`` to surface the graph's
most-connected nodes so reviewers can see at a glance what the high-signal
hubs look like. "Authority" here is the simple, explainable metric: total
degree (in_degree + out_degree), ties broken by node id for determinism.

These tests pin:

- Empty graphs return an empty list (no division or sort errors).
- Ranking is by total degree descending.
- Ties break deterministically by id ascending.
- The list is capped at five entries so the output stays compact.
- Each entry carries id / label / type / degree / in_degree / out_degree
  so CLI consumers can format compact output without rejoining against
  the full graph.
- Existing stats keys remain intact (additive change only).
"""

from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.graph import Graph  # noqa: E402


def _make_graph(nodes: dict, edges: list | None = None) -> Graph:
    tmp = tempfile.mkdtemp()
    g = Graph(Path(tmp))
    g._data = {
        "meta": {"version": 1},
        "nodes": copy.deepcopy(nodes),
        "edges": copy.deepcopy(edges) if edges else [],
    }
    g._build_inverted_index()
    return g


class TestTopAuthorityEmptyGraph(unittest.TestCase):
    def test_empty_graph_has_empty_top_list(self) -> None:
        g = _make_graph({})
        s = g.stats()
        self.assertIn("top_authority_nodes", s)
        self.assertEqual(s["top_authority_nodes"], [])


class TestTopAuthorityRanking(unittest.TestCase):
    def setUp(self) -> None:
        self.nodes = {
            "entity:Store": {"type": "entity", "label": "Store", "props": {}},
            "entity:Product": {"type": "entity", "label": "Product", "props": {}},
            "entity:Order": {"type": "entity", "label": "Order", "props": {}},
            "entity:Customer": {"type": "entity", "label": "Customer", "props": {}},
            "file:main.py": {"type": "file", "label": "main.py", "props": {"file": "main.py"}},
        }
        # Store has degree 4 (3 in + 1 out), Product 2, Order 2, Customer 1, file 1.
        self.edges = [
            {"from": "entity:Product", "to": "entity:Store", "type": "depends_on", "props": {}},
            {"from": "entity:Order", "to": "entity:Store", "type": "depends_on", "props": {}},
            {"from": "entity:Customer", "to": "entity:Store", "type": "depends_on", "props": {}},
            {"from": "entity:Store", "to": "entity:Product", "type": "depends_on", "props": {}},
            {"from": "file:main.py", "to": "entity:Order", "type": "imports", "props": {}},
        ]

    def test_ranked_by_total_degree_desc(self) -> None:
        g = _make_graph(self.nodes, self.edges)
        top = g.stats()["top_authority_nodes"]
        self.assertGreater(len(top), 0)
        ids = [entry["id"] for entry in top]
        self.assertEqual(ids[0], "entity:Store")
        degrees = [entry["degree"] for entry in top]
        self.assertEqual(degrees, sorted(degrees, reverse=True))

    def test_entry_shape(self) -> None:
        g = _make_graph(self.nodes, self.edges)
        top = g.stats()["top_authority_nodes"]
        first = top[0]
        self.assertEqual(first["id"], "entity:Store")
        self.assertEqual(first["label"], "Store")
        self.assertEqual(first["type"], "entity")
        # Store: 3 incoming + 1 outgoing = 4.
        self.assertEqual(first["in_degree"], 3)
        self.assertEqual(first["out_degree"], 1)
        self.assertEqual(first["degree"], 4)


class TestTopAuthorityDeterministicTieBreak(unittest.TestCase):
    def test_ties_broken_by_id_ascending(self) -> None:
        # All nodes share degree 1; expect alphabetical id order.
        nodes = {
            "entity:Zeta": {"type": "entity", "label": "Zeta", "props": {}},
            "entity:Alpha": {"type": "entity", "label": "Alpha", "props": {}},
            "entity:Mu": {"type": "entity", "label": "Mu", "props": {}},
        }
        edges = [
            {"from": "entity:Zeta", "to": "entity:Alpha", "type": "depends_on", "props": {}},
            {"from": "entity:Alpha", "to": "entity:Mu", "type": "depends_on", "props": {}},
            {"from": "entity:Mu", "to": "entity:Zeta", "type": "depends_on", "props": {}},
        ]
        g = _make_graph(nodes, edges)
        top = g.stats()["top_authority_nodes"]
        # Each node has degree 2 here (one in, one out) -> pure tie.
        ids = [entry["id"] for entry in top]
        self.assertEqual(ids, ["entity:Alpha", "entity:Mu", "entity:Zeta"])


class TestTopAuthorityLimit(unittest.TestCase):
    def test_capped_at_five(self) -> None:
        nodes = {
            f"entity:N{i}": {"type": "entity", "label": f"N{i}", "props": {}}
            for i in range(10)
        }
        # Give each node a unique degree by chaining edges of varying counts.
        edges: list[dict] = []
        for i in range(10):
            for _ in range(i + 1):
                # self-adjacent edges are not realistic, so point to N0.
                if i == 0:
                    continue
                edges.append({
                    "from": f"entity:N{i}", "to": "entity:N0",
                    "type": "depends_on", "props": {},
                })
        g = _make_graph(nodes, edges)
        top = g.stats()["top_authority_nodes"]
        self.assertLessEqual(len(top), 5)


class TestTopAuthorityBackwardCompat(unittest.TestCase):
    def test_existing_keys_preserved(self) -> None:
        g = _make_graph({})
        s = g.stats()
        for key in (
            "total_nodes",
            "total_edges",
            "nodes_by_type",
            "edges_by_type",
            "nodes_with_description",
            "description_coverage_pct",
            "description_coverage_by_type",
        ):
            self.assertIn(key, s)


class TestTopAuthorityIgnoresDanglingEdges(unittest.TestCase):
    """Edges pointing to nodes that are not in the graph should not crash.

    The graph validator rejects dangling edges but :meth:`Graph.stats` must
    still be robust to partially loaded or hand-edited payloads because it
    is a read-only, diagnostic command.
    """

    def test_dangling_edge_does_not_crash(self) -> None:
        nodes = {
            "entity:Only": {"type": "entity", "label": "Only", "props": {}},
        }
        edges = [
            {"from": "entity:Only", "to": "entity:Missing", "type": "depends_on", "props": {}},
        ]
        g = _make_graph(nodes, edges)
        top = g.stats()["top_authority_nodes"]
        ids = [entry["id"] for entry in top]
        self.assertIn("entity:Only", ids)
        # Missing node is skipped (no entry), not fabricated.
        self.assertNotIn("entity:Missing", ids)


if __name__ == "__main__":
    unittest.main()
