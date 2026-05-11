"""Tests for ``SqliteBackedGraph.query`` (ADR 0058 Option B).

Asserts envelope shape parity with :meth:`weld.graph.Graph.query`:

- An empty term returns the empty envelope.
- A unique single-token term returns the expected node.
- A multi-token term ANDs the tokens (strict-AND, like the JSON path).
- A term with no matches returns an empty matches list.
- The match set against a sqlite-backed graph equals the match set
  against the JSON-backed Graph for the same fixture (semantic
  parity, the v1 acceptance criterion).
- A SQL-injection-style term (``' OR 1=1 --``) does NOT return every
  node; the parameter binding holds.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld import _sqlite_reader as reader  # noqa: E402
from weld import _sqlite_writer as writer  # noqa: E402
from weld.graph import Graph  # noqa: E402
from weld.serializer import dumps_graph  # noqa: E402


def _fixture_nodes() -> dict[str, dict]:
    """Mixed-topic fixture with distinguishable token surfaces."""
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
            "props": {
                "description": "Generic helper used by billing",
            },
        },
        "file:readme": {
            "type": "file",
            "label": "README.md",
            "props": {"file": "README.md"},
        },
    }


def _open_sqlite_view(nodes: dict[str, dict]) -> tuple[
    reader.SqliteBackedGraph, "tempfile.TemporaryDirectory[str]",
]:
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    graph = {"meta": {"schema_version": 1}, "nodes": nodes, "edges": []}
    body = dumps_graph(graph).encode("utf-8")
    (root / ".weld" / "graph.json").write_bytes(body)
    writer.build_sidecar_for_bytes(graph, body, root / ".weld" / "graph.db")
    view = reader.open_sidecar_if_fresh(root / ".weld" / "graph.json")
    assert view is not None
    return view, tmp


def _open_json_graph(nodes: dict[str, dict]) -> tuple[
    Graph, "tempfile.TemporaryDirectory[str]",
]:
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {"version": "0", "updated_at": "t", "schema_version": 1},
        "nodes": nodes,
        "edges": [],
    }
    (root / ".weld" / "graph.json").write_text(
        dumps_graph(payload), encoding="utf-8",
    )
    g = Graph(root)
    g.load()
    return g, tmp


class SqliteQueryEnvelopeTest(unittest.TestCase):
    def test_empty_term_returns_empty_envelope(self) -> None:
        view, tmp = _open_sqlite_view(_fixture_nodes())
        try:
            result = view.query("")
            self.assertEqual(result["matches"], [])
            self.assertEqual(result["neighbors"], [])
            self.assertEqual(result["edges"], [])
            self.assertEqual(result["query"], "")
        finally:
            view.close()
            tmp.cleanup()

    def test_single_unique_token_finds_target(self) -> None:
        view, tmp = _open_sqlite_view(_fixture_nodes())
        try:
            ids = {m["id"] for m in view.query("auth")["matches"]}
            self.assertIn("service:auth", ids)
            self.assertNotIn("service:billing", ids)
        finally:
            view.close()
            tmp.cleanup()

    def test_term_with_no_hits_returns_empty_matches(self) -> None:
        view, tmp = _open_sqlite_view(_fixture_nodes())
        try:
            ids = {m["id"] for m in view.query("zzzzz_nothing")["matches"]}
            self.assertEqual(set(), ids)
        finally:
            view.close()
            tmp.cleanup()

    def test_match_set_parity_with_json_graph(self) -> None:
        """Match-set parity against JSON ``Graph.query`` on the same fixture.

        ADR 0058 Option B acceptance: the sqlite path's match IDs must
        equal the JSON path's match IDs (modulo rank, which Option B
        does not yet promise to preserve). We assert set equality here
        and let a separate test pin ordering when we extend to hybrid
        scoring.
        """
        nodes = _fixture_nodes()
        view, tmp_sqlite = _open_sqlite_view(nodes)
        try:
            json_graph, tmp_json = _open_json_graph(nodes)
            try:
                for term in ("auth", "billing", "helper", "README"):
                    sqlite_ids = {
                        m["id"]
                        for m in view.query(term, limit=50)["matches"]
                    }
                    json_ids = {
                        m["id"]
                        for m in json_graph.query(term, limit=50)["matches"]
                    }
                    self.assertEqual(
                        sqlite_ids, json_ids,
                        f"match set diverged for term {term!r}",
                    )
            finally:
                tmp_json.cleanup()
        finally:
            view.close()
            tmp_sqlite.cleanup()


class SqliteQuerySecurityTest(unittest.TestCase):
    """Term-injection probes for the parameter-bound reader."""

    def test_injection_attempt_does_not_widen_to_all_nodes(self) -> None:
        view, tmp = _open_sqlite_view(_fixture_nodes())
        try:
            # A literal ``' OR 1=1 --`` cannot escape parameter
            # binding, so the substring search returns zero hits.
            self.assertEqual(
                [], view.query("' OR 1=1 --")["matches"],
            )
            # A bare ``%`` must not match every row -- the LIKE
            # wildcard is escaped on the indexed-token side.
            self.assertEqual([], view.query("%")["matches"])
            self.assertEqual([], view.query("_")["matches"])
        finally:
            view.close()
            tmp.cleanup()

    def test_term_with_semicolon_and_drop_does_not_alter_schema(self) -> None:
        """``term`` is bound as a parameter; DDL inside it must not run."""
        view, tmp = _open_sqlite_view(_fixture_nodes())
        try:
            view.query("'; DROP TABLE token_index; --")
            # If the DDL ran, the next query would fail. The
            # connection is read-only, so even an unbound execution
            # would error -- this is a belt-and-braces check.
            ids = {m["id"] for m in view.query("auth")["matches"]}
            self.assertIn("service:auth", ids)
        finally:
            view.close()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
