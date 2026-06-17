"""Tests for synonym/alias expansion in wd query (tracked project).

Verifies that conceptual queries like 'authentication', 'database', and
'pipeline' return relevant matches by expanding query terms into their
domain-specific aliases before the inverted index lookup.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


from weld.graph import Graph  # noqa: E402
from weld.synonyms import (  # noqa: E402
    SYNONYMS,
    _stem_variants,
    expand_token_groups,
    expand_tokens,
)

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
    # Path tokenizes to 'strategies' (plural); a singular 'strategy' query
    # must reach it via stem-equivalence (bd 8rm0.2 / ADR 0075 part 3).
    "file:weld/strategies/boundary_entrypoint": {
        "type": "file",
        "label": "boundary_entrypoint",
        "props": {"file": "weld/strategies/boundary_entrypoint.py"},
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

class StemVariantsTest(unittest.TestCase):
    """Tests for the _stem_variants singular/plural helper (bd 8rm0.2).

    Two symmetric rules with over-stem guards: ``-ies <-> -y`` and the
    simple ``-s <-> (null)`` plural. The helper underpins stem-equivalence
    at the ``expand_token_groups`` seam (ADR 0075 part 3).
    """

    def test_strategy_and_strategies_are_equivalent(self) -> None:
        """The motivating pair: strategy <-> strategies (both directions)."""
        self.assertIn("strategies", _stem_variants("strategy"))
        self.assertIn("strategy", _stem_variants("strategies"))

    def test_simple_s_plural_is_symmetric(self) -> None:
        """test <-> tests via the -s rule, both directions."""
        self.assertIn("tests", _stem_variants("test"))
        self.assertIn("test", _stem_variants("tests"))

    def test_ies_y_pair_beyond_the_motivating_word(self) -> None:
        """entry <-> entries exercises the -ies/-y rule generally."""
        self.assertIn("entries", _stem_variants("entry"))
        self.assertIn("entry", _stem_variants("entries"))

    def test_short_words_are_not_over_stemmed(self) -> None:
        """Guard: tiny words must not collapse to noise variants."""
        for word in ("is", "as", "os", "db", "id"):
            self.assertEqual(
                _stem_variants(word), [],
                f"{word!r} should not be stemmed",
            )

    def test_non_plural_s_endings_are_not_stripped(self) -> None:
        """Guard: -ss/-us/-is/-as endings are not naive plurals."""
        for word in ("class", "css", "bus", "status", "process"):
            self.assertNotIn(
                word[:-1], _stem_variants(word),
                f"{word!r} should not strip a trailing -s",
            )

    def test_vowel_plus_y_is_not_pluralized_to_ies(self) -> None:
        """Guard: 'day'/'key' must not become 'daies'/'keies'."""
        self.assertNotIn("daies", _stem_variants("day"))
        self.assertNotIn("keies", _stem_variants("key"))

    def test_does_not_return_the_input_itself(self) -> None:
        """The input token is never echoed back as its own variant."""
        for word in ("strategy", "strategies", "test", "tests"):
            self.assertNotIn(word, _stem_variants(word))

    def test_empty_string_is_safe(self) -> None:
        """Degenerate empty input yields no variants, no error."""
        self.assertEqual(_stem_variants(""), [])

class ExpandTokenGroupsStemmingTest(unittest.TestCase):
    """Stem variants are folded into the SAME group (bd 8rm0.2)."""

    def test_singular_query_group_includes_plural_stem(self) -> None:
        """'strategy' group carries 'strategies' so it matches the path."""
        groups = expand_token_groups(["strategy"])
        self.assertEqual(len(groups), 1)
        self.assertIn("strategy", groups[0])
        self.assertIn("strategies", groups[0])

    def test_plural_query_group_includes_singular_stem(self) -> None:
        """Symmetry: 'strategies' group also carries 'strategy'."""
        groups = expand_token_groups(["strategies"])
        self.assertIn("strategy", groups[0])

    def test_stem_added_alongside_synonym_aliases(self) -> None:
        """'test' keeps its synonym aliases AND gains the 'tests' stem."""
        groups = expand_token_groups(["test"])
        self.assertIn("tests", groups[0])
        # Existing synonym aliases for 'test' are preserved.
        self.assertIn("fixture", groups[0])

    def test_group_has_no_duplicates(self) -> None:
        """Folding stems must not introduce duplicate tokens in a group."""
        for token in ("strategy", "test", "entry", "authentication"):
            group = expand_token_groups([token])[0]
            self.assertEqual(
                len(group), len(set(group)),
                f"group for {token!r} has duplicates: {group}",
            )

    def test_one_group_per_original_token_preserved(self) -> None:
        """Stemming must not change the group-per-token cardinality."""
        groups = expand_token_groups(["strategy", "test"])
        self.assertEqual(len(groups), 2)

class StemEquivalenceQueryIntegrationTest(unittest.TestCase):
    """End-to-end: a singular query reaches a plural-path node (bd 8rm0.2)."""

    def setUp(self) -> None:
        self.graph = _make_graph(_SYNONYM_NODES)

    def test_singular_strategy_matches_strategies_path(self) -> None:
        """'strategy' surfaces weld/strategies/boundary_entrypoint."""
        result = self.graph.query("strategy")
        ids = [m["id"] for m in result["matches"]]
        self.assertIn("file:weld/strategies/boundary_entrypoint", ids)

if __name__ == "__main__":
    unittest.main()
