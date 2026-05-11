"""Tests for the sqlite sidecar reader and Graph.open() (ADR 0058).

Covers the read path described in ADR 0058 §"Read path":

- :func:`sidecar_freshness` matches the writer's stamped SHA;
- :class:`SqliteBackedGraph` exposes the same surface (nodes / edges /
  neighbors) as the JSON-backed :class:`Graph`;
- :meth:`Graph.open` prefers sqlite when fresh and falls back to JSON
  otherwise (missing db, stale SHA, corrupt db, wrong sqlite_schema_version).
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld import _sqlite_reader as reader  # noqa: E402
from weld import _sqlite_writer as writer  # noqa: E402
from weld._sqlite_schema import (  # noqa: E402
    META_KEY_SOURCE_JSON_SHA,
    META_KEY_SQLITE_SCHEMA_VERSION,
    SQLITE_SCHEMA_VERSION,
)
from weld.graph import Graph  # noqa: E402
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
                    "exports": ["create_user"],
                    "description": "REST API",
                },
            },
            "package:core": {
                "type": "package",
                "label": "core",
                "props": {"file": "core/__init__.py"},
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
        ],
    }


def _write_pair(root: Path, graph: dict) -> tuple[Path, Path]:
    """Write graph.json + a fresh graph.db under *root*/.weld/."""
    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    graph_json_path = weld_dir / "graph.json"
    body = dumps_graph(graph).encode("utf-8")
    graph_json_path.write_bytes(body)
    db_path = weld_dir / "graph.db"
    writer.build_sidecar_for_bytes(
        graph, body, db_path, generated_at="t",
    )
    return graph_json_path, db_path


def _stamp_sidecar_schema_version(db_path: Path, value: str) -> None:
    """Overwrite the sidecar's stamped ``sqlite_schema_version`` row.

    Helper for regression coverage of the f0yn Option B path: when the
    runtime ``SQLITE_SCHEMA_VERSION`` constant is bumped, every on-disk
    ``graph.db`` carries a now-stale stamp. The reader must reject any
    mismatch (forward bump, backward, or garbage) without touching the
    nodes/edges tables. Mutation uses a bound parameter (no string
    interpolation) so the helper is safe for hostile inputs too.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "UPDATE meta SET value = ? WHERE key = ?",
            (str(value), META_KEY_SQLITE_SCHEMA_VERSION),
        )
        conn.commit()
    finally:
        conn.close()


class FreshnessTest(unittest.TestCase):
    def test_fresh_sidecar_reports_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            graph_path, _ = _write_pair(root, _sample_graph())
            fresh, meta = reader.sidecar_freshness(graph_path)
            self.assertTrue(fresh)
            self.assertEqual(
                meta[META_KEY_SOURCE_JSON_SHA],
                writer.compute_source_json_sha(graph_path.read_bytes()),
            )

    def test_stale_sidecar_when_json_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            graph_path, _ = _write_pair(root, _sample_graph())
            # Append a byte to the JSON -> SHA-256 diverges from the
            # value the sidecar recorded.
            graph_path.write_bytes(graph_path.read_bytes() + b"\n")
            fresh, _meta = reader.sidecar_freshness(graph_path)
            self.assertFalse(fresh)

    def test_missing_sidecar_returns_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            weld_dir = root / ".weld"
            weld_dir.mkdir()
            graph_path = weld_dir / "graph.json"
            graph_path.write_bytes(
                dumps_graph(_sample_graph()).encode("utf-8"),
            )
            fresh, meta = reader.sidecar_freshness(graph_path)
            self.assertFalse(fresh)
            self.assertEqual(meta, {})

    def test_corrupt_sidecar_returns_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            weld_dir = root / ".weld"
            weld_dir.mkdir()
            graph_path = weld_dir / "graph.json"
            graph_path.write_bytes(
                dumps_graph(_sample_graph()).encode("utf-8"),
            )
            (weld_dir / "graph.db").write_bytes(b"not a sqlite file")
            fresh, _meta = reader.sidecar_freshness(graph_path)
            self.assertFalse(fresh)

    def test_missing_graph_json_returns_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            graph_path = Path(tmp) / "graph.json"  # never created
            fresh, _meta = reader.sidecar_freshness(graph_path)
            self.assertFalse(fresh)


