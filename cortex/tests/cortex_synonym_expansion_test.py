"""Tests for synonym/alias expansion in cortex query (project-8r0.10).

Verifies that conceptual queries like 'authentication', 'database', and
'pipeline' return relevant matches by expanding query terms into their
domain-specific aliases before the inverted index lookup.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import sys
_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from cortex.graph import Graph  # noqa: E402
from cortex.synonyms import SYNONYMS, expand_tokens  # noqa: E402

def _make_graph(nodes: dict, edges: list | None = None) -> Graph:
    """Create an in-memory Graph with the given nodes and edges."""
    tmp = tempfile.mkdtemp()
    g = Graph(Path(tmp))
    g._data = {
        "meta": {"version": 1},
        "nodes": nodes,
        "edges": edges or [],
    }
    g._build_inverted_index()
    return g

# ---------------------------------------------------------------------------
# Fixture nodes for synonym expansion tests
# ---------------------------------------------------------------------------

_SYNONYM_NODES: dict[str, dict] = {
    "file:services/api/auth/middleware": {
        "type": "file",
        "label": "auth middleware",
        "props": {
            "file": "services/api/auth/middleware.py",
            "exports": ["AuthMiddleware", "verify_token"],
        },
    },
    "file:services/api/auth/session": {
        "type": "file",
        "label": "session handler",
        "props": {
            "file": "services/api/auth/session.py",
            "exports": ["SessionManager", "create_session"],
        },
    },
    "file:services/api/auth/login": {
        "type": "file",
        "label": "login endpoint",
        "props": {
            "file": "services/api/auth/login.py",
            "exports": ["login_handler"],
        },
    },
    "entity:Store": {
        "type": "entity",
        "label": "Store",
        "props": {"table": "store"},
    },
    "file:services/api/db/connection": {
        "type": "file",
        "label": "database connection",
        "props": {
            "file": "services/api/db/connection.py",
            "exports": ["get_db", "DatabasePool"],
        },
    },
    "file:services/api/models/schema": {
        "type": "file",
        "label": "schema definitions",
        "props": {
            "file": "services/api/models/schema.py",
            "exports": ["Base", "metadata"],
        },
    },
    "file:alembic/migrations/001": {
        "type": "file",
        "label": "migration 001",
        "props": {
            "file": "alembic/migrations/001_initial.py",
            "exports": ["upgrade", "downgrade"],
        },
    },
    "file:services/worker/pipeline/acquire": {
        "type": "file",
        "label": "acquisition stage",
        "props": {
            "file": "services/worker/pipeline/acquire.py",
            "exports": ["AcquireStage"],
        },
    },
    "file:services/worker/pipeline/extract": {
        "type": "file",
        "label": "extraction stage",
        "props": {
            "file": "services/worker/pipeline/extract.py",
            "exports": ["ExtractStage"],
        },
    },
    "file:services/worker/tasks": {
        "type": "file",
        "label": "worker tasks",
        "props": {
            "file": "services/worker/tasks.py",
            "exports": ["process_job", "schedule_task"],
        },
    },
}

class ExpandTokensTest(unittest.TestCase):
    """Tests for the expand_tokens function."""

    def test_known_synonym_expands(self) -> None:
        """A known synonym like 'authentication' expands to aliases."""
        expanded = expand_tokens(["authentication"])
        self.assertIn("authentication", expanded)
        self.assertIn("auth", expanded)

    def test_unknown_term_passes_through(self) -> None:
        """Unknown terms are returned unchanged."""
        expanded = expand_tokens(["xyznonexistent"])
        self.assertEqual(expanded, ["xyznonexistent"])

    def test_multiple_tokens_expand_independently(self) -> None:
        """Each token is expanded independently."""
        expanded = expand_tokens(["authentication", "xyzunknown"])
        self.assertIn("auth", expanded)
        self.assertIn("xyzunknown", expanded)

    def test_expansion_is_lowercased(self) -> None:
        """All expanded tokens are lowercase."""
        expanded = expand_tokens(["database"])
        for token in expanded:
            self.assertEqual(token, token.lower())

    def test_synonym_table_has_core_entries(self) -> None:
        """The synonym table covers authentication, database, and pipeline."""
        self.assertIn("authentication", SYNONYMS)
        self.assertIn("database", SYNONYMS)
        self.assertIn("pipeline", SYNONYMS)

    def test_no_duplicates_in_expansion(self) -> None:
        """Expanded tokens should not contain duplicates."""
        expanded = expand_tokens(["authentication"])
        self.assertEqual(len(expanded), len(set(expanded)))

    def test_empty_input_returns_empty(self) -> None:
        """Empty token list returns empty."""
        expanded = expand_tokens([])
        self.assertEqual(expanded, [])

class SynonymQueryIntegrationTest(unittest.TestCase):
    """Integration tests: synonym expansion in Graph.query()."""

    def setUp(self) -> None:
        self.graph = _make_graph(_SYNONYM_NODES)

    def test_authentication_finds_auth_nodes(self) -> None:
        """Querying 'authentication' should find auth-related nodes."""
        result = self.graph.query("authentication")
        ids = [m["id"] for m in result["matches"]]
        self.assertTrue(len(ids) > 0, "'authentication' should find matches via synonym expansion")
        # Should find at least the auth middleware or session nodes
        auth_ids = [i for i in ids if "auth" in i]
        self.assertTrue(len(auth_ids) > 0, "should find auth-related nodes")

    def test_database_finds_db_nodes(self) -> None:
        """Querying 'database' should find db-related nodes."""
        result = self.graph.query("database")
        ids = [m["id"] for m in result["matches"]]
        self.assertTrue(len(ids) > 0, "'database' should find matches via synonym expansion")

    def test_pipeline_finds_worker_nodes(self) -> None:
        """Querying 'pipeline' should find pipeline-related nodes."""
        result = self.graph.query("pipeline")
        ids = [m["id"] for m in result["matches"]]
        self.assertTrue(len(ids) > 0, "'pipeline' should find matches")

    def test_direct_term_still_works(self) -> None:
        """Direct terms that match without expansion still work."""
        result = self.graph.query("auth")
        ids = [m["id"] for m in result["matches"]]
        self.assertTrue(len(ids) > 0, "direct 'auth' should still find auth nodes")

    def test_synonym_expansion_does_not_break_multi_token(self) -> None:
        """Multi-token queries with synonyms should still work."""
        result = self.graph.query("authentication middleware")
        ids = [m["id"] for m in result["matches"]]
        # Should find auth middleware since 'authentication' expands to 'auth'
        self.assertIn("file:services/api/auth/middleware", ids)

    def test_unrelated_term_returns_empty(self) -> None:
        """Unrelated terms still return empty results."""
        result = self.graph.query("zzzznonexistent99")
        self.assertEqual(len(result["matches"]), 0)

if __name__ == "__main__":
    unittest.main()
