"""Decision propagation (ADR 0055): accept promotes, reject drops, reset reverts.

These tests exercise the in-memory side of the review queue: given a graph
and a sequence of accept/reject/reset calls, the on-disk graph mutates as
ADR 0055 prescribes:

- ``accept`` promotes the edge's ``confidence`` from ``speculative`` to
  ``definite`` and writes the graph through.
- ``reject`` does NOT mutate the graph immediately; the drop happens at
  next discover (covered by the discover-integration test).
- ``reset`` removes the decision from review-state.
- ``status`` reports counts and surfaces stale decisions whose
  ``edge_snapshot`` no longer matches the current edge.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


from weld._review import (  # noqa: E402
    accept_edge,
    apply_review_state,
    detect_ghost_emit,
    list_pending,
    mint_edge_id,
    reject_edge,
    reset_decision,
    show_edge,
    status_summary,
)
from weld._review_state import load_state  # noqa: E402
from weld.graph import Graph  # noqa: E402


def _seed_graph(root: Path) -> tuple[Graph, dict]:
    """Build a minimal graph with one speculative edge for review."""
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    g = Graph(root)
    g.load()
    g.add_node("symbol:caller", "symbol", "caller", {})
    g.add_node("symbol:callee", "symbol", "callee", {})
    edge = {
        "from": "symbol:caller",
        "to": "symbol:callee",
        "type": "calls",
        "props": {
            "source_strategy": "anthropic_enrichment",
            "confidence": "speculative",
            "provenance": {"model": "claude", "rationale": "guess"},
        },
    }
    g.add_edge(edge["from"], edge["to"], edge["type"], edge["props"])
    g.save()
    return g, edge


class ListPendingTest(unittest.TestCase):
    """``list_pending`` surfaces speculative edges not yet in review-state."""

    def test_list_pending_returns_speculative_edges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_graph(root)
            result = list_pending(root)
            self.assertEqual(len(result["edges"]), 1)
            entry = result["edges"][0]
            self.assertIn("review_id", entry)
            self.assertEqual(entry["confidence"], "speculative")

    def test_list_pending_skips_already_decided(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, edge = _seed_graph(root)
            eid = mint_edge_id(edge)
            accept_edge(root, eid, reason="ok", reviewer="me@example.org")
            result = list_pending(root)
            self.assertEqual(len(result["edges"]), 0)


class AcceptTest(unittest.TestCase):
    """``accept_edge`` promotes confidence and writes to graph + state."""

    def test_accept_promotes_speculative_to_definite_in_graph(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, edge = _seed_graph(root)
            eid = mint_edge_id(edge)
            res = accept_edge(root, eid, reason="LGTM", reviewer="me")
            self.assertEqual(res["decision"], "accepted")
            self.assertEqual(res["confidence"], "definite")
            # Reload the graph and check the edge confidence.
            g2 = Graph(root)
            g2.load()
            edges = g2.dump()["edges"]
            self.assertEqual(len(edges), 1)
            self.assertEqual(
                edges[0]["props"]["confidence"], "definite",
                "Accept should mutate the graph in-place.",
            )

    def test_accept_writes_decision_with_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, edge = _seed_graph(root)
            eid = mint_edge_id(edge)
            accept_edge(root, eid, reason="LGTM", reviewer="me@example.org")
            state = load_state(root)
            self.assertIn(eid, state.decisions)
            d = state.decisions[eid]
            self.assertEqual(d.decision, "accepted")
            self.assertEqual(d.reason, "LGTM")
            self.assertEqual(d.reviewer, "me@example.org")
            self.assertEqual(d.edge_snapshot["from"], "symbol:caller")

    def test_accept_returns_error_for_unknown_edge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_graph(root)
            res = accept_edge(
                root, "deadbeef" * 2, reason="", reviewer="me",
            )
            self.assertIn("error", res)


class RejectTest(unittest.TestCase):
    """``reject_edge`` records the decision but does NOT mutate the graph."""

    def test_reject_records_decision_without_graph_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, edge = _seed_graph(root)
            eid = mint_edge_id(edge)
            res = reject_edge(root, eid, reason="wrong", reviewer="me")
            self.assertEqual(res["decision"], "rejected")
            g2 = Graph(root)
            g2.load()
            # Edge is still on disk -- drop happens at next discover.
            self.assertEqual(len(g2.dump()["edges"]), 1)
            # State is recorded.
            state = load_state(root)
            self.assertEqual(state.decisions[eid].decision, "rejected")


class ResetTest(unittest.TestCase):
    """``reset_decision`` removes the decision from review-state."""

    def test_reset_removes_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, edge = _seed_graph(root)
            eid = mint_edge_id(edge)
            reject_edge(root, eid, reason="", reviewer="me")
            self.assertIn(eid, load_state(root).decisions)
            res = reset_decision(root, eid)
            self.assertEqual(res["decision"], "pending")
            self.assertNotIn(eid, load_state(root).decisions)

    def test_reset_unknown_decision_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_graph(root)
            # Resetting a never-decided edge id is a no-op success.
            res = reset_decision(root, "deadbeef" * 2)
            self.assertEqual(res["decision"], "pending")


class ShowTest(unittest.TestCase):
    """``show_edge`` returns the edge with provenance rendering."""

    def test_show_returns_edge_with_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, edge = _seed_graph(root)
            eid = mint_edge_id(edge)
            res = show_edge(root, eid)
            self.assertEqual(res["review_id"], eid)
            self.assertIn("provenance", res)
            self.assertEqual(res["provenance"]["model"], "claude")

    def test_show_returns_error_for_unknown_edge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_graph(root)
            res = show_edge(root, "0" * 16)
            self.assertIn("error", res)


class StatusTest(unittest.TestCase):
    """``status_summary`` reports pending / accepted / rejected / stale counts."""

    def test_status_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, edge = _seed_graph(root)
            eid = mint_edge_id(edge)
            reject_edge(root, eid, reason="", reviewer="me")
            s = status_summary(root)
            self.assertEqual(s["accepted"], 0)
            self.assertEqual(s["rejected"], 1)
            self.assertEqual(s["pending"], 0)
            self.assertEqual(s["stale"], 0)

    def test_stale_detection_when_edge_snapshot_diverges(self) -> None:
        """If the underlying edge changes after accept/reject, mark stale."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            g, edge = _seed_graph(root)
            eid = mint_edge_id(edge)
            reject_edge(root, eid, reason="", reviewer="me")
            # Mutate the edge so the snapshot diverges:
            g.load()
            edges = g.dump()["edges"]
            edges[0]["props"]["provenance"] = {"model": "new-model"}
            g.save()
            s = status_summary(root)
            self.assertEqual(s["stale"], 1)


