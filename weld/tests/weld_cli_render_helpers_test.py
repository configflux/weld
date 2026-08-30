"""Pure-function unit tests for ``weld._cli_render``.

ADR 0040 introduces a small renderer module shared across the
retrieval CLI surface. These tests pin the rendered shape for each
helper without touching the CLI dispatcher or argparse, so a logic
regression in a renderer fails here even before the end-to-end CLI
tests run.
"""

from __future__ import annotations

import unittest


from weld._cli_render import (  # noqa: E402
    render_context,
    render_find,
    render_path,
    render_query,
    render_stale,
    render_stats,
)


class RendererPurityTest(unittest.TestCase):
    """Each renderer is a pure function: takes a payload, returns text."""

    def test_render_query_includes_match_and_type_tag(self) -> None:
        text = render_query({
            "query": "alpha",
            "matches": [{"id": "entity:Foo", "type": "entity", "label": "Foo"}],
            "neighbors": [],
            "edges": [],
        })
        self.assertIn("# query: alpha", text)
        self.assertIn("entity:Foo", text)
        self.assertIn("[type: entity]", text)

    def test_render_query_shows_confidence_per_match(self) -> None:
        """Each match block surfaces ``props.confidence`` so agents can discount."""
        text = render_query({
            "query": "alpha",
            "matches": [{
                "id": "symbol:py:weld.foo:bar",
                "type": "function",
                "label": "bar",
                "props": {"confidence": "speculative"},
            }],
            "neighbors": [],
            "edges": [],
        })
        self.assertIn("confidence: speculative", text)

    def test_render_query_omits_confidence_when_absent(self) -> None:
        """No confidence line when the match has no ``props.confidence``."""
        text = render_query({
            "query": "alpha",
            "matches": [{"id": "entity:Foo", "type": "entity", "props": {}}],
            "neighbors": [],
            "edges": [],
        })
        self.assertNotIn("confidence:", text)

    def test_render_query_marks_or_fallback(self) -> None:
        text = render_query({
            "query": "a b",
            "matches": [{"id": "entity:Foo", "type": "entity"}],
            "neighbors": [],
            "edges": [],
            "degraded_match": "or_fallback",
        })
        self.assertIn("degraded match: or_fallback", text)

    def test_render_query_empty_says_no_matches(self) -> None:
        text = render_query({
            "query": "x",
            "matches": [],
            "neighbors": [],
            "edges": [],
        })
        self.assertIn("no matches", text)

    def test_render_query_federated_uses_display_id_separator(self) -> None:
        """Regression: federated matches must render with a visible separator.

        Federation prefixes child-local IDs with a ``\\x1f`` UNIT_SEPARATOR
        for the canonical ``id`` field and exposes the human-readable form
        via ``display_id`` (``child::id``). The text renderer must prefer
        ``display_id`` so the user does not see the invisible control
        character glue (which renders as e.g. ``sharexsymbol:csharp:..``).
        """
        canonical = "sharex\x1fsymbol:csharp:ShareX.Foo"
        display = "sharex::symbol:csharp:ShareX.Foo"
        text = render_query({
            "query": "ShareX",
            "matches": [{
                "id": canonical,
                "display_id": display,
                "type": "symbol",
            }],
            "neighbors": [{
                "id": canonical,
                "display_id": display,
                "type": "symbol",
            }],
            "edges": [],
        })
        self.assertIn(display, text)
        self.assertNotIn(canonical, text)
        # The invisible control char must not appear in user-facing output.
        self.assertNotIn("\x1f", text)

    def test_render_query_non_federated_unchanged(self) -> None:
        """Single-repo payloads have no display_id; render must use id."""
        text = render_query({
            "query": "alpha",
            "matches": [{"id": "entity:Foo", "type": "entity"}],
            "neighbors": [{"id": "entity:Bar", "type": "entity"}],
            "edges": [],
        })
        self.assertIn("entity:Foo", text)
        self.assertIn("entity:Bar", text)

    def test_render_find_is_tabular(self) -> None:
        text = render_find({
            "query": "install",
            "files": [
                {"path": "install.sh", "score": 13, "tokens": ["install", "sh"]},
                {"path": "README.md", "score": 4, "tokens": ["install"]},
            ],
        })
        self.assertIn("path", text)
        self.assertIn("score", text)
        self.assertIn("install.sh", text)
        self.assertIn("13", text)

    def test_render_find_empty_says_no_matches(self) -> None:
        text = render_find({"query": "nothing", "files": []})
        self.assertIn("no matches", text)

    def test_render_context_groups_by_edge_type(self) -> None:
        text = render_context({
            "node": {"id": "entity:Store", "type": "entity", "label": "Store"},
            "neighbors": [
                {"id": "entity:Cart", "type": "entity", "label": "Cart"},
            ],
            "edges": [
                {
                    "from": "entity:Store", "to": "entity:Cart",
                    "type": "depends_on", "props": {},
                },
            ],
        })
        self.assertIn("# context: entity:Store", text)
        self.assertIn("depends_on", text)
        self.assertIn("entity:Cart", text)

    def test_render_context_handles_resolved_from(self) -> None:
        text = render_context({
            "node": {"id": "entity:Store", "type": "entity"},
            "neighbors": [],
            "edges": [],
            "resolved_from": {
                "query": "store",
                "matched_id": "entity:Store",
                "score": 1,
            },
        })
        self.assertIn("resolved-from", text)
        self.assertIn("entity:Store", text)

    def test_render_context_error(self) -> None:
        text = render_context({"error": "node not found: foo"})
        self.assertIn("error", text)
        self.assertIn("foo", text)

    def test_render_path_chain(self) -> None:
        text = render_path({
            "path": [
                {"id": "a:1", "label": "1"},
                {"id": "b:2", "label": "2"},
                {"id": "c:3", "label": "3"},
            ],
            "edges": [
                {"from": "a:1", "to": "b:2", "type": "calls"},
                {"from": "b:2", "to": "c:3", "type": "depends_on"},
            ],
        })
        self.assertIn("a:1 -> b:2 -> c:3", text)

    def test_render_path_no_path(self) -> None:
        text = render_path({"path": None, "reason": "no path found"})
        self.assertIn("no path found", text)

    def test_render_stats_lists_counts_and_top_authority(self) -> None:
        text = render_stats({
            "total_nodes": 10,
            "total_edges": 4,
            "nodes_by_type": {"entity": 7, "symbol": 3},
            "edges_by_type": {"calls": 4},
            "top_authority_nodes": [
                {"id": "entity:Store", "type": "entity", "degree": 5},
            ],
            "top": 5,
            "stale": {"stale": False},
        })
        self.assertIn("total_nodes: 10", text)
        self.assertIn("entity:Store", text)
        self.assertIn("nodes_by_type:", text)

    def test_render_stale_uses_yes_no_form(self) -> None:
        text = render_stale({
            "stale": True,
            "source_stale": True,
            "sha_behind": False,
            "graph_sha": "abc",
            "current_sha": "def",
            "commits_behind": 1,
        })
        self.assertIn("stale: yes", text)
        self.assertIn("source_stale: yes", text)
        self.assertIn("sha_behind: no", text)

    def test_render_stale_renders_reason(self) -> None:
        text = render_stale({
            "stale": False, "source_stale": False, "sha_behind": False,
            "graph_sha": None, "current_sha": None, "commits_behind": 0,
            "reason": "not a git repo",
        })
        self.assertIn("reason: not a git repo", text)

    def test_render_stale_lists_stale_sources(self) -> None:
        # Names the diverging path(s) and why.
        text = render_stale({
            "stale": True, "source_stale": True, "sha_behind": False,
            "graph_sha": "abc", "current_sha": "def", "commits_behind": 0,
            "stale_sources": [
                {"path": "src/a.py", "reason": "content differs"},
            ],
            "stale_sources_omitted": 0,
        })
        self.assertIn("stale_sources (1):", text)
        self.assertIn("src/a.py: content differs", text)
        self.assertNotIn("elided", text)

    def test_render_stale_reports_omitted_count(self) -> None:
        text = render_stale({
            "stale": True, "source_stale": True, "sha_behind": False,
            "graph_sha": "abc", "current_sha": "def", "commits_behind": 0,
            "stale_sources": [
                {"path": "src/a.py", "reason": "content differs"},
            ],
            "stale_sources_omitted": 12,
        })
        self.assertIn("12 more elided (capped)", text)

    def test_render_stale_omits_the_block_when_nothing_diverged(self) -> None:
        text = render_stale({
            "stale": False, "source_stale": False, "sha_behind": False,
            "graph_sha": "abc", "current_sha": "abc", "commits_behind": 0,
            "stale_sources": [], "stale_sources_omitted": 0,
        })
        self.assertNotIn("stale_sources", text)

    def test_stale_sources_path_survives_render_then_escapes_at_boundary(
        self,
    ) -> None:
        # weld._safe_text: render_stale is a pure formatter (no escaping);
        # the write boundary (_emit -> sanitize_terminal_text) is where a
        # hostile repo-controlled path gets escaped. Same contract every
        # other renderer in this module already relies on -- no new write
        # site is introduced by this block.
        from weld._safe_text import sanitize_terminal_text

        hostile = "src/\x1b[2Jevil.py"
        text = render_stale({
            "stale": True, "source_stale": True, "sha_behind": False,
            "graph_sha": "abc", "current_sha": "def", "commits_behind": 0,
            "stale_sources": [{"path": hostile, "reason": "content differs"}],
            "stale_sources_omitted": 0,
        })
        self.assertIn(hostile, text)
        safe = sanitize_terminal_text(text)
        self.assertNotIn("\x1b", safe)
        self.assertIn("\\x1b[2J", safe)

    def test_render_stale_children_all_missing_are_not_bare_zero_stale(
        self,
    ) -> None:
        # bd 51oxx (field-eval finding 02): at a federation root where zero
        # children are checked out on disk, every registered child is
        # ``missing``. The old "children: 4 (0 stale)" reads as "all healthy";
        # the fix must make registered-but-absent distinguishable from
        # present-and-fresh (ADR 0134, one level up in the freshness surface).
        text = render_stale({
            "stale": True, "source_stale": True, "sha_behind": False,
            "graph_sha": None, "current_sha": "def", "commits_behind": -1,
            "reason": "no graph",
            "children": [
                {"name": "a", "state": "missing", "reason": "missing",
                 "commits_behind": 0},
                {"name": "b", "state": "missing", "reason": "missing",
                 "commits_behind": 0},
                {"name": "c", "state": "missing", "reason": "missing",
                 "commits_behind": 0},
                {"name": "d", "state": "missing", "reason": "missing",
                 "commits_behind": 0},
            ],
        })
        # The summary must state how many are actually present, and surface the
        # missing count -- not present a clean "0 stale" that masks absence.
        self.assertIn("4 registered", text)
        self.assertIn("0 present", text)
        self.assertIn("missing=4", text)
        # It must NOT collapse to the old bare "(0 stale)" form.
        self.assertNotIn("children: 4 (0 stale)", text)

    def test_render_stale_children_mixed_lifecycle_breakdown(self) -> None:
        text = render_stale({
            "stale": True, "source_stale": False, "sha_behind": False,
            "graph_sha": "abc", "current_sha": "abc", "commits_behind": 0,
            "children": [
                {"name": "a", "state": "present", "reason": "fresh",
                 "commits_behind": 0},
                {"name": "b", "state": "stale", "reason": "source_changed",
                 "commits_behind": 3},
                {"name": "c", "state": "missing", "reason": "missing",
                 "commits_behind": 0},
                {"name": "d", "state": "uninitialized",
                 "reason": "uninitialized", "commits_behind": 0},
            ],
        })
        self.assertIn("4 registered", text)
        # A stale child counts as present-on-disk but drifted.
        self.assertIn("2 present", text)
        self.assertIn("1 stale", text)
        self.assertIn("missing=1", text)
        self.assertIn("uninitialized=1", text)
        # Stale children are still enumerated line-by-line (regression).
        self.assertIn("b: stale (source_changed, 3 behind)", text)

    def test_render_stale_children_all_present_fresh(self) -> None:
        text = render_stale({
            "stale": False, "source_stale": False, "sha_behind": False,
            "graph_sha": "abc", "current_sha": "abc", "commits_behind": 0,
            "children": [
                {"name": "a", "state": "present", "reason": "fresh",
                 "commits_behind": 0},
                {"name": "b", "state": "present", "reason": "fresh",
                 "commits_behind": 0},
            ],
        })
        self.assertIn("2 registered", text)
        self.assertIn("2 present", text)
        self.assertIn("0 stale", text)
        # No absent-state breakdown when every child is present.
        self.assertNotIn("missing=", text)

    def test_render_stale_non_federated_has_no_children_line(self) -> None:
        text = render_stale({
            "stale": False, "source_stale": False, "sha_behind": False,
            "graph_sha": "abc", "current_sha": "abc", "commits_behind": 0,
        })
        self.assertNotIn("children", text)


if __name__ == "__main__":
    unittest.main()