class SqliteBackedSurfaceTest(unittest.TestCase):
    def test_get_node_returns_jsonish_dict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            graph_path, _ = _write_pair(root, _sample_graph())
            backed = reader.open_sidecar_if_fresh(graph_path)
            self.assertIsNotNone(backed)
            assert backed is not None
            node = backed.get_node("service:api")
            self.assertIsNotNone(node)
            assert node is not None
            self.assertEqual(node["id"], "service:api")
            self.assertEqual(node["type"], "service")
            self.assertEqual(node["props"]["file"], "services/api.py")
            backed.close()

    def test_list_nodes_filter_by_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            graph_path, _ = _write_pair(root, _sample_graph())
            backed = reader.open_sidecar_if_fresh(graph_path)
            self.assertIsNotNone(backed)
            assert backed is not None
            services = backed.list_nodes(type_filter="service")
            self.assertEqual([n["id"] for n in services], ["service:api"])
            backed.close()

    def test_neighbors_returns_both_directions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            graph_path, _ = _write_pair(root, _sample_graph())
            backed = reader.open_sidecar_if_fresh(graph_path)
            self.assertIsNotNone(backed)
            assert backed is not None
            nb = backed.neighbors("package:core")
            self.assertEqual(len(nb), 1)
            self.assertEqual(nb[0]["type"], "imports")
            backed.close()

    def test_iter_edges_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            graph_path, _ = _write_pair(root, _sample_graph())
            backed = reader.open_sidecar_if_fresh(graph_path)
            self.assertIsNotNone(backed)
            assert backed is not None
            edges_a = list(backed.iter_edges())
            edges_b = list(backed.iter_edges())
            self.assertEqual(edges_a, edges_b)
            backed.close()

    def test_dump_materialises_json_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            graph_path, _ = _write_pair(root, _sample_graph())
            backed = reader.open_sidecar_if_fresh(graph_path)
            self.assertIsNotNone(backed)
            assert backed is not None
            data = backed.dump()
            self.assertIn("service:api", data["nodes"])
            self.assertEqual(len(data["edges"]), 1)
            self.assertEqual(data["meta"]["schema_version"], 1)
            backed.close()


class GraphOpenTest(unittest.TestCase):
    def test_open_returns_sqlite_when_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_pair(root, _sample_graph())
            handle = Graph.open(root)
            try:
                # Must be the sqlite-backed shape -- not a plain Graph.
                self.assertIsInstance(handle, reader.SqliteBackedGraph)
                self.assertEqual(
                    handle.get_node("service:api")["type"], "service",
                )
            finally:
                # Close only if it is the sqlite-backed object.
                if isinstance(handle, reader.SqliteBackedGraph):
                    handle.close()

    def test_open_falls_back_to_json_when_sqlite_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            weld_dir = root / ".weld"
            weld_dir.mkdir()
            graph_path = weld_dir / "graph.json"
            graph_path.write_bytes(
                dumps_graph(_sample_graph()).encode("utf-8"),
            )
            handle = Graph.open(root)
            self.assertIsInstance(handle, Graph)
            # JSON-backed Graph exposes get_node with the same shape.
            node = handle.get_node("service:api")
            self.assertIsNotNone(node)

    def test_open_falls_back_when_sidecar_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            graph_path, _ = _write_pair(root, _sample_graph())
            # Mutate the JSON so the SHA no longer matches.
            graph_path.write_bytes(graph_path.read_bytes() + b"\n")
            handle = Graph.open(root)
            self.assertIsInstance(handle, Graph)

    def test_open_falls_back_when_sidecar_corrupt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            graph_path, db_path = _write_pair(root, _sample_graph())
            db_path.write_bytes(b"corrupt")
            handle = Graph.open(root)
            self.assertIsInstance(handle, Graph)


