"""Fused canonicalization equals the legacy two-pass — how changed, not what.

The discover post-process tail used to canonicalize in two passes::

    _sort(canonical_graph({"meta": ..., "nodes": ..., "edges": ...}))

``canonical_graph`` deep-copied everything and sorted the edge list; ``_sort``
then recursively rebuilt every dict with sorted keys (preserving list order).
The deep-copy was redundant because ``_sort`` re-materialized the whole tree
anyway, so the two passes were fused into one recursive sorted-rebuild that
also sorts the edge list in the same walk.

This test pins the fusion to the legacy behavior on an adversarial fixture:
unsorted nested prop keys, edges in non-canonical insertion order, unsorted
meta keys, two edges that tie on (from, to, type) and must break on props, and
a props-less edge. The fused output must be:

1. deep-equal (``==``) to the legacy result;
2. key-order-identical when serialized WITHOUT ``sort_keys`` (the in-memory
   recursive sorted-key contract that ``weld_determinism_dict_order_test``
   guards end-to-end);
3. byte-identical on disk via ``dumps_graph``;

and the fusion must not mutate its input.
"""

from __future__ import annotations

import copy
import json
import unittest

from weld._discover_postprocess import _canonical_sorted, _sort
from weld.serializer import canonical_graph, dumps_graph


def _adversarial_graph() -> dict:
    """A graph that exercises every ordering rule the fusion must preserve."""
    return {
        # Unsorted top-level keys: nodes/meta/edges, not sorted.
        "nodes": {
            # Node ids out of lex order; entry keys out of order
            # (type/props/label, must canonicalize to label/props/type).
            "n:zeta": {
                "type": "file",
                "props": {"zoo": 1, "alpha": {"y": 2, "x": 1}, "mid": 3},
                "label": "zeta",
            },
            "n:alpha": {
                "type": "file",
                "props": {"beta": 2, "alpha": 1},
                "label": "alpha",
            },
        },
        "meta": {
            "version": 5,
            "discovered_from": ["b", "a"],
            "schema_version": 2,
        },
        "edges": [
            # Non-canonical insertion order; a later edge sorts before an
            # earlier one by (from, to, type).
            {"from": "n:zeta", "to": "n:alpha", "type": "calls",
             "props": {"z": 1, "a": 2}},
            {"from": "n:alpha", "to": "n:zeta", "type": "calls",
             "props": {"weight": 9}},
            # Tie on (from, to, type) with the next edge — must break on props
            # serialization: {"a":1} sorts before {"z":1}.
            {"from": "n:alpha", "to": "n:alpha", "type": "self",
             "props": {"z": 1}},
            {"from": "n:alpha", "to": "n:alpha", "type": "self",
             "props": {"a": 1}},
            # A props-less edge (no "props" key at all).
            {"from": "n:alpha", "to": "n:zeta", "type": "depends_on"},
        ],
    }


def _legacy(graph: dict) -> dict:
    """The pre-fusion two-pass canonicalization."""
    return _sort(canonical_graph(graph))


def _key_order_dump(obj: dict) -> str:
    """Serialize preserving in-memory key order (NO sort_keys).

    If two trees produce identical text here, every dict at every level
    carries keys in the same order — the strict in-memory contract.
    """
    return json.dumps(obj, indent=2, ensure_ascii=False)


class FusedCanonicalizationEquivalenceTest(unittest.TestCase):
    def test_fused_is_deep_equal_to_legacy(self) -> None:
        graph = _adversarial_graph()
        self.assertEqual(
            _canonical_sorted(graph), _legacy(graph),
            "fused canonicalization must be value-equal to "
            "_sort(canonical_graph(...))",
        )

    def test_fused_key_order_identical_to_legacy(self) -> None:
        """Without sort_keys, both forms must emit identical key sequences."""
        graph = _adversarial_graph()
        self.assertEqual(
            _key_order_dump(_canonical_sorted(graph)),
            _key_order_dump(_legacy(graph)),
            "fused in-memory key order diverged from the legacy two-pass; "
            "the recursive sorted-key contract is not preserved",
        )

    def test_fused_byte_identical_on_disk(self) -> None:
        graph = _adversarial_graph()
        self.assertEqual(
            dumps_graph(_canonical_sorted(graph)),
            dumps_graph(_legacy(graph)),
            "fused on-disk bytes diverged from the legacy two-pass",
        )

    def test_edges_sorted_by_adr_tuple(self) -> None:
        """The fused walk is the sole edge sort — verify the tuple order."""
        result = _canonical_sorted(_adversarial_graph())
        keys = [
            (e["from"], e["to"], e["type"],
             json.dumps(e.get("props", {}), sort_keys=True, ensure_ascii=True))
            for e in result["edges"]
        ]
        self.assertEqual(keys, sorted(keys), "edges not in ADR 0012 tuple order")

    def test_props_tie_break_preserved(self) -> None:
        """Edges tying on (from,to,type) order by props serialization."""
        result = _canonical_sorted(_adversarial_graph())
        self_edges = [e for e in result["edges"] if e["type"] == "self"]
        self.assertEqual(len(self_edges), 2)
        self.assertEqual(self_edges[0]["props"], {"a": 1})
        self.assertEqual(self_edges[1]["props"], {"z": 1})

    def test_fused_does_not_mutate_input(self) -> None:
        graph = _adversarial_graph()
        snapshot = copy.deepcopy(graph)
        _canonical_sorted(graph)
        self.assertEqual(
            graph, snapshot, "fused canonicalization mutated its input",
        )

    def test_empty_graph_keeps_contract_shape(self) -> None:
        """An input missing nodes/edges still yields both keys (ADR shape)."""
        result = _canonical_sorted({"meta": {"version": 5}})
        self.assertEqual(result.get("nodes"), {})
        self.assertEqual(result.get("edges"), [])

    def test_degenerate_inputs_match_legacy_key_order(self) -> None:
        """Inputs omitting nodes/edges must still emit top-level keys sorted.

        The defaulted ``nodes``/``edges`` must land in sorted top-level
        position exactly as the legacy ``_sort(canonical_graph(...))`` did
        (which re-sorted the top level after ``setdefault``), so the
        in-memory key-order contract holds even for partial inputs.
        """
        for graph in (
            {"meta": {"version": 5}},  # no nodes, no edges
            {"meta": {"version": 5},
             "nodes": {"n:b": {"type": "x", "label": "b", "props": {}}}},  # no edges
            {"meta": {"version": 5},
             "edges": [{"from": "a", "to": "b", "type": "calls"}]},  # no nodes
        ):
            with self.subTest(keys=sorted(graph)):
                self.assertEqual(
                    _key_order_dump(_canonical_sorted(graph)),
                    _key_order_dump(_legacy(graph)),
                    "degenerate-input top-level key order diverged from legacy",
                )

    def test_sort_only_changes_order_not_values(self) -> None:
        """``_sort`` rebuilds keys sorted but preserves list order + scalars."""
        self.assertEqual(_sort({"b": 1, "a": [3, 1, 2]}), {"a": [3, 1, 2], "b": 1})
        self.assertEqual(list(_sort({"b": 1, "a": 2}).keys()), ["a", "b"])


if __name__ == "__main__":
    unittest.main()
