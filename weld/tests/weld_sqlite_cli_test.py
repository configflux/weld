"""Tests for the sqlite-sidecar CLI surface (ADR 0058).

- ``wd graph index --rebuild`` writes ``.weld/graph.db`` from the
  current ``graph.json`` and produces a sidecar a fresh
  :func:`open_sidecar_if_fresh` accepts.
- ``wd graph index`` without ``--rebuild`` is a usage error (exit 2)
  rather than a silent no-op.
- Discovery's ``--no-sqlite`` opt-out skips the sidecar write.
"""

from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path


from weld import _sqlite_reader as reader  # noqa: E402
from weld import _sqlite_writer as writer  # noqa: E402
from weld._graph_cli import main as graph_cli_main  # noqa: E402
from weld._sqlite_schema import (  # noqa: E402
    META_KEY_SQLITE_SCHEMA_VERSION,
    SQLITE_SCHEMA_VERSION,
)
from weld.serializer import dumps_graph  # noqa: E402


def _sample_graph() -> dict:
    return {
        "meta": {"schema_version": 1},
        "nodes": {
            "service:api": {
                "type": "service",
                "label": "api",
                "props": {"file": "api.py"},
            },
        },
        "edges": [],
    }


def _write_graph_json(root: Path) -> Path:
    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    graph_path = weld_dir / "graph.json"
    graph_path.write_bytes(dumps_graph(_sample_graph()).encode("utf-8"))
    return graph_path


class GraphIndexCliTest(unittest.TestCase):
    def test_rebuild_writes_fresh_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            graph_path = _write_graph_json(root)
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                graph_cli_main(["--root", str(root), "index", "--rebuild"])
            db_path = root / ".weld" / "graph.db"
            self.assertTrue(db_path.is_file())
            fresh, _meta = reader.sidecar_freshness(graph_path)
            self.assertTrue(fresh)
            # Output must be machine-readable JSON for scripted callers.
            self.assertIn("graph.db", stdout.getvalue())

    def test_index_without_rebuild_is_usage_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_graph_json(root)
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as ctx:
                    graph_cli_main(["--root", str(root), "index"])
            self.assertEqual(ctx.exception.code, 2)
            self.assertIn("--rebuild", stderr.getvalue())

    def test_index_without_graph_json_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".weld").mkdir()
            # No graph.json at all -> we cannot rebuild from nothing.
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as ctx:
                    graph_cli_main(["--root", str(root), "index", "--rebuild"])
            self.assertEqual(ctx.exception.code, 1)
            self.assertIn("graph.json", stderr.getvalue())


class QueryWithStaleSidecarCliTest(unittest.TestCase):
    """``wd query`` must never crash on a sidecar with a stale schema version.

    Locks the prerequisite for the f0yn Option B follow-up (lazy
    inverted-index from sqlite for federation query) which bumps
    :data:`weld._sqlite_schema.SQLITE_SCHEMA_VERSION`. Every on-disk
    ``graph.db`` written by an older ``wd`` will then carry a stale
    stamp; the CLI must keep working against it. Behavior is verified
    end-to-end through :func:`graph_cli_main` (the same entry point
    ``python -m weld query`` invokes), so a regression that, for
    example, made the read path try to query a renamed column would
    surface here even if the unit-level guards still passed.
    """

    def _write_pair_with_stale_stamp(self, root: Path, stamp: str) -> Path:
        weld_dir = root / ".weld"
        weld_dir.mkdir(parents=True, exist_ok=True)
        graph_path = weld_dir / "graph.json"
        body = dumps_graph(_sample_graph()).encode("utf-8")
        graph_path.write_bytes(body)
        db_path = weld_dir / "graph.db"
        writer.build_sidecar_for_bytes(
            _sample_graph(), body, db_path, generated_at="t",
        )
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute(
                "UPDATE meta SET value = ? WHERE key = ?",
                (str(stamp), META_KEY_SQLITE_SCHEMA_VERSION),
            )
            conn.commit()
        finally:
            conn.close()
        return graph_path

    def test_query_succeeds_with_stale_schema_version(self) -> None:
        # Forward bump mirrors the f0yn Option B rollout: writer was a
        # newer ``wd``, reader is the current build. Backward and
        # garbage stamps exercise the same fallback branch with the
        # additional cases the unit test parametrises.
        for stamp in (str(SQLITE_SCHEMA_VERSION + 1), "0", "not-an-int"):
            with self.subTest(stamp=stamp):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    graph_path = self._write_pair_with_stale_stamp(
                        root, stamp,
                    )
                    # Sanity: the sidecar exists but is stale.
                    self.assertTrue((root / ".weld" / "graph.db").is_file())
                    self.assertFalse(reader.sidecar_freshness(graph_path)[0])

                    stdout = io.StringIO()
                    stderr = io.StringIO()
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        # Must NOT raise. JSON output keeps the assertion
                        # parse-safe and avoids relying on render text.
                        graph_cli_main([
                            "--root", str(root),
                            "query", "api", "--no-refresh", "--json",
                        ])
                    payload = json.loads(stdout.getvalue())
                    ids = {match["id"] for match in payload.get("matches", [])}
                    self.assertIn("service:api", ids)


if __name__ == "__main__":
    unittest.main()
