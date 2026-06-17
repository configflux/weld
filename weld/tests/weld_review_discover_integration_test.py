"""Re-discovery honors review-state (ADR 0055).

When ``wd discover`` runs, the post-processing step must:

* drop any edge whose review-state decision is ``rejected``;
* promote ``speculative`` -> ``definite`` for any edge with ``accepted``;
* leave all other edges untouched.

This test exercises the post-processing entry point that the discover
pipeline calls.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


from weld._discover_postprocess import post_process  # noqa: E402
from weld._review import accept_edge, mint_edge_id, reject_edge  # noqa: E402
from weld.graph import Graph  # noqa: E402


def _seed_state_with_decisions(root: Path, edge: dict, decision: str) -> None:
    """Seed review-state by routing through the public accept/reject."""
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    g = Graph(root)
    g.load()
    g.add_node(edge["from"], "symbol", "from", {})
    g.add_node(edge["to"], "symbol", "to", {})
    g.add_edge(edge["from"], edge["to"], edge["type"], edge["props"])
    g.save()
    eid = mint_edge_id(edge)
    if decision == "accepted":
        accept_edge(root, eid, reason="ok", reviewer="me")
    else:
        reject_edge(root, eid, reason="bad", reviewer="me")


class PostProcessAppliesReviewStateTest(unittest.TestCase):
    """Verify post_process drops rejected and promotes accepted edges."""

    def test_rejected_edges_are_dropped_at_post_process(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            edge = {
                "from": "symbol:a", "to": "symbol:b", "type": "calls",
                "props": {
                    "source_strategy": "anthropic_enrichment",
                    "confidence": "speculative",
                },
            }
            _seed_state_with_decisions(root, edge, "rejected")
            # Simulate the discover pipeline re-emitting the same edge.
            nodes = {
                "symbol:a": {"type": "symbol", "label": "a", "props": {}},
                "symbol:b": {"type": "symbol", "label": "b", "props": {}},
            }
            edges = [dict(edge, props=dict(edge["props"]))]
            graph = post_process(nodes, edges, {}, {}, root, [])
            # Edge should be absent from the new graph.
            self.assertEqual(graph["edges"], [])

    def test_accepted_edges_are_promoted_at_post_process(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            edge = {
                "from": "symbol:a", "to": "symbol:b", "type": "calls",
                "props": {
                    "source_strategy": "anthropic_enrichment",
                    "confidence": "speculative",
                },
            }
            _seed_state_with_decisions(root, edge, "accepted")
            nodes = {
                "symbol:a": {"type": "symbol", "label": "a", "props": {}},
                "symbol:b": {"type": "symbol", "label": "b", "props": {}},
            }
            edges = [dict(edge, props=dict(edge["props"]))]
            graph = post_process(nodes, edges, {}, {}, root, [])
            self.assertEqual(len(graph["edges"]), 1)
            self.assertEqual(
                graph["edges"][0]["props"]["confidence"], "definite",
            )


if __name__ == "__main__":
    unittest.main()
