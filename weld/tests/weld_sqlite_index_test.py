"""Tests for the sqlite inverted-index writer + reader helpers.

Covers the contract added by sidecar schema version 2 (Option B
lazy per-query inverted index):

- ``build_token_rows`` matches the in-memory ``query_index.build_index``
  plus ``bm25.BM25Corpus.from_nodes`` (same token universe, same
  per-node lengths, same average length).
- ``insert_token_index`` populates the three new tables and writes the
  corpus stats row even on an empty corpus.
- ``read_token_rows_for_token`` does substring matching equivalent to
  the in-memory ``candidate_nodes`` for a single token.
- LIKE-wildcard injection in the query term is escaped: a query
  containing ``%`` does NOT widen to "all rows".
- ``read_node_frequencies`` returns a per-node ``{token: frequency}``
  map identical to ``Counter(node_tokens(...))``.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from collections import Counter
from pathlib import Path


from weld import _sqlite_index as idx  # noqa: E402
from weld import _sqlite_writer as writer  # noqa: E402
from weld.bm25 import BM25Corpus  # noqa: E402
from weld.query_index import build_index, node_tokens  # noqa: E402
from weld.serializer import dumps_graph  # noqa: E402


def _sample_nodes() -> dict[str, dict]:
    return {
        "service:billing": {
            "type": "service",
            "label": "billing",
            "props": {
                "file": "services/billing.py",
                "description": "Billing rollup",
                "exports": ["bill", "charge"],
            },
        },
        "service:auth": {
            "type": "service",
            "label": "auth",
            "props": {
                "file": "services/auth.py",
                "description": "Auth surface",
                "exports": ["login", "logout"],
            },
        },
        "symbol:helper": {
            "type": "symbol",
            "label": "helper",
            "props": {},
        },
    }


def _build_db(nodes: dict[str, dict]) -> tuple[Path, "tempfile.TemporaryDirectory[str]"]:
    """Build a sidecar containing only the supplied nodes."""
    tmp = tempfile.TemporaryDirectory()
    target = Path(tmp.name) / "graph.db"
    graph = {"meta": {"schema_version": 1}, "nodes": nodes, "edges": []}
    body = dumps_graph(graph).encode("utf-8")
    writer.build_sidecar_for_bytes(graph, body, target, generated_at="t")
    return target, tmp


class BuildTokenRowsTest(unittest.TestCase):
    def test_token_universe_matches_in_memory_build_index(self) -> None:
        nodes = _sample_nodes()
        sorted_nodes = sorted(nodes.items(), key=lambda kv: kv[0])
        result = idx.build_token_rows(sorted_nodes)
        in_memory = build_index(nodes)

        # Every (token, node_id) pair in token_rows must appear in the
        # in-memory inverted-index sets, and vice versa.
        sqlite_pairs = {(t, n) for (t, n, _f) in result.token_rows}
        memory_pairs = {
            (token, nid)
            for token, ids in in_memory.items()
            for nid in ids
        }
        self.assertEqual(sqlite_pairs, memory_pairs)

    def test_doc_lengths_and_avg_match_bm25_corpus(self) -> None:
        nodes = _sample_nodes()
        sorted_nodes = sorted(nodes.items(), key=lambda kv: kv[0])
        result = idx.build_token_rows(sorted_nodes)
        corpus = BM25Corpus.from_nodes(nodes)

        # Recompute the corpus's per-doc length the same way it does
        # internally to compare against the writer's output.
        doc_lengths_sqlite = dict(result.doc_length_rows)
        for nid in nodes:
            expected = sum(Counter(node_tokens(nid, nodes[nid])).values())
            self.assertEqual(doc_lengths_sqlite[nid], expected)

        # Average length must match the in-memory corpus to within a
        # tiny epsilon (both run the same arithmetic).
        self.assertAlmostEqual(
            result.avg_length,
            sum(doc_lengths_sqlite.values()) / len(nodes),
            places=6,
        )
        # And BM25 corpus exposes doc_count for a separate sanity check.
        self.assertEqual(result.doc_count, corpus.doc_count)


class InsertTokenIndexTest(unittest.TestCase):
    def test_empty_graph_still_writes_corpus_stats_row(self) -> None:
        db_path, tmp = _build_db({})
        try:
            with sqlite3.connect(str(db_path)) as conn:
                row = conn.execute(
                    "SELECT field, total_docs FROM token_field_stats",
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row[0], idx.CORPUS_STATS_FIELD)
                self.assertEqual(row[1], 0)
        finally:
            tmp.cleanup()

    def test_populated_graph_writes_token_rows_and_lengths(self) -> None:
        nodes = _sample_nodes()
        db_path, tmp = _build_db(nodes)
        try:
            with sqlite3.connect(str(db_path)) as conn:
                token_count = conn.execute(
                    "SELECT COUNT(*) FROM token_index",
                ).fetchone()[0]
                self.assertGreater(token_count, 0)
                length_rows = dict(conn.execute(
                    "SELECT node_id, length FROM token_doc_lengths",
                ))
                self.assertEqual(set(length_rows), set(nodes))
                # Per-node length must equal sum of frequencies in
                # token_index for that node.
                for nid in nodes:
                    summed = conn.execute(
                        "SELECT COALESCE(SUM(frequency), 0) FROM token_index"
                        " WHERE node_id = ?",
                        (nid,),
                    ).fetchone()[0]
                    self.assertEqual(int(summed), length_rows[nid])
        finally:
            tmp.cleanup()


class ReadTokenRowsTest(unittest.TestCase):
    def test_substring_match_matches_candidate_nodes(self) -> None:
        nodes = _sample_nodes()
        db_path, tmp = _build_db(nodes)
        try:
            with sqlite3.connect(str(db_path)) as conn:
                rows = idx.read_token_rows_for_token(conn, "bill")
                hit_ids = {nid for (_t, nid, _f) in rows}
                # "bill" hits the billing service's id and description.
                self.assertIn("service:billing", hit_ids)
                self.assertNotIn("service:auth", hit_ids)
        finally:
            tmp.cleanup()

    def test_empty_token_returns_empty_list(self) -> None:
        nodes = _sample_nodes()
        db_path, tmp = _build_db(nodes)
        try:
            with sqlite3.connect(str(db_path)) as conn:
                self.assertEqual([], idx.read_token_rows_for_token(conn, ""))
        finally:
            tmp.cleanup()

    def test_like_wildcard_in_term_is_escaped(self) -> None:
        """A query containing ``%`` must not widen to match all rows.

        The reader percent-encodes the term, but the inverted-index
        path does its own substring search via LIKE. This test pins the
        ``ESCAPE '\\'`` contract of
        :func:`weld._sqlite_index.read_token_rows_for_token` so a
        user-supplied term like ``%`` does not accidentally pull every row.
        """
        nodes = _sample_nodes()
        db_path, tmp = _build_db(nodes)
        try:
            with sqlite3.connect(str(db_path)) as conn:
                rows = idx.read_token_rows_for_token(conn, "%")
                # No indexed token contains a literal '%' character, so
                # the escaped LIKE must return zero rows.
                self.assertEqual([], rows)
                rows = idx.read_token_rows_for_token(conn, "_")
                self.assertEqual([], rows)
        finally:
            tmp.cleanup()


class ReadNodeFrequenciesTest(unittest.TestCase):
    def test_matches_in_memory_counter(self) -> None:
        nodes = _sample_nodes()
        db_path, tmp = _build_db(nodes)
        try:
            with sqlite3.connect(str(db_path)) as conn:
                fetched = idx.read_node_frequencies(conn, list(nodes))
            for nid, node in nodes.items():
                expected = Counter(node_tokens(nid, node))
                self.assertEqual(fetched.get(nid, {}), dict(expected))
        finally:
            tmp.cleanup()

    def test_unknown_ids_return_empty_map(self) -> None:
        db_path, tmp = _build_db(_sample_nodes())
        try:
            with sqlite3.connect(str(db_path)) as conn:
                self.assertEqual({}, idx.read_node_frequencies(conn, []))
                self.assertEqual(
                    {},
                    idx.read_node_frequencies(conn, ["nope:absent"]),
                )
        finally:
            tmp.cleanup()


class ReadCorpusStatsTest(unittest.TestCase):
    def test_stats_match_doc_count_and_avg(self) -> None:
        nodes = _sample_nodes()
        db_path, tmp = _build_db(nodes)
        try:
            with sqlite3.connect(str(db_path)) as conn:
                avg, total = idx.read_corpus_stats(conn)
            self.assertEqual(total, len(nodes))
            self.assertGreater(avg, 0.0)
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
