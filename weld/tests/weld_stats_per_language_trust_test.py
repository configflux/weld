"""Per-language trust metrics for ``wd stats`` (epic: Tier-1 trust).

These tests pin the numbers behind "do agents trust weld output in
language X?": for each language, the unresolved-symbol ratio, the
``inherits``/``calls`` edge-resolution rate, and the description
coverage. The fixture is a deliberately small two-language graph
(python + go) with hand-chosen resolved/unresolved symbols and edges so
every ratio is verifiable by hand.

The aggregation lives in :func:`weld._graph_stats_trust.compute_per_language_trust`
and is surfaced additively by :func:`weld._graph_stats.compute_stats`
under the ``per_language_trust`` key.
"""

from __future__ import annotations

import unittest

from weld._graph_stats import compute_stats
from weld._graph_stats_trust import compute_per_language_trust


def _sym(node_id, language, origin, *, description=None):
    props = {"language": language, "origin": origin}
    if description is not None:
        props["description"] = description
    return {"id": node_id, "type": "symbol", "label": node_id, "props": props}


def _edge(src, dst, etype, *, resolved):
    return {
        "from": src,
        "to": dst,
        "type": etype,
        "props": {"resolved": resolved},
    }


def _fixture():
    """Two-language graph with known trust numbers.

    python: 4 symbols, 1 unresolved -> ratio 0.25; 2 described -> 50%.
            calls edges: 2 resolved + 1 unresolved -> rate 2/3.
            inherits edges: 1 resolved -> counted with calls (3+1=4 edges,
            3 resolved -> 0.75).
    go:     2 symbols, 1 unresolved -> ratio 0.5; 0 described -> 0%.
            calls edges: 0 resolved + 1 unresolved -> rate 0.0.
    A non-symbol node and a ``contains`` edge are present to prove they
    are ignored.
    """
    nodes = {
        "symbol:py:a": _sym("symbol:py:a", "python", "project", description="A"),
        "symbol:py:b": _sym("symbol:py:b", "python", "project", description="B"),
        "symbol:py:c": _sym("symbol:py:c", "python", "stdlib"),
        "symbol:unresolved:py_x": _sym(
            "symbol:unresolved:py_x", "python", "unresolved"
        ),
        "symbol:go:m": _sym("symbol:go:m", "go", "project"),
        "symbol:unresolved:go_y": _sym(
            "symbol:unresolved:go_y", "go", "unresolved"
        ),
        "file:pkg/a": {
            "id": "file:pkg/a",
            "type": "file",
            "label": "a",
            "props": {},
        },
    }
    edges = [
        # python calls: 2 resolved, 1 unresolved.
        _edge("symbol:py:a", "symbol:py:b", "calls", resolved=True),
        _edge("symbol:py:a", "symbol:py:c", "calls", resolved=True),
        _edge("symbol:py:b", "symbol:unresolved:py_x", "calls", resolved=False),
        # python inherits: 1 resolved.
        _edge("symbol:py:b", "symbol:py:a", "inherits", resolved=True),
        # go calls: 0 resolved, 1 unresolved.
        _edge("symbol:go:m", "symbol:unresolved:go_y", "calls", resolved=False),
        # structural edge that must be ignored by the trust aggregation.
        _edge("symbol:py:a", "file:pkg/a", "contains", resolved=True),
    ]
    return nodes, edges


