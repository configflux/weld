"""Tests for the ``no-circular-deps`` non-structural edge-type exclusion.

Split out of ``weld_arch_lint_cycles_test.py`` to keep both files under the
line-count cap (that file pins the base Tarjan SCC algorithm and the rule's
plain wiring; this one pins bd 5038-ojg27's fix: SCCs formed only by
:data:`weld.arch_lint_cycles.NON_STRUCTURAL_EDGE_TYPES` -- ``relates_to``,
``documents``, ``validates``, ``calls``, ``decorates``, ``references`` --
are no longer reported as ``no-circular-deps`` violations, while a cycle
that closes through even one genuine structural edge (``depends_on``,
``contains``, ...) still is, regardless of which node types sit on it.

Each fixture below mirrors a shape actually measured on this repo's own
graph during ojg27's investigation (see the bd issue and the module
docstring in ``weld.arch_lint_cycles`` for the full evidence): doc-doc
prose cross-references, symbol-level recursion, and the doc<->file
``documents``/``validates`` governance round-trip that was gluing an
unrelated real ``tools/*`` cycle onto a documentation cluster.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from weld.contract import SCHEMA_VERSION


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


# ---- find_cycles(): direct exclude_edge_types unit coverage ---------------

class FindCyclesExcludeEdgeTypesTest(unittest.TestCase):
    """``exclude_edge_types`` is a pure adjacency-list filter, opt-in."""

    def _relates_to_pair(self) -> dict:
        return {
            "nodes": {
                "doc:a": {"type": "doc", "label": "a", "props": {}},
                "doc:b": {"type": "doc", "label": "b", "props": {}},
            },
            "edges": [
                {"from": "doc:a", "to": "doc:b", "type": "relates_to", "props": {}},
                {"from": "doc:b", "to": "doc:a", "type": "relates_to", "props": {}},
            ],
        }

    def test_default_excludes_nothing(self) -> None:
        from weld.arch_lint_cycles import find_cycles
        sccs = find_cycles(self._relates_to_pair())
        self.assertEqual(len(sccs), 1)
        self.assertEqual(sorted(sccs[0]), ["doc:a", "doc:b"])

    def test_excluded_type_drops_the_edge(self) -> None:
        from weld.arch_lint_cycles import find_cycles
        sccs = find_cycles(
            self._relates_to_pair(), exclude_edge_types=frozenset({"relates_to"})
        )
        self.assertEqual(sccs, [])

    def test_excluding_one_type_leaves_another_cycle_intact(self) -> None:
        from weld.arch_lint_cycles import find_cycles
        data = self._relates_to_pair()
        data["nodes"]["file:x.py"] = {"type": "file", "label": "x.py", "props": {}}
        data["nodes"]["file:y.py"] = {"type": "file", "label": "y.py", "props": {}}
        data["edges"].extend([
            {"from": "file:x.py", "to": "file:y.py", "type": "depends_on", "props": {}},
            {"from": "file:y.py", "to": "file:x.py", "type": "depends_on", "props": {}},
        ])
        sccs = find_cycles(data, exclude_edge_types=frozenset({"relates_to"}))
        self.assertEqual(len(sccs), 1)
        self.assertEqual(sorted(sccs[0]), ["file:x.py", "file:y.py"])


# ---- rule_no_circular_deps(): the exclusion set the rule actually ships ---

class NonStructuralEdgeTypesConstantTest(unittest.TestCase):
    """Pin the exact set so an accidental edit is a visible, reviewed diff."""

    def test_pinned_contents(self) -> None:
        from weld.arch_lint_cycles import NON_STRUCTURAL_EDGE_TYPES
        self.assertEqual(
            NON_STRUCTURAL_EDGE_TYPES,
            frozenset({
                "relates_to", "documents", "validates",
                "calls", "decorates", "references",
            }),
        )


class NoCircularDepsExclusionRuleTest(unittest.TestCase):
    """Integration tests via the ``lint()`` runner, real ``Graph`` load."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())

    def _lint_cycles(self, nodes: dict, edges: list) -> dict:
        from weld.arch_lint import lint
        from weld.graph import Graph
        _write_graph(self.root, nodes, edges)
        g = Graph(self.root)
        g.load()
        return lint(g, rule_ids=["no-circular-deps"])

    def test_doc_doc_relates_to_cycle_not_reported(self) -> None:
        """[text](other.md) both ways -- the bug this issue reports."""
        nodes = {
            "doc:a": {"type": "doc", "label": "a", "props": {}},
            "doc:b": {"type": "doc", "label": "b", "props": {}},
        }
        edges = [
            {"from": "doc:a", "to": "doc:b", "type": "relates_to", "props": {}},
            {"from": "doc:b", "to": "doc:a", "type": "relates_to", "props": {}},
        ]
        r = self._lint_cycles(nodes, edges)
        self.assertEqual(r["violation_count"], 0)

    def test_symbol_self_recursion_not_reported(self) -> None:
        """A function calling itself is ordinary recursion."""
        nodes = {"symbol:f": {"type": "symbol", "label": "f", "props": {}}}
        edges = [
            {"from": "symbol:f", "to": "symbol:f", "type": "calls", "props": {}},
        ]
        r = self._lint_cycles(nodes, edges)
        self.assertEqual(r["violation_count"], 0)

    def test_symbol_mutual_recursion_not_reported(self) -> None:
        """Two functions calling each other (recursive-descent shape)."""
        nodes = {
            "symbol:a": {"type": "symbol", "label": "a", "props": {}},
            "symbol:b": {"type": "symbol", "label": "b", "props": {}},
        }
        edges = [
            {"from": "symbol:a", "to": "symbol:b", "type": "calls", "props": {}},
            {"from": "symbol:b", "to": "symbol:a", "type": "calls", "props": {}},
        ]
        r = self._lint_cycles(nodes, edges)
        self.assertEqual(r["violation_count"], 0)

    def test_doc_file_governance_bridge_not_reported(self) -> None:
        """doc cites a script in prose; the script asserts it checks that
        doc. Two different relationship kinds crossing paths, not a
        dependency -- the ojg27 79-member mixed-SCC finding, minimized."""
        nodes = {
            "doc:runtime-validation": {"type": "doc", "label": "rv", "props": {}},
            "file:tools/checker.py": {"type": "file", "label": "checker.py", "props": {}},
        }
        edges = [
            {"from": "doc:runtime-validation", "to": "file:tools/checker.py",
             "type": "documents", "props": {}},
            {"from": "file:tools/checker.py", "to": "doc:runtime-validation",
             "type": "validates", "props": {}},
        ]
        r = self._lint_cycles(nodes, edges)
        self.assertEqual(r["violation_count"], 0)

    def test_real_structural_cycle_still_reported(self) -> None:
        """Sabotage check: a plain depends_on cycle between two files is a
        real structural cycle and must still flag after the exclusion."""
        nodes = {
            "file:a.py": {"type": "file", "label": "a.py", "props": {}},
            "file:b.py": {"type": "file", "label": "b.py", "props": {}},
        }
        edges = [
            {"from": "file:a.py", "to": "file:b.py", "type": "depends_on", "props": {}},
            {"from": "file:b.py", "to": "file:a.py", "type": "depends_on", "props": {}},
        ]
        r = self._lint_cycles(nodes, edges)
        self.assertEqual(r["violation_count"], 1)
        self.assertEqual(r["violations"][0]["node_id"], "file:a.py")

    def test_doc_code_cycle_via_structural_edge_still_reported(self) -> None:
        """A ``doc:`` node also sits on a genuinely structural round trip
        (``depends_on`` both ways) -- an excluded ``documents`` edge rides
        alongside one leg, same two endpoints, and must not matter. The
        exclusion is edge-type-scoped, not a blanket doc-node exemption:
        a REAL doc->code->doc cycle, if the structural edges existed,
        would still be reportable."""
        nodes = {
            "doc:a": {"type": "doc", "label": "a", "props": {}},
            "file:b.py": {"type": "file", "label": "b.py", "props": {}},
        }
        edges = [
            # Excluded -- must not be what makes this a cycle.
            {"from": "doc:a", "to": "file:b.py", "type": "documents", "props": {}},
            # Structural round trip -- this is what must make it a cycle.
            {"from": "doc:a", "to": "file:b.py", "type": "depends_on", "props": {}},
            {"from": "file:b.py", "to": "doc:a", "type": "depends_on", "props": {}},
        ]
        r = self._lint_cycles(nodes, edges)
        self.assertEqual(r["violation_count"], 1)
        v = r["violations"][0]
        self.assertIn("doc:a", v["message"])
        self.assertIn("file:b.py", v["message"])


if __name__ == "__main__":
    unittest.main()