class StaleSchemaVersionTest(unittest.TestCase):
    """Regression: stale ``sqlite_schema_version`` values fall back to JSON.

    The f0yn Option B follow-up (lazy inverted-index from sqlite for
    federation query) bumps :data:`SQLITE_SCHEMA_VERSION`. That bump
    invalidates every existing ``graph.db`` on disk. The reader must
    treat the mismatch as a *cache miss* -- never as a crash -- so the
    bump is safe to ship without a coordinated rebuild on every host
    that ever ran an older ``wd``.

    Behavior is exercised at three layers:

    1. :func:`weld._sqlite_reader.sidecar_freshness` returns
       ``(False, meta)`` for any non-matching stamp.
    2. :func:`weld._sqlite_reader.open_sidecar_if_fresh` returns
       ``None`` so callers fall through to JSON.
    3. :meth:`weld.graph.Graph.open` returns a JSON-backed
       :class:`Graph`, not a :class:`SqliteBackedGraph`, and
       :meth:`Graph.query` returns valid matches against the JSON.

    Three sentinel stamps cover the realistic mismatch shapes:

    - Forward bump (``runtime + 1``): the f0yn Option B scenario --
      readers built before the bump see a stamp from a newer writer.
    - Backward (``"0"``): a hand-edited or rolled-back sidecar that
      claims to predate the current envelope.
    - Garbage (``"not-an-int"``): a corrupted meta row whose value
      cannot parse to an integer.
    """

    def _stale_stamps(self) -> list[str]:
        forward = str(SQLITE_SCHEMA_VERSION + 1)
        return [forward, "0", "not-an-int"]

    def test_sidecar_freshness_rejects_stale_schema_version(self) -> None:
        for stamp in self._stale_stamps():
            with self.subTest(stamp=stamp):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    graph_path, db_path = _write_pair(root, _sample_graph())
                    _stamp_sidecar_schema_version(db_path, stamp)
                    fresh, meta = reader.sidecar_freshness(graph_path)
                    self.assertFalse(fresh)
                    # ``sidecar_freshness`` returns the meta dict it
                    # read (not empty) when the mismatch is the
                    # schema-version row -- diagnostic callers like
                    # ``wd doctor`` rely on this to render the actual
                    # stale value.
                    self.assertIn(
                        META_KEY_SQLITE_SCHEMA_VERSION,
                        meta,
                        f"meta should still expose the stale stamp "
                        f"for diagnostics ({stamp!r})",
                    )
                    self.assertEqual(
                        meta[META_KEY_SQLITE_SCHEMA_VERSION], stamp,
                    )

    def test_open_sidecar_if_fresh_returns_none_on_stale_schema_version(self) -> None:
        for stamp in self._stale_stamps():
            with self.subTest(stamp=stamp):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    graph_path, db_path = _write_pair(root, _sample_graph())
                    _stamp_sidecar_schema_version(db_path, stamp)
                    self.assertIsNone(reader.open_sidecar_if_fresh(graph_path))

    def test_graph_open_falls_back_and_query_works(self) -> None:
        for stamp in self._stale_stamps():
            with self.subTest(stamp=stamp):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    _graph_path, db_path = _write_pair(root, _sample_graph())
                    _stamp_sidecar_schema_version(db_path, stamp)
                    handle = Graph.open(root)
                    try:
                        # JSON-backed Graph, not SqliteBackedGraph.
                        self.assertIsInstance(handle, Graph)
                        self.assertNotIsInstance(handle, reader.SqliteBackedGraph)
                        # Query path uses the in-memory inverted index --
                        # exercising it proves the fallback is functional,
                        # not just structurally typed.
                        result = handle.query("api", limit=5)
                        ids = {match["id"] for match in result.get("matches", [])}
                        self.assertIn("service:api", ids)
                    finally:
                        if isinstance(handle, reader.SqliteBackedGraph):
                            handle.close()


class QueryOnlyTest(unittest.TestCase):
    def test_read_only_connection_refuses_writes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            graph_path, _ = _write_pair(root, _sample_graph())
            backed = reader.open_sidecar_if_fresh(graph_path)
            self.assertIsNotNone(backed)
            assert backed is not None
            try:
                with self.assertRaises(sqlite3.Error):
                    # Even with query_only=ON, a read-only URI connection
                    # already refuses writes. Either error class is fine.
                    backed._conn.execute(
                        "INSERT INTO nodes(id, type, label, props_json)"
                        " VALUES (?, ?, ?, ?)",
                        ("x", "service", "x", "{}"),
                    )
            finally:
                backed.close()


if __name__ == "__main__":
    unittest.main()
