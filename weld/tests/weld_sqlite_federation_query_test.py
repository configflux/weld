"""Federation query rewire over sqlite-backed children (ADR 0058 Option B).

Validates the federation-side wiring of ``SqliteBackedGraph.query``:

- A child with a fresh sidecar surfaces matches via the sqlite path
  (the federation never parses its ``graph.json`` for the query).
- A child with a stale sidecar transparently falls through to the
  JSON-backed :class:`Graph` query (the graceful-fallback
  guarantee covers this).
- A multi-child federation answers the same query consistently across
  sqlite + JSON children (mixed mode).
- The cache-miss observation: when every child is sqlite-backed, the
  JSON child cache stays empty after a query.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


from weld._sqlite_reader import SqliteBackedGraph  # noqa: E402
from weld.federation import FederatedGraph  # noqa: E402
from weld.graph import Graph  # noqa: E402
from weld.tests._federation_sqlite_fixtures import (  # noqa: E402
    graph_payload,
    make_workspace,
)


def _child_payload(label: str) -> dict:
    return graph_payload({
        f"service:{label}": {
            "type": "service",
            "label": label,
            "props": {
                "file": f"{label}/main.py",
                "description": f"{label} service surface",
            },
        },
        f"symbol:{label}_helper": {
            "type": "symbol",
            "label": f"{label}_helper",
            "props": {"description": f"helper for {label}"},
        },
    })


class FederationSqliteQueryTest(unittest.TestCase):
    def test_sqlite_child_serves_query_without_json_parse(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(
                root,
                children=[("alpha", _child_payload("alpha"), True)],
            )
            fg = FederatedGraph(root)
            try:
                # Sanity: handle is sqlite-backed.
                self.assertIsInstance(
                    fg._load_child("alpha"), SqliteBackedGraph,
                )
                ids = {m["id"] for m in fg.query("alpha", limit=10)["matches"]}
                self.assertIn("alpha\x1fservice:alpha", ids)
                # JSON cache must be empty: the sqlite path was used.
                self.assertEqual(
                    0, len(fg._child_cache),
                    "sqlite query path must not populate the JSON cache",
                )
            finally:
                fg.close()

    def test_stale_sidecar_falls_back_to_json(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(
                root,
                children=[("beta", _child_payload("beta"), True)],
            )
            # Make the sidecar stale by appending to graph.json.
            graph_path = root / "beta" / ".weld" / "graph.json"
            graph_path.write_bytes(graph_path.read_bytes() + b"\n")
            fg = FederatedGraph(root)
            try:
                # _load_child must drop the stale sidecar and return a
                # JSON-backed Graph (not a SqliteBackedGraph).
                child = fg._load_child("beta")
                self.assertIsInstance(child, Graph)
                self.assertNotIsInstance(child, SqliteBackedGraph)
                # The query path must still find matches via JSON.
                ids = {m["id"] for m in fg.query("beta", limit=10)["matches"]}
                self.assertIn("beta\x1fservice:beta", ids)
            finally:
                fg.close()

    def test_missing_sidecar_falls_back_to_json(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(
                root,
                children=[("gamma", _child_payload("gamma"), False)],
            )
            fg = FederatedGraph(root)
            try:
                ids = {m["id"] for m in fg.query("gamma", limit=10)["matches"]}
                self.assertIn("gamma\x1fservice:gamma", ids)
            finally:
                fg.close()

    def test_mixed_sidecar_and_json_children_answer_query(self) -> None:
        """Sqlite + JSON siblings both surface their matches."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(
                root,
                children=[
                    ("alpha", _child_payload("alpha"), True),
                    ("beta", _child_payload("beta"), False),
                ],
            )
            fg = FederatedGraph(root)
            try:
                # Each child surfaces its own match.
                ids_alpha = {
                    m["id"]
                    for m in fg.query("alpha", limit=10)["matches"]
                }
                ids_beta = {
                    m["id"]
                    for m in fg.query("beta", limit=10)["matches"]
                }
                self.assertIn("alpha\x1fservice:alpha", ids_alpha)
                self.assertIn("beta\x1fservice:beta", ids_beta)
            finally:
                fg.close()

    def test_match_set_parity_sqlite_vs_json_federation(self) -> None:
        """A sqlite-backed federation returns the same match IDs as JSON.

        Acceptance for Option B: ``wd query <term>`` produces the
        same hits across the lazy and the eager paths for an identical
        fixture graph. Rank can drift slightly (only BM25 today on the
        sqlite side); set equality is what matters here.
        """
        with TemporaryDirectory() as tmp_sqlite:
            sqlite_root = Path(tmp_sqlite)
            make_workspace(
                sqlite_root,
                children=[
                    ("alpha", _child_payload("alpha"), True),
                    ("beta", _child_payload("beta"), True),
                ],
            )
            fg_sqlite = FederatedGraph(sqlite_root)
            try:
                with TemporaryDirectory() as tmp_json:
                    json_root = Path(tmp_json)
                    make_workspace(
                        json_root,
                        children=[
                            ("alpha", _child_payload("alpha"), False),
                            ("beta", _child_payload("beta"), False),
                        ],
                    )
                    fg_json = FederatedGraph(json_root)
                    try:
                        for term in ("alpha", "beta", "helper", "service"):
                            sqlite_ids = {
                                m["id"]
                                for m in fg_sqlite.query(term, limit=20)["matches"]
                            }
                            json_ids = {
                                m["id"]
                                for m in fg_json.query(term, limit=20)["matches"]
                            }
                            self.assertEqual(
                                sqlite_ids, json_ids,
                                f"federation match-set drift on {term!r}",
                            )
                    finally:
                        fg_json.close()
            finally:
                fg_sqlite.close()


if __name__ == "__main__":
    unittest.main()
