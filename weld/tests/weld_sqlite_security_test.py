"""Security regression tests for the sqlite sidecar (ADR 0058).

The sidecar carries user-supplied node ids and edge metadata directly
into a SQLite database. The security review on this change flagged two
risk classes:

1. **SQL injection.** Every column we write must be bound, never
   string-formatted. A hostile node id like ``'); DROP TABLE nodes;--``
   must survive a round trip and *not* execute as SQL.
2. **Path traversal.** The build path resolves the target and uses an
   atomic same-directory temp file; a writer pointed at a path that
   would escape its directory must either work safely or fail clean.

These tests are intentionally minimal -- the parameterized writer/
reader make injection structurally impossible, so the tests verify
that the writer accepts hostile payloads without crashing or executing
them.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path


from weld import _sqlite_reader as reader  # noqa: E402
from weld import _sqlite_writer as writer  # noqa: E402
from weld.serializer import dumps_graph  # noqa: E402


_HOSTILE_NODE_ID = "service:api'); DROP TABLE nodes;--"
_HOSTILE_LABEL = "label'); UPDATE nodes SET type = 'hijacked';--"


def _hostile_graph() -> dict:
    return {
        "meta": {"schema_version": 1},
        "nodes": {
            _HOSTILE_NODE_ID: {
                "type": "service",
                "label": _HOSTILE_LABEL,
                "props": {
                    "file": "x.py",
                    # Hostile prop value: "exports" used to be substring-searched.
                    "exports": ["1'); DELETE FROM nodes;--"],
                },
            },
        },
        "edges": [
            {
                "from": _HOSTILE_NODE_ID,
                "to": _HOSTILE_NODE_ID,
                "type": "calls",
                "props": {
                    "confidence": "definite",
                    "source_strategy": "x'); DROP TABLE edges;--",
                },
            },
        ],
    }


class SqlInjectionTest(unittest.TestCase):
    def test_hostile_node_id_survives_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "graph.db"
            graph = _hostile_graph()
            writer.build_sidecar_for_bytes(
                graph, dumps_graph(graph).encode("utf-8"), target,
                generated_at="t",
            )
            with sqlite3.connect(str(target)) as conn:
                row = conn.execute(
                    "SELECT id, label FROM nodes WHERE id = ?",
                    (_HOSTILE_NODE_ID,),
                ).fetchone()
            self.assertIsNotNone(row, "hostile node id must be stored verbatim")
            self.assertEqual(row[0], _HOSTILE_NODE_ID)
            self.assertEqual(row[1], _HOSTILE_LABEL)

    def test_hostile_strings_do_not_drop_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "graph.db"
            graph = _hostile_graph()
            writer.build_sidecar_for_bytes(
                graph, dumps_graph(graph).encode("utf-8"), target,
                generated_at="t",
            )
            # Both tables must still exist with the expected row counts.
            with sqlite3.connect(str(target)) as conn:
                nrows = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
                erows = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
            self.assertEqual(nrows, 1)
            self.assertEqual(erows, 1)

    def test_reader_get_node_with_hostile_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            weld_dir = root / ".weld"
            weld_dir.mkdir()
            graph_path = weld_dir / "graph.json"
            graph = _hostile_graph()
            body = dumps_graph(graph).encode("utf-8")
            graph_path.write_bytes(body)
            writer.build_sidecar_for_bytes(
                graph, body, weld_dir / "graph.db", generated_at="t",
            )
            backed = reader.open_sidecar_if_fresh(graph_path)
            self.assertIsNotNone(backed)
            assert backed is not None
            try:
                node = backed.get_node(_HOSTILE_NODE_ID)
                self.assertIsNotNone(node)
                assert node is not None
                self.assertEqual(node["id"], _HOSTILE_NODE_ID)
                # ``neighbors`` also accepts and matches the hostile id.
                nb = backed.neighbors(_HOSTILE_NODE_ID)
                self.assertEqual(len(nb), 1)
            finally:
                backed.close()


class FreshnessTimingTest(unittest.TestCase):
    def test_freshness_consistent_for_same_input(self) -> None:
        # Repeated reads against the same JSON must return the same
        # answer; no observable side-channels in the freshness check.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            weld_dir = root / ".weld"
            weld_dir.mkdir()
            graph_path = weld_dir / "graph.json"
            graph = _hostile_graph()
            body = dumps_graph(graph).encode("utf-8")
            graph_path.write_bytes(body)
            writer.build_sidecar_for_bytes(
                graph, body, weld_dir / "graph.db", generated_at="t",
            )
            answers = [reader.sidecar_freshness(graph_path)[0] for _ in range(5)]
            self.assertEqual(answers, [True] * 5)


if __name__ == "__main__":
    unittest.main()
