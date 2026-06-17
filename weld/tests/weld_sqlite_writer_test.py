"""Tests for the sqlite sidecar writer (ADR 0058).

Covers the writer's contract:

- builds the documented schema (meta + nodes + edges + indexes);
- stamps the closed set of meta keys with the correct types;
- inserts nodes alphabetical by id and edges in
  (from, to, type, mint_edge_id) order so rebuilds are deterministic;
- ``source_json_sha`` records the SHA-256 of the bytes the caller hashed;
- a rebuild from the same JSON bytes produces a byte-identical file
  modulo the ``generated_at`` field (which the test pins to a fixed value);
- a build failure leaves no partial sidecar behind (atomic rename);
- the writer accepts unicode / empty / None-prop nodes without crashing.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path


from weld import _sqlite_schema as schema  # noqa: E402
from weld import _sqlite_writer as writer  # noqa: E402
from weld._review import mint_edge_id  # noqa: E402
from weld.serializer import dumps_graph  # noqa: E402


def _sample_graph() -> dict:
    return {
        "meta": {"schema_version": 1, "version": 4},
        "nodes": {
            "service:api": {
                "type": "service",
                "label": "api",
                "props": {
                    "file": "services/api.py",
                    "origin": "in_tree",
                    "confidence": "definite",
                    "description": "REST API",
                },
            },
            "package:core": {
                "type": "package",
                "label": "core",
                "props": {"file": "core/__init__.py"},
            },
            "symbol:ident": {
                "type": "symbol",
                "label": "ident",
                "props": {},
            },
        },
        "edges": [
            {
                "from": "service:api",
                "to": "package:core",
                "type": "imports",
                "props": {
                    "source_strategy": "python_module",
                    "confidence": "definite",
                },
            },
            {
                "from": "package:core",
                "to": "symbol:ident",
                "type": "defines",
                "props": {"confidence": "inferred"},
            },
        ],
    }


def _graph_bytes(graph: dict) -> bytes:
    return dumps_graph(graph).encode("utf-8")


class SchemaShapeTest(unittest.TestCase):
    def test_creates_documented_tables_and_indexes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "graph.db"
            writer.build_sidecar_for_bytes(
                _sample_graph(), _graph_bytes(_sample_graph()), target,
                generated_at="2025-01-01T00:00:00+00:00",
            )
            self.assertTrue(target.is_file())
            with sqlite3.connect(str(target)) as conn:
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'",
                    )
                }
                self.assertEqual(
                    {
                        "meta", "nodes", "edges",
                        # Option B inverted-index tables (sidecar schema v2).
                        "token_index", "token_doc_lengths", "token_field_stats",
                    },
                    tables,
                )
                indexes = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='index'"
                        " AND name NOT LIKE 'sqlite_%'",
                    )
                }
                # Documented in ADR 0058 §"What sqlite stores".
                for expected in (
                    "nodes_type_idx", "nodes_file_idx", "nodes_origin_idx",
                    "edges_from_idx", "edges_to_idx", "edges_type_idx",
                    "edges_conf_idx",
                ):
                    self.assertIn(expected, indexes, f"missing index {expected}")

    def test_meta_keys_stamped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "graph.db"
            graph = _sample_graph()
            writer.build_sidecar_for_bytes(
                graph, _graph_bytes(graph), target,
                generated_at="2025-01-01T00:00:00+00:00",
                weld_version="0.1.0-test",
            )
            with sqlite3.connect(str(target)) as conn:
                meta = dict(conn.execute("SELECT key, value FROM meta"))
            self.assertEqual(meta[schema.META_KEY_SCHEMA_VERSION], "1")
            self.assertEqual(
                meta[schema.META_KEY_SQLITE_SCHEMA_VERSION],
                str(schema.SQLITE_SCHEMA_VERSION),
            )
            self.assertEqual(
                meta[schema.META_KEY_SOURCE_JSON_SHA],
                writer.compute_source_json_sha(_graph_bytes(graph)),
            )
            self.assertEqual(meta[schema.META_KEY_GENERATED_AT], "2025-01-01T00:00:00+00:00")
            self.assertEqual(meta[schema.META_KEY_WELD_VERSION], "0.1.0-test")


class DeterminismTest(unittest.TestCase):
    def test_byte_identical_rebuild_from_same_json(self) -> None:
        graph = _sample_graph()
        body = _graph_bytes(graph)
        ts = "2025-01-01T00:00:00+00:00"
        version = "0.1.0-test"
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.db"
            b = Path(tmp) / "b.db"
            writer.build_sidecar_for_bytes(
                graph, body, a, generated_at=ts, weld_version=version,
            )
            writer.build_sidecar_for_bytes(
                graph, body, b, generated_at=ts, weld_version=version,
            )
            self.assertEqual(
                a.read_bytes(), b.read_bytes(),
                "rebuild from identical JSON must yield byte-identical sqlite",
            )

    def test_node_insertion_alphabetical(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "graph.db"
            writer.build_sidecar_for_bytes(
                _sample_graph(), _graph_bytes(_sample_graph()), target,
                generated_at="t",
            )
            with sqlite3.connect(str(target)) as conn:
                # Use rowid -- a strict reflection of insertion order.
                ids = [
                    row[0]
                    for row in conn.execute(
                        "SELECT id FROM nodes ORDER BY rowid",
                    )
                ]
            self.assertEqual(sorted(ids), ids, "nodes must be inserted in alphabetical order")

    def test_edge_insertion_sorted_by_endpoints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "graph.db"
            writer.build_sidecar_for_bytes(
                _sample_graph(), _graph_bytes(_sample_graph()), target,
                generated_at="t",
            )
            with sqlite3.connect(str(target)) as conn:
                rows = list(
                    conn.execute(
                        "SELECT from_id, to_id, type, id FROM edges ORDER BY rowid",
                    )
                )
            expected = sorted(rows, key=lambda r: (r[0], r[1], r[2], r[3]))
            self.assertEqual(rows, expected, "edges must be sorted (from, to, type, id)")


class PropsTest(unittest.TestCase):
    def test_props_json_roundtrip_through_sort_keys(self) -> None:
        # The writer must sort prop keys so rebuild stability holds even when
        # upstream dict insertion order changes.
        g1 = {
            "meta": {"schema_version": 1},
            "nodes": {
                "x": {
                    "type": "service",
                    "label": "x",
                    "props": {"b": 1, "a": 2},
                },
            },
            "edges": [],
        }
        g2 = {
            "meta": {"schema_version": 1},
            "nodes": {
                "x": {
                    "type": "service",
                    "label": "x",
                    "props": {"a": 2, "b": 1},
                },
            },
            "edges": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            t1 = Path(tmp) / "1.db"
            t2 = Path(tmp) / "2.db"
            writer.build_sidecar_for_bytes(
                g1, _graph_bytes(g1), t1, generated_at="t",
            )
            writer.build_sidecar_for_bytes(
                g2, _graph_bytes(g2), t2, generated_at="t",
            )
            self.assertEqual(
                t1.read_bytes(), t2.read_bytes(),
                "props_json must be sort_keys-canonical",
            )

    def test_missing_props_dict_is_tolerated(self) -> None:
        graph = {
            "meta": {"schema_version": 1},
            "nodes": {
                "x": {"type": "service", "label": "x"},  # no props key
            },
            "edges": [
                {"from": "x", "to": "x", "type": "calls"},  # no props key
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "graph.db"
            writer.build_sidecar_for_bytes(
                graph, _graph_bytes(graph), target, generated_at="t",
            )
            with sqlite3.connect(str(target)) as conn:
                node_row = conn.execute(
                    "SELECT id, type, label, props_json FROM nodes",
                ).fetchone()
                edge_row = conn.execute(
                    "SELECT from_id, to_id, type, props_json FROM edges",
                ).fetchone()
            self.assertEqual(node_row[3], "{}")
            self.assertEqual(edge_row[3], "{}")


class AtomicWriteTest(unittest.TestCase):
    def test_existing_file_replaced_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "graph.db"
            target.write_bytes(b"placeholder")
            graph = _sample_graph()
            writer.build_sidecar_for_bytes(
                graph, _graph_bytes(graph), target, generated_at="t",
            )
            # The placeholder must be gone and replaced by a sqlite file.
            with sqlite3.connect(str(target)) as conn:
                count = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()
            self.assertEqual(count[0], len(graph["nodes"]))

    def test_safe_build_swallows_target_dir_failure(self) -> None:
        # Pointing the writer at a parent that does not exist and cannot
        # be created (a path under a file, not a directory) must not crash.
        with tempfile.TemporaryDirectory() as tmp:
            blocker = Path(tmp) / "blocker"
            blocker.write_bytes(b"")
            bad_target = blocker / "graph.db"
            result = writer.safe_build_sidecar_for_bytes(
                _sample_graph(), _graph_bytes(_sample_graph()), bad_target,
            )
            self.assertIsNone(result)


class EdgeIdTest(unittest.TestCase):
    def test_edge_id_matches_mint_edge_id(self) -> None:
        graph = _sample_graph()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "graph.db"
            writer.build_sidecar_for_bytes(
                graph, _graph_bytes(graph), target, generated_at="t",
            )
            with sqlite3.connect(str(target)) as conn:
                rows = list(
                    conn.execute("SELECT id, from_id, to_id, type FROM edges"),
                )
            for edge_id, frm, to_id, etype in rows:
                expected = mint_edge_id({
                    "from": frm,
                    "to": to_id,
                    "type": etype,
                    "props": next(
                        e["props"] for e in graph["edges"]
                        if e["from"] == frm and e["to"] == to_id and e["type"] == etype
                    ),
                })
                self.assertEqual(edge_id, expected)


if __name__ == "__main__":
    unittest.main()