class ComputePerLanguageTrustTest(unittest.TestCase):
    def setUp(self) -> None:
        self.nodes, self.edges = _fixture()
        self.trust = compute_per_language_trust(self.nodes, self.edges)

    def test_keys_are_sorted_languages_only(self) -> None:
        self.assertEqual(list(self.trust.keys()), ["go", "python"])

    def test_python_symbol_counts_and_unresolved_ratio(self) -> None:
        py = self.trust["python"]
        self.assertEqual(py["symbols"], 4)
        self.assertEqual(py["unresolved_symbols"], 1)
        self.assertEqual(py["unresolved_symbol_ratio"], 0.25)

    def test_python_edge_resolution_rate_spans_calls_and_inherits(self) -> None:
        py = self.trust["python"]
        # 3 calls + 1 inherits = 4 trust edges; 3 resolved.
        self.assertEqual(py["edges"], 4)
        self.assertEqual(py["resolved_edges"], 3)
        self.assertEqual(py["edge_resolution_rate"], 0.75)

    def test_python_description_coverage(self) -> None:
        py = self.trust["python"]
        self.assertEqual(py["described_symbols"], 2)
        self.assertEqual(py["description_coverage_pct"], 50.0)

    def test_go_metrics(self) -> None:
        go = self.trust["go"]
        self.assertEqual(go["symbols"], 2)
        self.assertEqual(go["unresolved_symbols"], 1)
        self.assertEqual(go["unresolved_symbol_ratio"], 0.5)
        self.assertEqual(go["edges"], 1)
        self.assertEqual(go["resolved_edges"], 0)
        self.assertEqual(go["edge_resolution_rate"], 0.0)
        self.assertEqual(go["description_coverage_pct"], 0.0)

    def test_structural_edges_and_nonsymbol_nodes_ignored(self) -> None:
        # The ``contains`` edge and the ``file`` node must not inflate any
        # count: python has exactly 4 trust edges (not 5) and the file
        # node creates no language bucket of its own.
        self.assertEqual(self.trust["python"]["edges"], 4)
        self.assertNotIn("", self.trust)
        self.assertEqual(set(self.trust), {"python", "go"})

    def test_edge_attributed_to_source_language(self) -> None:
        # A go symbol calling an unresolved target counts under go, not
        # under the (unresolved, language-less) target.
        self.assertEqual(self.trust["go"]["edges"], 1)


class EmptyAndDegenerateGraphTest(unittest.TestCase):
    def test_empty_graph_yields_empty_trust(self) -> None:
        self.assertEqual(compute_per_language_trust({}, []), {})

    def test_language_with_no_trust_edges_is_vacuously_resolved(self) -> None:
        nodes = {"symbol:py:a": _sym("symbol:py:a", "python", "project")}
        trust = compute_per_language_trust(nodes, [])
        self.assertEqual(trust["python"]["edges"], 0)
        # No edges -> no unresolved edges to count against the language.
        self.assertEqual(trust["python"]["edge_resolution_rate"], 1.0)

    def test_symbol_without_language_is_skipped(self) -> None:
        nodes = {
            "symbol:x": {
                "id": "symbol:x",
                "type": "symbol",
                "label": "x",
                "props": {"origin": "project"},  # no language
            }
        }
        self.assertEqual(compute_per_language_trust(nodes, []), {})


class ComputeStatsIntegrationTest(unittest.TestCase):
    """``compute_stats`` carries the trust block additively."""

    def test_stats_contains_per_language_trust(self) -> None:
        nodes, edges = _fixture()
        stats = compute_stats({"nodes": nodes, "edges": edges})
        self.assertIn("per_language_trust", stats)
        self.assertEqual(
            list(stats["per_language_trust"].keys()), ["go", "python"]
        )
        self.assertEqual(
            stats["per_language_trust"]["python"]["unresolved_symbol_ratio"],
            0.25,
        )

    def test_existing_keys_unchanged(self) -> None:
        # Additive-only guarantee: the historical keys still exist.
        nodes, edges = _fixture()
        stats = compute_stats({"nodes": nodes, "edges": edges})
        for key in (
            "total_nodes",
            "total_edges",
            "nodes_by_type",
            "edges_by_type",
            "nodes_with_description",
            "description_coverage_pct",
            "description_coverage_by_type",
            "top_authority_nodes",
        ):
            self.assertIn(key, stats)


if __name__ == "__main__":
    unittest.main()
