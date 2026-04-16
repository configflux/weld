"""Tests for ``weld.arch_lint_cycles`` -- no-circular-deps rule.

Verifies Tarjan's SCC algorithm detects cycles in the graph and reports
each non-trivial strongly connected component as a violation on the
cycle's lowest-sorted node id.
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

from weld.contract import SCHEMA_VERSION  # noqa: E402


def _write_graph(root: Path, nodes: dict, edges: list) -> None:
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    (root / ".weld" / "graph.json").write_text(
        json.dumps(
            {
                "meta": {
                    "version": SCHEMA_VERSION,
                    "git_sha": "deadbeef",
                    "updated_at": "2026-04-15T00:00:00+00:00",
                },
                "nodes": nodes,
                "edges": edges,
            }
        ),
        encoding="utf-8",
    )


# ---- Graph fixtures -------------------------------------------------------

def _acyclic_graph() -> tuple[dict, list]:
    """A -> B -> C, no cycles."""
    nodes = {
        "file:a.py": {"type": "file", "label": "a.py", "props": {}},
        "file:b.py": {"type": "file", "label": "b.py", "props": {}},
        "file:c.py": {"type": "file", "label": "c.py", "props": {}},
    }
    edges = [
        {"from": "file:a.py", "to": "file:b.py", "type": "imports", "props": {}},
        {"from": "file:b.py", "to": "file:c.py", "type": "imports", "props": {}},
    ]
    return nodes, edges


def _simple_cycle() -> tuple[dict, list]:
    """A -> B -> A  (2-node cycle)."""
    nodes = {
        "file:a.py": {"type": "file", "label": "a.py", "props": {}},
        "file:b.py": {"type": "file", "label": "b.py", "props": {}},
    }
    edges = [
        {"from": "file:a.py", "to": "file:b.py", "type": "imports", "props": {}},
        {"from": "file:b.py", "to": "file:a.py", "type": "imports", "props": {}},
    ]
    return nodes, edges


def _self_loop() -> tuple[dict, list]:
    """A -> A  (self-loop counts as a cycle)."""
    nodes = {
        "file:a.py": {"type": "file", "label": "a.py", "props": {}},
    }
    edges = [
        {"from": "file:a.py", "to": "file:a.py", "type": "imports", "props": {}},
    ]
    return nodes, edges


def _two_separate_cycles() -> tuple[dict, list]:
    """A -> B -> A  and  C -> D -> C  (two independent cycles)."""
    nodes = {
        "file:a.py": {"type": "file", "label": "a.py", "props": {}},
        "file:b.py": {"type": "file", "label": "b.py", "props": {}},
        "file:c.py": {"type": "file", "label": "c.py", "props": {}},
        "file:d.py": {"type": "file", "label": "d.py", "props": {}},
    }
    edges = [
        {"from": "file:a.py", "to": "file:b.py", "type": "imports", "props": {}},
        {"from": "file:b.py", "to": "file:a.py", "type": "imports", "props": {}},
        {"from": "file:c.py", "to": "file:d.py", "type": "imports", "props": {}},
        {"from": "file:d.py", "to": "file:c.py", "type": "imports", "props": {}},
    ]
    return nodes, edges


def _three_node_cycle() -> tuple[dict, list]:
    """A -> B -> C -> A  (3-node cycle)."""
    nodes = {
        "file:a.py": {"type": "file", "label": "a.py", "props": {}},
        "file:b.py": {"type": "file", "label": "b.py", "props": {}},
        "file:c.py": {"type": "file", "label": "c.py", "props": {}},
    }
    edges = [
        {"from": "file:a.py", "to": "file:b.py", "type": "imports", "props": {}},
        {"from": "file:b.py", "to": "file:c.py", "type": "imports", "props": {}},
        {"from": "file:c.py", "to": "file:a.py", "type": "imports", "props": {}},
    ]
    return nodes, edges


# ---- Unit tests for the SCC function directly -----------------------------

class TarjanSCCTest(unittest.TestCase):
    """Direct tests of the ``find_cycles`` function."""

    def test_acyclic_returns_empty(self) -> None:
        from weld.arch_lint_cycles import find_cycles
        nodes, edges = _acyclic_graph()
        data = {"nodes": nodes, "edges": edges}
        self.assertEqual(find_cycles(data), [])

    def test_simple_cycle_detected(self) -> None:
        from weld.arch_lint_cycles import find_cycles
        nodes, edges = _simple_cycle()
        data = {"nodes": nodes, "edges": edges}
        sccs = find_cycles(data)
        self.assertEqual(len(sccs), 1)
        self.assertEqual(sorted(sccs[0]), ["file:a.py", "file:b.py"])

    def test_self_loop_detected(self) -> None:
        from weld.arch_lint_cycles import find_cycles
        nodes, edges = _self_loop()
        data = {"nodes": nodes, "edges": edges}
        sccs = find_cycles(data)
        self.assertEqual(len(sccs), 1)
        self.assertEqual(sccs[0], ["file:a.py"])

    def test_two_separate_cycles(self) -> None:
        from weld.arch_lint_cycles import find_cycles
        nodes, edges = _two_separate_cycles()
        data = {"nodes": nodes, "edges": edges}
        sccs = find_cycles(data)
        self.assertEqual(len(sccs), 2)
        sorted_sccs = sorted(sccs, key=lambda s: s[0])
        self.assertEqual(sorted(sorted_sccs[0]), ["file:a.py", "file:b.py"])
        self.assertEqual(sorted(sorted_sccs[1]), ["file:c.py", "file:d.py"])

    def test_three_node_cycle(self) -> None:
        from weld.arch_lint_cycles import find_cycles
        nodes, edges = _three_node_cycle()
        data = {"nodes": nodes, "edges": edges}
        sccs = find_cycles(data)
        self.assertEqual(len(sccs), 1)
        self.assertEqual(
            sorted(sccs[0]),
            ["file:a.py", "file:b.py", "file:c.py"],
        )

    def test_empty_graph(self) -> None:
        from weld.arch_lint_cycles import find_cycles
        self.assertEqual(find_cycles({"nodes": {}, "edges": []}), [])

    def test_nodes_only_no_edges(self) -> None:
        from weld.arch_lint_cycles import find_cycles
        data = {
            "nodes": {"file:a.py": {}, "file:b.py": {}},
            "edges": [],
        }
        self.assertEqual(find_cycles(data), [])

    def test_edges_with_missing_nodes_ignored(self) -> None:
        """Edges referencing non-existent nodes should not crash."""
        from weld.arch_lint_cycles import find_cycles
        data = {
            "nodes": {"file:a.py": {}},
            "edges": [
                {"from": "file:a.py", "to": "file:ghost.py",
                 "type": "imports", "props": {}},
                {"from": "file:ghost.py", "to": "file:a.py",
                 "type": "imports", "props": {}},
            ],
        }
        # ghost.py is not in nodes, so the edge targets are not graph
        # nodes. The algorithm should handle this gracefully.
        sccs = find_cycles(data)
        # Whether this is a cycle depends on whether ghost is included;
        # the key contract is no crash.
        self.assertIsInstance(sccs, list)


# ---- Integration: rule wired through lint() --------------------------------

class NoCircularDepsRuleTest(unittest.TestCase):
    """Integration tests via the ``lint()`` runner."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())

    def _lint_cycles(self, nodes: dict, edges: list) -> dict:
        from weld.arch_lint import lint
        from weld.graph import Graph
        _write_graph(self.root, nodes, edges)
        g = Graph(self.root)
        g.load()
        return lint(g, rule_ids=["no-circular-deps"])

    def test_acyclic_graph_clean(self) -> None:
        r = self._lint_cycles(*_acyclic_graph())
        self.assertEqual(r["violation_count"], 0)
        self.assertIn("no-circular-deps", r["rules_run"])

    def test_simple_cycle_reported(self) -> None:
        r = self._lint_cycles(*_simple_cycle())
        self.assertEqual(r["violation_count"], 1)
        v = r["violations"][0]
        self.assertEqual(v["rule"], "no-circular-deps")
        # Violation anchored on the lowest-sorted node in the SCC.
        self.assertEqual(v["node_id"], "file:a.py")
        self.assertIn("file:b.py", v["message"])

    def test_self_loop_reported(self) -> None:
        r = self._lint_cycles(*_self_loop())
        self.assertEqual(r["violation_count"], 1)
        v = r["violations"][0]
        self.assertEqual(v["rule"], "no-circular-deps")
        self.assertEqual(v["node_id"], "file:a.py")

    def test_two_cycles_two_violations(self) -> None:
        r = self._lint_cycles(*_two_separate_cycles())
        self.assertEqual(r["violation_count"], 2)
        ids = sorted(v["node_id"] for v in r["violations"])
        self.assertEqual(ids, ["file:a.py", "file:c.py"])

    def test_three_node_cycle_anchored_on_lowest(self) -> None:
        r = self._lint_cycles(*_three_node_cycle())
        self.assertEqual(r["violation_count"], 1)
        v = r["violations"][0]
        self.assertEqual(v["node_id"], "file:a.py")
        # All three nodes mentioned in the message.
        for nid in ("file:a.py", "file:b.py", "file:c.py"):
            self.assertIn(nid, v["message"])

    def test_rule_listed_in_available_rules(self) -> None:
        from weld.arch_lint import available_rule_ids
        self.assertIn("no-circular-deps", available_rule_ids())

    def test_violations_deterministic_order(self) -> None:
        """Multiple cycles produce violations sorted by node_id."""
        r = self._lint_cycles(*_two_separate_cycles())
        ids = [v["node_id"] for v in r["violations"]]
        self.assertEqual(ids, sorted(ids))


if __name__ == "__main__":
    unittest.main()
