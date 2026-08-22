"""Pure-function unit tests for ``render_callers`` / ``render_references``.

Split out of weld_cli_render_helpers_test.py, which sat at the 400-line cap
(AGENTS.md / CLAUDE.md line-count policy) -- same reason and same pattern as
the ``_cli_render_seeds`` / ``_cli_render_prose`` / ``_cli_render_trust`` /
``_cli_render_freshness`` splits on the source side. ``render_callers`` and
``render_references`` are the two traversal-read renderers (the ``callers`` /
``references`` half of :mod:`weld.read_traversal`'s "impact / callers /
references / trace" grouping) and the two that share ``_match_block``, so
they are the cohesive unit to carve out rather than an arbitrary line split.

ADR 0040 introduces a small renderer module shared across the retrieval CLI
surface. These tests pin the rendered shape for each helper without touching
the CLI dispatcher or argparse, so a logic regression in a renderer fails
here even before the end-to-end CLI tests run.
"""

from __future__ import annotations

import unittest


from weld._cli_render import render_callers, render_references  # noqa: E402


class CallersReferencesRendererTest(unittest.TestCase):
    """Each renderer is a pure function: takes a payload, returns text."""

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

    def test_render_callers_single_seed_shows_no_seeds_line(self) -> None:
        """bd jz65r: a single resolved seed repeats the ``symbol:`` header
        and would be noise on the overwhelmingly common exact-id/unique-name
        lookup -- rendered only when there is something to disambiguate."""
        text = render_callers({
            "symbol": "caller_one", "depth": 1,
            "seeds": ["symbol:py:m:caller_one"],
            "callers": [
                {"id": "symbol:py:m:top", "type": "symbol",
                 "targets": ["symbol:py:m:caller_one"]},
            ],
            "edges": [],
        })
        self.assertNotIn("seeds", text)

    def test_render_callers_annotates_plural_seeds_and_targets(self) -> None:
        """bd jz65r: the ``callers()`` half of the honesty gap bd nyoks
        fixed for ``references()``. A bare name resolving to more than one
        seed shows the ``seeds`` line, and each caller line names the
        seed(s) it was actually found calling -- the latter needs no new
        renderer code, since ``_match_block`` already renders a ``targets``
        key generically (bd nyoks's original addition)."""
        text = render_callers({
            "symbol": "helper", "depth": 1,
            "seeds": ["symbol:py:m:helper", "symbol:unresolved:helper"],
            "callers": [
                {"id": "symbol:py:m:caller_one", "type": "symbol",
                 "targets": ["symbol:py:m:helper"]},
                {"id": "symbol:py:m:top", "type": "symbol",
                 "targets": ["symbol:unresolved:helper"]},
            ],
            "edges": [],
        })
        self.assertIn(
            "seeds (2): symbol:py:m:helper, symbol:unresolved:helper", text,
        )
        self.assertIn("targets: symbol:py:m:helper", text)
        self.assertIn("targets: symbol:unresolved:helper", text)

    def test_render_callers_depth_two_has_no_seeds_line_regression(
        self,
    ) -> None:
        """A payload without ``seeds`` at all (the shape before bd jz65r, or
        any depth's response before this field existed) must render exactly
        as before -- ``.get`` defaulting to ``[]`` is silent, not an error."""
        text = render_callers({
            "symbol": "_load_strategy", "depth": 2,
            "callers": [
                {"id": "symbol:py:m:fn", "type": "symbol", "label": "m.fn"},
            ],
            "edges": [],
        })
        self.assertNotIn("seeds", text)
        self.assertNotIn("targets", text)

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

    def test_render_references_error(self) -> None:
        """An unknown name reads as an error, not as "nothing uses this".

        The envelope has carried ``error`` since bd nywd; this renderer
        read neither key and printed ``no references`` for both cases, so
        the default human output still gave a name weld had never heard of
        the answer that means "weld knows this and nothing points at it"
        (bd ily7).
        """
        text = render_references({
            "symbol": "x", "matches": [], "callers": [], "edges": [],
            "files": [], "error": "node not found: x",
        })
        self.assertIn("error: node not found: x", text)
        self.assertNotIn("no references", text)

    def test_render_references_known_node_with_no_referrers(self) -> None:
        """The other half of the same distinction stays readable.

        A node weld *does* know, that nothing points at, must not borrow
        the error spelling -- it renders its match and stops.
        """
        text = render_references({
            "symbol": "build-target://a:b",
            "matches": [{"id": "build-target://a:b", "type": "build-target"}],
            "callers": [], "edges": [], "files": [],
        })
        self.assertIn("graph matches", text)
        self.assertNotIn("error", text)

    def test_render_references_annotates_caller_with_its_match(self) -> None:
        """bd nyoks: a caller line names which same-named match it belongs
        to, so two ambiguous matches with different callers stay readable
        without cross-referencing ``edges`` by hand."""
        text = render_references({
            "symbol": "Tool",
            "matches": [
                {"id": "symbol:py:mcp.types:Tool", "type": "symbol"},
                {"id": "symbol:py:weld.mcp_server:Tool", "type": "symbol"},
            ],
            "callers": [
                {"id": "symbol:py:weld._mcp_stdio:run_stdio", "type": "symbol",
                 "targets": ["symbol:py:mcp.types:Tool"]},
                {"id": "symbol:py:weld.mcp_server:build_tools", "type": "symbol",
                 "targets": ["symbol:py:weld.mcp_server:Tool"]},
            ],
            "files": [],
        })
        self.assertIn("targets: symbol:py:mcp.types:Tool", text)
        self.assertIn("targets: symbol:py:weld.mcp_server:Tool", text)


if __name__ == "__main__":
    unittest.main()
