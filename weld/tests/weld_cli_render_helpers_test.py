"""Pure-function unit tests for ``weld._cli_render``.

ADR 0040 introduces a small renderer module shared across the
retrieval CLI surface. These tests pin the rendered shape for each
helper without touching the CLI dispatcher or argparse, so a logic
regression in a renderer fails here even before the end-to-end CLI
tests run.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld._cli_render import (  # noqa: E402
    render_callers,
    render_context,
    render_find,
    render_path,
    render_query,
    render_references,
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

    def test_render_callers_includes_symbol_header(self) -> None:
        text = render_callers({
            "symbol": "_load_strategy",
            "depth": 2,
            "callers": [
                {"id": "symbol:py:m:fn", "type": "symbol", "label": "m.fn"},
            ],
            "edges": [],
        })
        self.assertIn("# callers: _load_strategy", text)
        self.assertIn("depth 2", text)

    def test_render_callers_no_callers(self) -> None:
        text = render_callers({
            "symbol": "x",
            "depth": 1,
            "callers": [],
            "edges": [],
        })
        self.assertIn("no callers", text)

    def test_render_callers_error(self) -> None:
        text = render_callers({
            "symbol": "x", "depth": 1, "callers": [], "edges": [],
            "error": "node not found: x",
        })
        self.assertIn("error: node not found: x", text)

    def test_render_references_groups_graph_and_textual(self) -> None:
        text = render_references({
            "symbol": "checkout",
            "matches": [
                {"id": "symbol:py:m:checkout", "type": "symbol"},
            ],
            "callers": [],
            "files": [{"path": "shop.py", "score": 3, "tokens": ["checkout"]}],
        })
        self.assertIn("graph matches", text)
        self.assertIn("textual hits", text)
        self.assertIn("shop.py", text)

    def test_render_references_empty(self) -> None:
        text = render_references({
            "symbol": "x", "matches": [], "callers": [], "files": [],
        })
        self.assertIn("no references", text)

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


if __name__ == "__main__":
    unittest.main()
