"""Multi-graph federation-relevant sqlite tests (ADR 0058).

Two layers of coverage:

1. The per-graph sidecar contract: each child can be opened via
   :meth:`Graph.open` and the sidecar is honoured exactly as for the
   single-repo case. Two independent children's sidecars can be opened
   concurrently without cross-contamination; a stale child sidecar
   does not poison a fresh sibling's reads.
2. The federation rewire (this task): ``FederatedGraph._load_child``
   returns a :class:`SqliteBackedGraph` when the child's sidecar is
   fresh, and the federation read paths (``get_node``,
   ``_exact_context``, ``_adjacent``, ``path``) operate correctly on
   that lazy handle. ``query`` continues to use the JSON-backed
   :class:`Graph` because the inverted index has not yet been
   reconstructed from sqlite (out of scope per the ADR 0058 v1 plan).
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


from weld import _sqlite_reader as reader  # noqa: E402
from weld._sqlite_schema import (  # noqa: E402
    META_KEY_SQLITE_SCHEMA_VERSION,
    SQLITE_SCHEMA_VERSION,
)
from weld.federation import FederatedGraph  # noqa: E402
from weld.graph import Graph  # noqa: E402
from weld.tests._federation_sqlite_fixtures import (  # noqa: E402
    graph_payload,
    make_workspace,
    write_child,
)


class MultiChildSidecarTest(unittest.TestCase):
    def test_independent_children_open_concurrently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a_path, _ = write_child(root, "alpha", "alpha")
            b_path, _ = write_child(root, "beta", "beta")
            a = reader.open_sidecar_if_fresh(a_path)
            b = reader.open_sidecar_if_fresh(b_path)
            self.assertIsNotNone(a)
            self.assertIsNotNone(b)
            assert a is not None and b is not None
            try:
                self.assertEqual(a.get_node("service:alpha")["label"], "alpha")
                self.assertEqual(b.get_node("service:beta")["label"], "beta")
                # Cross-pollution check: alpha's sidecar must not see beta's node.
                self.assertIsNone(a.get_node("service:beta"))
                self.assertIsNone(b.get_node("service:alpha"))
            finally:
                a.close()
                b.close()

    def test_stale_child_does_not_poison_sibling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a_path, _ = write_child(root, "alpha", "alpha")
            b_path, _ = write_child(root, "beta", "beta")
            # Stale alpha by appending bytes; beta remains fresh.
            a_path.write_bytes(a_path.read_bytes() + b"\n")
            self.assertFalse(reader.sidecar_freshness(a_path)[0])
            self.assertTrue(reader.sidecar_freshness(b_path)[0])


# ---------------------------------------------------------------------------
# FederatedGraph rewire tests (ADR 0058 follow-up)
# ---------------------------------------------------------------------------


class FederationLoadChildSqliteTest(unittest.TestCase):
    """`_load_child` activates the sqlite path when sidecar is fresh."""

    def test_returns_sqlite_when_sidecar_fresh(self) -> None:
        payload = graph_payload({
            "service:alpha": {
                "type": "service",
                "label": "alpha",
                "props": {"file": "alpha.py"},
            },
        })
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(
                root,
                children=[("child-a", payload, True)],
            )
            fg = FederatedGraph(root)
            loaded = fg._load_child("child-a")
            self.assertIsInstance(loaded, reader.SqliteBackedGraph)

    def test_falls_back_to_graph_when_sidecar_absent(self) -> None:
        payload = graph_payload({
            "service:alpha": {
                "type": "service",
                "label": "alpha",
                "props": {},
            },
        })
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(
                root,
                children=[("child-a", payload, False)],
            )
            fg = FederatedGraph(root)
            loaded = fg._load_child("child-a")
            self.assertIsInstance(loaded, Graph)
            self.assertNotIsInstance(loaded, reader.SqliteBackedGraph)

    def test_falls_back_to_graph_when_sidecar_stale(self) -> None:
        payload = graph_payload({
            "service:alpha": {
                "type": "service",
                "label": "alpha",
                "props": {},
            },
        })
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(
                root,
                children=[("child-a", payload, True)],
            )
            # Stale: append bytes to the JSON so source_json_sha mismatches.
            graph_path = root / "child-a" / ".weld" / "graph.json"
            graph_path.write_bytes(graph_path.read_bytes() + b"\n")
            fg = FederatedGraph(root)
            loaded = fg._load_child("child-a")
            # Either Graph (JSON parse succeeded) or CorruptChild (parse failed).
            # The trailing newline is benign for the JSON parser, so we expect Graph.
            self.assertIsInstance(loaded, Graph)
            self.assertNotIsInstance(loaded, reader.SqliteBackedGraph)


class FederationReadPathSqliteTest(unittest.TestCase):
    """Read paths (get_node / context / path / adjacent) work over sqlite."""

    def _build_payload(self) -> dict:
        return graph_payload(
            nodes={
                "service:alpha": {
                    "type": "service",
                    "label": "alpha",
                    "props": {"file": "alpha.py"},
                },
                "service:beta": {
                    "type": "service",
                    "label": "beta",
                    "props": {"file": "beta.py"},
                },
            },
            edges=[
                {
                    "from": "service:alpha",
                    "to": "service:beta",
                    "type": "calls",
                    "props": {"confidence": "high"},
                },
            ],
        )

    def test_get_node_via_sqlite_returns_prefixed_node(self) -> None:
        payload = self._build_payload()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(root, children=[("child-a", payload, True)])
            fg = FederatedGraph(root)
            # Sanity: the loaded child is sqlite-backed.
            self.assertIsInstance(
                fg._load_child("child-a"),
                reader.SqliteBackedGraph,
            )
            node = fg.get_node("child-a\x1fservice:alpha")
            self.assertIsNotNone(node)
            assert node is not None
            self.assertEqual(node["type"], "service")
            self.assertEqual(node["label"], "alpha")
            self.assertEqual(node["display_id"], "child-a::service:alpha")

    def test_exact_context_via_sqlite(self) -> None:
        payload = self._build_payload()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(root, children=[("child-a", payload, True)])
            fg = FederatedGraph(root)
            ctx = fg.context("child-a\x1fservice:alpha", fallback=False)
            self.assertNotIn("error", ctx)
            self.assertEqual(ctx["node"]["id"], "child-a\x1fservice:alpha")
            neighbor_ids = sorted(n["id"] for n in ctx["neighbors"])
            self.assertIn("child-a\x1fservice:beta", neighbor_ids)
            # The edge is prefixed.
            self.assertTrue(any(
                e["from"] == "child-a\x1fservice:alpha"
                and e["to"] == "child-a\x1fservice:beta"
                for e in ctx["edges"]
            ))

    def test_path_via_sqlite_children(self) -> None:
        payload = self._build_payload()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(root, children=[("child-a", payload, True)])
            fg = FederatedGraph(root)
            result = fg.path(
                "child-a\x1fservice:alpha",
                "child-a\x1fservice:beta",
            )
            self.assertIsNotNone(result["path"])
            ids = [n["id"] for n in result["path"]]
            self.assertEqual(
                ids,
                ["child-a\x1fservice:alpha", "child-a\x1fservice:beta"],
            )

    def test_query_falls_back_to_json_for_sqlite_children(self) -> None:
        """``query`` requires the inverted index, so the JSON path is used.

        The user-visible behavior must be identical to the pre-sqlite
        federation: matches surface from each present child.
        """
        payload = self._build_payload()
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(root, children=[("child-a", payload, True)])
            fg = FederatedGraph(root)
            result = fg.query("alpha", limit=5)
            ids = {m["id"] for m in result["matches"]}
            self.assertIn("child-a\x1fservice:alpha", ids)


class FederationStaleSchemaVersionTest(unittest.TestCase):
    """Regression: child sidecar with stale schema version falls back to JSON.

    Locks the prerequisite for the f0yn Option B follow-up. After the
    bump, every previously written child ``graph.db`` carries a stale
    :data:`SQLITE_SCHEMA_VERSION` stamp. The federation must keep
    returning matches by reading the canonical JSON, not crash and not
    silently drop the child. Covers both the sidecar-preferring
    ``_load_child`` path (used by ``get_node`` / context / path) and
    the JSON-only ``_load_child_for_query`` path used by ``query``.
    """

    def _stamp_child_sidecar(
        self, root: Path, child_name: str, stamp: str,
    ) -> None:
        db_path = root / child_name / ".weld" / "graph.db"
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute(
                "UPDATE meta SET value = ? WHERE key = ?",
                (str(stamp), META_KEY_SQLITE_SCHEMA_VERSION),
            )
            conn.commit()
        finally:
            conn.close()

    def _build_payload(self) -> dict:
        return graph_payload({
            "service:alpha": {
                "type": "service",
                "label": "alpha",
                "props": {"file": "alpha.py"},
            },
        })

    def test_load_child_falls_back_when_schema_version_stale(self) -> None:
        for stamp in (str(SQLITE_SCHEMA_VERSION + 1), "0", "not-an-int"):
            with self.subTest(stamp=stamp):
                with TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    make_workspace(
                        root,
                        children=[("child-a", self._build_payload(), True)],
                    )
                    self._stamp_child_sidecar(root, "child-a", stamp)
                    fg = FederatedGraph(root)
                    try:
                        loaded = fg._load_child("child-a")
                        # Must be the JSON-backed Graph -- the
                        # SqliteBackedGraph fast-path is rejected when
                        # the stamp does not match runtime.
                        self.assertIsInstance(loaded, Graph)
                        self.assertNotIsInstance(
                            loaded, reader.SqliteBackedGraph,
                        )
                    finally:
                        fg.close()

    def test_query_returns_matches_when_child_sidecar_stale(self) -> None:
        for stamp in (str(SQLITE_SCHEMA_VERSION + 1), "0", "not-an-int"):
            with self.subTest(stamp=stamp):
                with TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    make_workspace(
                        root,
                        children=[("child-a", self._build_payload(), True)],
                    )
                    self._stamp_child_sidecar(root, "child-a", stamp)
                    fg = FederatedGraph(root)
                    try:
                        result = fg.query("alpha", limit=5)
                        ids = {m["id"] for m in result["matches"]}
                        # Prefixed child id form (UNIT SEPARATOR).
                        self.assertIn("child-a\x1fservice:alpha", ids)
                    finally:
                        fg.close()


class FederationChildrenStatusSqliteTest(unittest.TestCase):
    """``children_status`` reports a sqlite-backed child as ``present``.

    Regression: prior to the ADR 0058 rewire the method assumed any
    non-:class:`Graph` value was a sentinel and would AttributeError
    on :class:`SqliteBackedGraph`'s missing ``.status`` field.
    """

    def test_status_includes_sqlite_children_as_present(self) -> None:
        payload = graph_payload({
            "service:alpha": {
                "type": "service",
                "label": "alpha",
                "props": {},
            },
        })
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(root, children=[("child-a", payload, True)])
            fg = FederatedGraph(root)
            status = fg.children_status()
            self.assertIn("child-a", status)
            self.assertEqual(status["child-a"]["status"], "present")


if __name__ == "__main__":
    unittest.main()