class GhostEmitTest(unittest.TestCase):
    """A strategy that re-emits a rejected edge triggers the ghost warning."""

    def test_detect_ghost_emit_warns_for_rejected_still_on_graph(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, edge = _seed_graph(root)
            eid = mint_edge_id(edge)
            reject_edge(root, eid, reason="", reviewer="me")
            # Simulate a re-discover that re-added the edge without
            # honoring the review-state.
            ghosts = detect_ghost_emit(root)
            self.assertEqual(len(ghosts), 1)
            self.assertEqual(ghosts[0]["review_id"], eid)


class ApplyReviewStateTest(unittest.TestCase):
    """The re-discovery contract: apply_review_state filters rejected, promotes accepted."""

    def test_apply_filters_rejected_and_promotes_accepted(self) -> None:
        """A new discovery edge list passes through ``apply_review_state``;
        rejected edges are removed and accepted edges keep ``definite``."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, edge = _seed_graph(root)
            eid = mint_edge_id(edge)
            reject_edge(root, eid, reason="", reviewer="me")
            # Simulate the next discover, which re-emits the same edge
            # as speculative.
            edges = [dict(edge)]
            edges[0]["props"] = dict(edge["props"])
            filtered = apply_review_state(root, edges)
            self.assertEqual(filtered, [])

    def test_apply_promotes_accepted_edges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, edge = _seed_graph(root)
            eid = mint_edge_id(edge)
            accept_edge(root, eid, reason="ok", reviewer="me")
            # Even if discover wants to emit speculative again, accept
            # sticks.
            edges = [dict(edge)]
            edges[0]["props"] = dict(edge["props"])
            edges[0]["props"]["confidence"] = "speculative"
            filtered = apply_review_state(root, edges)
            self.assertEqual(len(filtered), 1)
            self.assertEqual(filtered[0]["props"]["confidence"], "definite")


if __name__ == "__main__":
    unittest.main()
