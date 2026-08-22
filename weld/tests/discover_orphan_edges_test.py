"""Pure-function tests for weld._discover_orphan_edges (bd znzu).

``orphaned_producer_files`` answers one question: after a purge-and-merge
pass, which surviving edges still dangle (endpoint missing from the final
node set), and which clean file produced them? These tests pin that contract
directly, without paying for a real discovery run --
``weld/tests/incremental_cross_source_equivalence_test.py`` covers the
end-to-end behavior (ADR 0074 fourth amendment).
"""

from __future__ import annotations

import unittest

from weld._discover_orphan_edges import orphaned_producer_files


def _node() -> dict:
    return {"type": "symbol", "label": "thing", "props": {}}


def _edge(from_id: str, to_id: str, prov_file: str | None) -> dict:
    props: dict = {"source_strategy": "python_callgraph"}
    if prov_file is not None:
        props["provenance"] = {"file": prov_file, "line": 1}
    return {"from": from_id, "to": to_id, "type": "calls", "props": props}


class OrphanedProducerFilesTest(unittest.TestCase):
    def test_both_endpoints_present_is_not_orphaned(self) -> None:
        nodes = {"a": _node(), "b": _node()}
        edges = [_edge("a", "b", "src/caller.py")]
        self.assertEqual(orphaned_producer_files(nodes, edges), set())

    def test_missing_to_endpoint_with_provenance_is_collected(self) -> None:
        nodes = {"a": _node()}
        edges = [_edge("a", "vanished", "src/caller.py")]
        self.assertEqual(orphaned_producer_files(nodes, edges), {"src/caller.py"})

    def test_missing_from_endpoint_with_provenance_is_collected(self) -> None:
        # provenance.file need not equal the "from" node's own file in
        # general (graph_closure backfills it from "from", but the function
        # itself makes no such assumption) -- either endpoint vanishing is
        # enough to make the edge actionable.
        nodes = {"b": _node()}
        edges = [_edge("vanished", "b", "src/caller.py")]
        self.assertEqual(orphaned_producer_files(nodes, edges), {"src/caller.py"})

    def test_missing_endpoint_without_provenance_is_not_actionable(self) -> None:
        # No file to re-run for it; stays on the existing endpoint-membership
        # purge floor. Should not occur in practice (the floor already drops
        # such edges before they ever reach here), but the function does not
        # assume that -- it just has nothing useful to report.
        nodes: dict[str, dict] = {}
        edges = [_edge("a", "vanished", None)]
        self.assertEqual(orphaned_producer_files(nodes, edges), set())

    def test_two_edges_same_producer_dedupe_to_one_file(self) -> None:
        nodes = {"a": _node()}
        edges = [
            _edge("a", "vanished-1", "src/caller.py"),
            _edge("a", "vanished-2", "src/caller.py"),
        ]
        self.assertEqual(orphaned_producer_files(nodes, edges), {"src/caller.py"})

    def test_no_edges_is_empty(self) -> None:
        self.assertEqual(orphaned_producer_files({"a": _node()}, []), set())

    def test_mixed_edges_only_dangling_ones_contribute(self) -> None:
        # Pins ADR 0074's optimization survives: an ordinary graph with no
        # dangling edges contributes nothing, regardless of how many clean
        # (non-dangling) provenance-carrying edges it has.
        nodes = {"a": _node(), "b": _node()}
        edges = [
            _edge("a", "b", "src/clean_caller.py"),
            _edge("a", "vanished", "src/orphaned_caller.py"),
        ]
        self.assertEqual(
            orphaned_producer_files(nodes, edges), {"src/orphaned_caller.py"},
        )


if __name__ == "__main__":
    unittest.main()
