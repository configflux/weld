"""Tests for ``weld._graph_contract_check`` (bd 5038-rhuc).

Hermetic half of the two-file split ``weld_graph_edge_provenance_lint_test.py``
established for this shape (ADR 0074 sixth amendment, bd whnwb): pin the
checker's own behavior cheaply against hand-built fixtures. Only a real
discovery of this repo (``weld_node_edge_contract_repo_test.py``) can prove
the checker catches the *next* strategy that regresses the contract; these
fixtures prove the checker's own branch logic -- attribution, discrimination,
and the exact rgru shape (an out-of-vocabulary ``roles`` entry) -- in
isolation from any real strategy.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld._graph_contract_check import ContractViolation, check_node_edge_contract


def _good_node(strategy: str = "widget_strategy") -> dict:
    return {
        "type": "concept",
        "label": "Widget",
        "props": {"source_strategy": strategy, "roles": ["implementation"]},
    }


def _good_edge(from_id: str, to_id: str, strategy: str = "widget_strategy") -> dict:
    return {
        "from": from_id,
        "to": to_id,
        "type": "relates_to",
        "props": {"source_strategy": strategy},
    }


class ContractCheckTest(unittest.TestCase):
    def _check(self, nodes: dict, edges: list) -> list[ContractViolation]:
        return list(check_node_edge_contract(nodes, edges))

    def test_well_formed_graph_yields_no_violations(self) -> None:
        nodes = {"concept:a": _good_node(), "concept:b": _good_node()}
        edges = [_good_edge("concept:a", "concept:b")]
        self.assertEqual([], self._check(nodes, edges))

    def test_out_of_vocabulary_role_is_flagged_with_strategy_and_field(self) -> None:
        """The rgru shape: a strategy stamps a role the contract rejects."""
        bad = _good_node(strategy="fake_strategy")
        bad["props"]["roles"] = ["not_a_real_role"]
        violations = self._check({"concept:bad": bad}, [])
        self.assertEqual(1, len(violations))
        v = violations[0]
        self.assertEqual("fake_strategy", v.source_strategy)
        self.assertEqual("node", v.kind)
        self.assertEqual("concept:bad", v.subject_id)
        self.assertEqual("props.roles", v.field)
        self.assertIn("not_a_real_role", v.message)

    def test_missing_required_field_is_flagged(self) -> None:
        bad = {"label": "no type", "props": {"source_strategy": "fake_strategy"}}
        violations = self._check({"concept:bad": bad}, [])
        self.assertEqual(1, len(violations))
        self.assertEqual("type", violations[0].field)

    def test_invalid_node_type_is_flagged(self) -> None:
        bad = _good_node(strategy="fake_strategy")
        bad["type"] = "not_a_real_type"
        violations = self._check({"concept:bad": bad}, [])
        self.assertEqual(1, len(violations))
        self.assertEqual("type", violations[0].field)
        self.assertEqual("fake_strategy", violations[0].source_strategy)

    def test_edge_dangling_reference_is_flagged_with_edge_strategy(self) -> None:
        nodes = {"concept:a": _good_node()}
        edges = [_good_edge("concept:a", "concept:ghost", strategy="fake_strategy")]
        violations = self._check(nodes, edges)
        self.assertEqual(1, len(violations))
        v = violations[0]
        self.assertEqual("fake_strategy", v.source_strategy)
        self.assertEqual("edge", v.kind)
        self.assertEqual("to", v.field)
        self.assertIn("concept:a -> concept:ghost", v.subject_id)

    def test_invalid_edge_type_is_flagged(self) -> None:
        nodes = {"concept:a": _good_node(), "concept:b": _good_node()}
        edge = _good_edge("concept:a", "concept:b", strategy="fake_strategy")
        edge["type"] = "not_a_real_edge_type"
        violations = self._check(nodes, [edge])
        self.assertEqual(1, len(violations))
        self.assertEqual("type", violations[0].field)

    def test_missing_source_strategy_reports_unknown_placeholder(self) -> None:
        """Attribution degrades to a visible placeholder, never a crash."""
        bad = {"type": "not_a_real_type", "label": "x", "props": {}}
        violations = self._check({"concept:bad": bad}, [])
        self.assertEqual(1, len(violations))
        self.assertEqual("<unknown>", violations[0].source_strategy)

    def test_non_mapping_node_value_is_flagged_not_crashed(self) -> None:
        """The most malformed case (None, a bare int) must report, not raise."""
        violations = self._check({"concept:bad": None}, [])
        self.assertEqual(1, len(violations))
        self.assertEqual("<unknown>", violations[0].source_strategy)
        self.assertEqual("concept:bad", violations[0].subject_id)

    def test_non_mapping_edge_value_is_flagged_not_skipped(self) -> None:
        """A garbage edge entry must surface, not vanish silently."""
        nodes = {"concept:a": _good_node()}
        violations = self._check(nodes, [None])
        self.assertEqual(1, len(violations))
        self.assertEqual("edge", violations[0].kind)

    def test_only_the_bad_node_is_flagged_among_several(self) -> None:
        """Discrimination: good nodes must not add noise."""
        bad = _good_node(strategy="fake_strategy")
        bad["props"]["roles"] = ["not_a_real_role"]
        nodes = {
            "concept:good_one": _good_node(),
            "concept:bad": bad,
            "concept:good_two": _good_node(),
        }
        violations = self._check(nodes, [])
        self.assertEqual(1, len(violations))
        self.assertEqual("concept:bad", violations[0].subject_id)

    def test_real_python_package_strategy_output_passes_the_contract(self) -> None:
        """Generalizes rgru's per-strategy pin: real strategy output, shared
        checker, zero manual per-strategy wiring required.

        Nodes only, no edges -- like rgru's own ``test_emitted_nodes_
        satisfy_the_contract``. ``python_package`` run in isolation emits
        ``contains`` edges to ``file:`` nodes that only ``python_module``
        (a sibling strategy over the same glob) would mint; checking those
        edges' referential integrity here would flag a fixture artifact,
        not a real defect. Edge referential integrity is a whole-graph
        property and is exactly what the merged-strategy real discovery in
        weld_node_edge_contract_repo_test.py covers.
        """
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "mypkg").mkdir()
            (root / "mypkg" / "__init__.py").write_text("", encoding="utf-8")
            (root / "mypkg" / "alpha.py").write_text("X = 1\n", encoding="utf-8")
            from weld.strategies.python_package import extract

            result = extract(root, {"glob": "mypkg/*.py"}, {})
        self.assertTrue(result.nodes, "fixture must emit at least one node")
        violations = self._check(result.nodes, [])
        self.assertEqual(
            [], violations,
            f"real python_package output violates the contract: "
            f"{[str(v) for v in violations]}",
        )


if __name__ == "__main__":
    unittest.main()
