"""Tests for ``weld.arch_lint_boundary`` -- boundary-enforcement rule.

The ``boundary-enforcement`` rule flags edges that cross declared layer
boundaries when no topology declaration explicitly allows the crossing.
Nodes declare their layer via ``props.layer``; allowed crossings are
declared in the ``topology.allowed_cross_layer`` section of
``discover.yaml``.
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
                    "updated_at": "2026-04-16T00:00:00+00:00",
                },
                "nodes": nodes,
                "edges": edges,
            }
        ),
        encoding="utf-8",
    )


def _write_discover_yaml(root: Path, yaml_text: str) -> None:
    d = root / ".weld"
    d.mkdir(parents=True, exist_ok=True)
    (d / "discover.yaml").write_text(yaml_text, encoding="utf-8")


def _make_node(label: str, layer: str | None = None) -> dict:
    props: dict = {"file": f"{label}.py"}
    if layer is not None:
        props["layer"] = layer
    return {"type": "file", "label": label, "props": props}


class BoundaryEnforcementRuleTest(unittest.TestCase):
    """The built-in ``boundary-enforcement`` rule."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())

    def _lint_boundary(
        self,
        nodes: dict,
        edges: list,
        yaml_text: str | None = None,
    ) -> dict:
        from weld.arch_lint import lint
        from weld.graph import Graph

        _write_graph(self.root, nodes, edges)
        if yaml_text is not None:
            _write_discover_yaml(self.root, yaml_text)
        g = Graph(self.root)
        g.load()
        return lint(g, rule_ids=["boundary-enforcement"])

    def test_same_layer_no_violation(self) -> None:
        """Edges within the same layer are always allowed."""
        nodes = {
            "file:a.py": _make_node("a", layer="api"),
            "file:b.py": _make_node("b", layer="api"),
        }
        edges = [
            {"from": "file:a.py", "to": "file:b.py",
             "type": "imports", "props": {}},
        ]
        r = self._lint_boundary(nodes, edges)
        self.assertEqual(r["violation_count"], 0)

    def test_cross_layer_without_declaration_produces_violation(self) -> None:
        """An edge crossing layers without topology allow is flagged."""
        nodes = {
            "file:api.py": _make_node("api", layer="api"),
            "file:db.py": _make_node("db", layer="internal"),
        }
        edges = [
            {"from": "file:api.py", "to": "file:db.py",
             "type": "imports", "props": {}},
        ]
        r = self._lint_boundary(nodes, edges)
        self.assertEqual(r["violation_count"], 1)
        v = r["violations"][0]
        self.assertEqual(v["rule"], "boundary-enforcement")
        self.assertEqual(v["node_id"], "file:api.py")
        self.assertIn("api", v["message"])
        self.assertIn("internal", v["message"])

    def test_cross_layer_with_allow_declaration_no_violation(self) -> None:
        """An allowed_cross_layer entry suppresses the violation."""
        nodes = {
            "file:api.py": _make_node("api", layer="api"),
            "file:db.py": _make_node("db", layer="internal"),
        }
        edges = [
            {"from": "file:api.py", "to": "file:db.py",
             "type": "imports", "props": {}},
        ]
        yaml_text = (
            "sources: []\n"
            "topology:\n"
            "  allowed_cross_layer:\n"
            "    - from: api\n"
            "      to: internal\n"
        )
        r = self._lint_boundary(nodes, edges, yaml_text=yaml_text)
        self.assertEqual(r["violation_count"], 0)

    def test_nodes_without_layer_are_skipped(self) -> None:
        """Edges involving nodes without a layer prop are not flagged."""
        nodes = {
            "file:a.py": _make_node("a"),
            "file:b.py": _make_node("b", layer="api"),
        }
        edges = [
            {"from": "file:a.py", "to": "file:b.py",
             "type": "imports", "props": {}},
        ]
        r = self._lint_boundary(nodes, edges)
        self.assertEqual(r["violation_count"], 0)

    def test_multiple_violations_deterministic_order(self) -> None:
        """Violations are sorted for stable CI diffs."""
        nodes = {
            "file:z.py": _make_node("z", layer="api"),
            "file:a.py": _make_node("a", layer="api"),
            "file:int.py": _make_node("int", layer="internal"),
        }
        edges = [
            {"from": "file:z.py", "to": "file:int.py",
             "type": "imports", "props": {}},
            {"from": "file:a.py", "to": "file:int.py",
             "type": "imports", "props": {}},
        ]
        r = self._lint_boundary(nodes, edges)
        self.assertEqual(r["violation_count"], 2)
        ids = [v["node_id"] for v in r["violations"]]
        self.assertEqual(ids, sorted(ids))

    def test_allow_is_directional(self) -> None:
        """Allow from api->internal does not allow internal->api."""
        nodes = {
            "file:api.py": _make_node("api", layer="api"),
            "file:int.py": _make_node("int", layer="internal"),
        }
        edges = [
            {"from": "file:int.py", "to": "file:api.py",
             "type": "imports", "props": {}},
        ]
        yaml_text = (
            "sources: []\n"
            "topology:\n"
            "  allowed_cross_layer:\n"
            "    - from: api\n"
            "      to: internal\n"
        )
        r = self._lint_boundary(nodes, edges, yaml_text=yaml_text)
        self.assertEqual(r["violation_count"], 1)

    def test_wildcard_allow(self) -> None:
        """A wildcard '*' in from or to matches any layer."""
        nodes = {
            "file:api.py": _make_node("api", layer="api"),
            "file:db.py": _make_node("db", layer="internal"),
        }
        edges = [
            {"from": "file:api.py", "to": "file:db.py",
             "type": "imports", "props": {}},
        ]
        yaml_text = (
            "sources: []\n"
            "topology:\n"
            "  allowed_cross_layer:\n"
            "    - from: '*'\n"
            "      to: internal\n"
        )
        r = self._lint_boundary(nodes, edges, yaml_text=yaml_text)
        self.assertEqual(r["violation_count"], 0)

    def test_no_discover_yaml_still_flags_crossings(self) -> None:
        """Without discover.yaml, all cross-layer edges are flagged."""
        nodes = {
            "file:api.py": _make_node("api", layer="api"),
            "file:int.py": _make_node("int", layer="internal"),
        }
        edges = [
            {"from": "file:api.py", "to": "file:int.py",
             "type": "imports", "props": {}},
        ]
        r = self._lint_boundary(nodes, edges)
        self.assertEqual(r["violation_count"], 1)

    def test_listed_in_available_rules(self) -> None:
        from weld.arch_lint import available_rule_ids

        self.assertIn("boundary-enforcement", available_rule_ids())

    def test_violation_severity_is_warning(self) -> None:
        """Boundary violations are warnings, not errors."""
        nodes = {
            "file:api.py": _make_node("api", layer="api"),
            "file:int.py": _make_node("int", layer="internal"),
        }
        edges = [
            {"from": "file:api.py", "to": "file:int.py",
             "type": "imports", "props": {}},
        ]
        r = self._lint_boundary(nodes, edges)
        self.assertEqual(r["violations"][0]["severity"], "warning")

    def test_edge_type_filter_in_allow(self) -> None:
        """Allow entries can restrict by edge type."""
        nodes = {
            "file:api.py": _make_node("api", layer="api"),
            "file:int.py": _make_node("int", layer="internal"),
        }
        edges = [
            {"from": "file:api.py", "to": "file:int.py",
             "type": "imports", "props": {}},
            {"from": "file:api.py", "to": "file:int.py",
             "type": "calls", "props": {}},
        ]
        yaml_text = (
            "sources: []\n"
            "topology:\n"
            "  allowed_cross_layer:\n"
            "    - from: api\n"
            "      to: internal\n"
            "      edge_type: imports\n"
        )
        r = self._lint_boundary(nodes, edges, yaml_text=yaml_text)
        # 'imports' is allowed, 'calls' is not
        self.assertEqual(r["violation_count"], 1)
        self.assertIn("calls", r["violations"][0]["message"])


if __name__ == "__main__":
    unittest.main()
