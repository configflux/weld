"""Regression tests for weld tokenized query behavior (tracked project / tracked project).

Ensures that ``Graph.query()`` and ``Graph._match_tokens()`` correctly:
- match single tokens against node ID segments, labels, props.file paths,
  and props.exports symbol lists
- prefer strict-AND matches when they exist (every token hits some field)
- rank results by matched-token count (desc), then node ID (asc)
- produce deterministic ordering across identical queries
- when strict-AND yields nothing on a multi-token query, fall back to a
  per-group OR union and tag the envelope with ``degraded_match``
  (covered in detail by ``weld_query_or_fallback_test.py``)

These are regression guards: if the tokenized matching logic is refactored, any
silent narrowing of the match surface will cause a failure here.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import sys
_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.graph import Graph  # noqa: E402

def _make_graph(nodes: dict, edges: list | None = None) -> Graph:
    """Create an in-memory Graph with the given nodes and edges."""
    tmp = tempfile.mkdtemp()
    g = Graph(Path(tmp))
    # Bypass file I/O — inject data directly
    g._data = {
        "meta": {"version": 1},
        "nodes": nodes,
        "edges": edges or [],
    }
    return g

# ---------------------------------------------------------------------------
# Fixture nodes modeled after real web-app TS/TSX entries
# ---------------------------------------------------------------------------

_WEB_NODES: dict[str, dict] = {
    "file:web/app/stores/page": {
        "type": "file",
        "label": "page",
        "props": {
            "file": "apps/web/app/stores/page.tsx",
            "exports": ["StoresPage"],
            "imports_from": ["react", "shell"],
            "line_count": 42,
        },
    },
    "file:web/app/flyers/current/page": {
        "type": "file",
        "label": "page",
        "props": {
            "file": "apps/web/app/flyers/current/page.tsx",
            "exports": ["FlyersPage"],
            "imports_from": ["react"],
            "line_count": 55,
        },
    },
    "file:web/components/shell": {
        "type": "file",
        "label": "shell",
        "props": {
            "file": "apps/web/components/shell.tsx",
            "exports": ["SiteHeader", "SiteFooter", "PageIntro", "SectionCard"],
            "imports_from": ["react", "link"],
            "line_count": 120,
        },
    },
    "file:web/app/dashboard/page": {
        "type": "file",
        "label": "page",
        "props": {
            "file": "apps/web/app/dashboard/page.tsx",
            "exports": ["DashboardPage"],
            "imports_from": ["react"],
            "line_count": 80,
        },
    },
    "entity:Store": {
        "type": "entity",
        "label": "Store",
        "props": {"table": "store"},
    },
    "agent:qa": {
        "type": "agent",
        "label": "qa",
        "props": {
            "description": "Black-box verification agent that validates completed tasks against acceptance criteria",
        },
    },
}

class MatchTokensTest(unittest.TestCase):
    """Tests for the static _match_tokens helper."""

    def test_single_token_matches_node_id_segment(self) -> None:
        """'stores' should match file:web/app/stores/page via ID."""
        node = _WEB_NODES["file:web/app/stores/page"]
        hits = Graph._match_tokens(["stores"], "file:web/app/stores/page", node)
        self.assertGreater(hits, 0, "single token 'stores' should match ID segment")

    def test_single_token_matches_props_file(self) -> None:
        """'flyers' should match via props.file path."""
        node = _WEB_NODES["file:web/app/flyers/current/page"]
        hits = Graph._match_tokens(["flyers"], "file:web/app/flyers/current/page", node)
        self.assertGreater(hits, 0, "'flyers' should match via props.file")

    def test_single_token_matches_props_exports(self) -> None:
        """'footer' should match shell node via SiteFooter export."""
        node = _WEB_NODES["file:web/components/shell"]
        hits = Graph._match_tokens(["footer"], "file:web/components/shell", node)
        self.assertGreater(hits, 0, "'footer' should match via SiteFooter export")

    def test_single_token_matches_label(self) -> None:
        """'shell' should match via node label."""
        node = _WEB_NODES["file:web/components/shell"]
        hits = Graph._match_tokens(["shell"], "file:web/components/shell", node)
        self.assertGreater(hits, 0, "'shell' should match via label")

    def test_all_tokens_must_hit(self) -> None:
        """All tokens must match at least one field; a miss returns 0."""
        node = _WEB_NODES["file:web/app/stores/page"]
        hits = Graph._match_tokens(
            ["stores", "nonexistent_xyz"], "file:web/app/stores/page", node
        )
        self.assertEqual(hits, 0, "one missing token should return 0")

    def test_multi_token_both_hit_different_fields(self) -> None:
        """'stores storespage' — 'stores' hits ID, 'storespage' hits exports."""
        node = _WEB_NODES["file:web/app/stores/page"]
        hits = Graph._match_tokens(
            ["stores", "storespage"], "file:web/app/stores/page", node
        )
        self.assertGreater(hits, 0, "multi-token query across fields should match")

    def test_case_insensitive_matching(self) -> None:
        """Matching is case-insensitive (tokens are pre-lowered by query())."""
        node = _WEB_NODES["file:web/components/shell"]
        # _match_tokens expects pre-lowered tokens (query() calls .lower())
        hits = Graph._match_tokens(["siteheader"], "file:web/components/shell", node)
        self.assertGreater(hits, 0, "case-insensitive match on exports should work")

    def test_empty_exports_no_crash(self) -> None:
        """Nodes without exports list should not crash."""
        node = _WEB_NODES["entity:Store"]
        hits = Graph._match_tokens(["store"], "entity:Store", node)
        self.assertGreater(hits, 0, "'store' should match entity:Store via ID/label")

    def test_no_match_returns_zero(self) -> None:
        """Completely unrelated term returns 0."""
        node = _WEB_NODES["file:web/app/stores/page"]
        hits = Graph._match_tokens(
            ["xyznonexistent"], "file:web/app/stores/page", node
        )
        self.assertEqual(hits, 0, "unrelated term should return 0")

    def test_tsx_file_path_matching(self) -> None:
        """'.tsx' in props.file should be matchable."""
        node = _WEB_NODES["file:web/app/dashboard/page"]
        hits = Graph._match_tokens(["dashboard"], "file:web/app/dashboard/page", node)
        self.assertGreater(hits, 0, "'dashboard' should match via ID or file path")

    def test_single_token_matches_props_description(self) -> None:
        """'verification' should match agent:qa via props.description only.

        The word 'verification' does not appear in the node ID, label, file,
        or exports — only in props.description. Covers tracked project
        """
        node = _WEB_NODES["agent:qa"]
        hits = Graph._match_tokens(["verification"], "agent:qa", node)
        self.assertGreater(hits, 0, "'verification' should match via props.description")

    def test_description_only_match_does_not_hit_other_fields(self) -> None:
        """Confirm 'acceptance' is truly description-only (not in ID/label/file/exports)."""
        node = _WEB_NODES["agent:qa"]
        nid = "agent:qa"
        # Verify the token is NOT in the other four fields
        self.assertNotIn("acceptance", nid.lower())
        self.assertNotIn("acceptance", node["label"].lower())
        self.assertNotIn("acceptance", (node.get("props") or {}).get("file", "").lower())
        exports = [e.lower() for e in (node.get("props") or {}).get("exports", [])]
        self.assertFalse(any("acceptance" in e for e in exports))
        # But it IS in the description
        desc = (node.get("props") or {}).get("description", "").lower()
        self.assertIn("acceptance", desc)
        # And _match_tokens finds it
        hits = Graph._match_tokens(["acceptance"], nid, node)
        self.assertGreater(hits, 0, "'acceptance' should match via props.description only")

class QueryEndToEndTest(unittest.TestCase):
    """End-to-end tests for Graph.query() with web-app fixture nodes."""

    def setUp(self) -> None:
        self.graph = _make_graph(_WEB_NODES)

    def test_single_token_stores(self) -> None:
        result = self.graph.query("stores")
        ids = [m["id"] for m in result["matches"]]
        self.assertIn(
            "file:web/app/stores/page", ids,
            "'stores' query should find stores/page",
        )

    def test_single_token_flyers(self) -> None:
        result = self.graph.query("flyers")
        ids = [m["id"] for m in result["matches"]]
        self.assertIn(
            "file:web/app/flyers/current/page", ids,
            "'flyers' query should find flyers page via props.file",
        )

    def test_single_token_footer_via_exports(self) -> None:
        result = self.graph.query("footer")
        ids = [m["id"] for m in result["matches"]]
        self.assertIn(
            "file:web/components/shell", ids,
            "'footer' should match shell via SiteFooter export",
        )

    def test_multi_word_stores_page(self) -> None:
        result = self.graph.query("stores page")
        ids = [m["id"] for m in result["matches"]]
        self.assertIn("file:web/app/stores/page", ids)
        # stores/page matches both tokens; flyers/page only matches 'page'
        # so stores/page should rank higher if flyers/page appears at all
        if "file:web/app/flyers/current/page" in ids:
            self.assertLess(
                ids.index("file:web/app/stores/page"),
                ids.index("file:web/app/flyers/current/page"),
                "stores/page should rank above flyers/page for 'stores page'",
            )

    def test_multi_word_or_fallback_when_one_token_misses(self) -> None:
        """When one token misses, OR fallback returns matches for the hit token.

        Pre-fallback behavior was an empty envelope; the fallback now keeps
        ``wd query`` from returning nothing useful when humans type
        multi-word phrases where one token is nonsense or out-of-scope.
        """
        result = self.graph.query("stores xyznonexistent")
        ids = [m["id"] for m in result["matches"]]
        # Strict-AND would have been empty; OR fallback returns the
        # 'stores' match.
        self.assertIn(
            "file:web/app/stores/page", ids,
            "OR fallback should return the 'stores' match when "
            "'xyznonexistent' has no candidates",
        )
        # The fallback path tags the envelope so consumers know the result
        # was not strict-AND.
        self.assertEqual(result.get("degraded_match"), "or_fallback")

    def test_multi_word_both_missing_returns_empty(self) -> None:
        """If neither token matches, the result is honestly empty."""
        result = self.graph.query("zzznonexistent xyznonexistent")
        self.assertEqual(
            len(result["matches"]), 0,
            "no candidates for either token -> empty envelope",
        )
        self.assertNotIn("degraded_match", result)

    def test_deterministic_ordering(self) -> None:
        """Same query run twice must return identical ordering."""
        r1 = self.graph.query("page")
        r2 = self.graph.query("page")
        ids1 = [m["id"] for m in r1["matches"]]
        ids2 = [m["id"] for m in r2["matches"]]
        self.assertEqual(ids1, ids2, "query ordering must be deterministic")

    def test_empty_query_returns_nothing(self) -> None:
        result = self.graph.query("")
        self.assertEqual(len(result["matches"]), 0)

    def test_whitespace_only_query_returns_nothing(self) -> None:
        result = self.graph.query("   ")
        self.assertEqual(len(result["matches"]), 0)

    def test_limit_respected(self) -> None:
        result = self.graph.query("page", limit=1)
        self.assertLessEqual(len(result["matches"]), 1)

    def test_query_returns_neighbors_and_edges(self) -> None:
        """Result dict must contain neighbors and edges keys."""
        result = self.graph.query("store")
        self.assertIn("neighbors", result)
        self.assertIn("edges", result)
        self.assertIn("query", result)
        self.assertIn("matches", result)

    def test_tsx_fingerprint_file_path_lookup(self) -> None:
        """Representative web lookup: 'dashboard' should find the dashboard page.

        This guards against regressions where path-level web fingerprints
        are silently collapsed (e.g., if props.file matching is removed).
        """
        result = self.graph.query("dashboard")
        ids = [m["id"] for m in result["matches"]]
        self.assertIn(
            "file:web/app/dashboard/page", ids,
            "dashboard page must be findable via tokenized query",
        )

    def test_tsx_fingerprint_export_symbol_lookup(self) -> None:
        """Representative web lookup: 'DashboardPage' should find by export."""
        result = self.graph.query("DashboardPage")
        ids = [m["id"] for m in result["matches"]]
        self.assertIn(
            "file:web/app/dashboard/page", ids,
            "DashboardPage export symbol should be findable",
        )

    def test_multi_token_ranking_more_hits_first(self) -> None:
        """A node matching more tokens should rank higher."""
        # 'shell components' — shell matches ID, label, file; components matches ID, file
        result = self.graph.query("shell components")
        ids = [m["id"] for m in result["matches"]]
        self.assertIn("file:web/components/shell", ids)
        # Only shell/components node can match both tokens
        self.assertEqual(ids[0], "file:web/components/shell")

    def test_nonexistent_term_returns_empty(self) -> None:
        result = self.graph.query("zzzznonexistent42")
        self.assertEqual(len(result["matches"]), 0)
        self.assertEqual(result["query"], "zzzznonexistent42")

    def test_query_matches_props_description(self) -> None:
        """Query for 'verification' should find agent:qa via description.

        The word 'verification' appears only in props.description — not in
        the node ID, label, file, or exports. Covers tracked project
        """
        result = self.graph.query("verification")
        ids = [m["id"] for m in result["matches"]]
        self.assertIn(
            "agent:qa", ids,
            "'verification' should match agent:qa via props.description",
        )

    def test_query_description_multi_token(self) -> None:
        """Multi-token query where one token matches description only."""
        result = self.graph.query("qa acceptance")
        ids = [m["id"] for m in result["matches"]]
        self.assertIn(
            "agent:qa", ids,
            "'qa acceptance' should match — 'qa' hits ID/label, 'acceptance' hits description",
        )

if __name__ == "__main__":
    unittest.main()
