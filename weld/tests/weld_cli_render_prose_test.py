"""Coverage for the description/summary fallback (bd ph1g follow-up).

ADR 0114 gave ~100% of Python file nodes a ``props.summary`` -- the module's
own opening docstring line -- but nothing rendered it: ``weld._cli_render``
only ever printed ``props.description`` (LLM enrichment, ~2% coverage at the
time). :mod:`weld._cli_render_prose` is the fix: one ``prose_line`` helper
that renders ``description`` when present and falls back to ``summary``
otherwise, shared by :func:`weld._cli_render.render_context` and the
match-block helper behind ``render_query`` / ``render_callers`` /
``render_references``.

Split out of :mod:`weld_cli_render_helpers_test` (which sat at the 400-line
cap) rather than folded in, so this module tests the shared helper directly
as well as through the two renderers named in the originating issue.
"""

from __future__ import annotations

import unittest

from weld._cli_render import render_context, render_query
from weld._cli_render_prose import prose_line


class ProseLineUnitTest(unittest.TestCase):
    """Direct coverage of the shared precedence rule."""

    def test_description_only(self) -> None:
        self.assertEqual(
            prose_line({"description": "Validates a token."}, 200),
            "description: Validates a token.",
        )

    def test_summary_only(self) -> None:
        self.assertEqual(
            prose_line({"summary": "Canonical serializer for graph.json."}, 200),
            "summary: Canonical serializer for graph.json.",
        )

    def test_description_wins_when_both_present(self) -> None:
        line = prose_line(
            {"description": "Reviewed.", "summary": "Raw docstring."}, 200,
        )
        self.assertEqual(line, "description: Reviewed.")

    def test_neither_present_returns_none(self) -> None:
        self.assertIsNone(prose_line({}, 200))

    def test_blank_strings_are_treated_as_absent(self) -> None:
        # Both fields are always-present keys on a Python file node (ADR
        # 0114): "" means no docstring, not "render an empty line".
        self.assertIsNone(prose_line({"description": "  ", "summary": ""}, 200))

    def test_blank_description_falls_back_to_summary(self) -> None:
        line = prose_line({"description": "", "summary": "Has content."}, 200)
        self.assertEqual(line, "summary: Has content.")

    def test_limit_truncates_with_ellipsis(self) -> None:
        line = prose_line({"summary": "x" * 50}, 10)
        self.assertEqual(line, "summary: " + "x" * 7 + "...")

    def test_whitespace_is_collapsed(self) -> None:
        line = prose_line({"summary": "a\n  b\tc"}, 200)
        self.assertEqual(line, "summary: a b c")


class RenderQuerySummaryFallbackTest(unittest.TestCase):
    """The originating surface: `wd query`'s match block."""

    def test_summary_renders_when_no_description(self) -> None:
        text = render_query({
            "query": "alpha",
            "matches": [{
                "id": "file:weld/foo.py",
                "type": "file",
                "props": {"summary": "Canonical serializer for graph.json."},
            }],
            "neighbors": [],
            "edges": [],
        })
        self.assertIn("summary: Canonical serializer for graph.json.", text)

    def test_description_wins_over_summary(self) -> None:
        text = render_query({
            "query": "alpha",
            "matches": [{
                "id": "file:weld/foo.py",
                "type": "file",
                "props": {
                    "description": "Reviewed summary of foo.",
                    "summary": "Raw docstring line.",
                },
            }],
            "neighbors": [],
            "edges": [],
        })
        self.assertIn("description: Reviewed summary of foo.", text)
        self.assertNotIn("Raw docstring line.", text)
        self.assertNotIn("summary:", text)

    def test_neither_field_renders_no_prose_line(self) -> None:
        text = render_query({
            "query": "alpha",
            "matches": [{"id": "file:weld/foo.py", "type": "file", "props": {}}],
            "neighbors": [],
            "edges": [],
        })
        self.assertNotIn("description:", text)
        self.assertNotIn("summary:", text)


class RenderContextSummaryFallbackTest(unittest.TestCase):
    """The other originating surface: `wd context`'s node header."""

    def test_summary_renders_when_no_description(self) -> None:
        text = render_context({
            "node": {
                "id": "file:weld/foo.py",
                "type": "file",
                "props": {"summary": "Canonical serializer for graph.json."},
            },
            "neighbors": [],
            "edges": [],
        })
        self.assertIn("summary: Canonical serializer for graph.json.", text)

    def test_description_wins_over_summary(self) -> None:
        text = render_context({
            "node": {
                "id": "file:weld/foo.py",
                "type": "file",
                "props": {
                    "description": "Reviewed summary of foo.",
                    "summary": "Raw docstring line.",
                },
            },
            "neighbors": [],
            "edges": [],
        })
        self.assertIn("description: Reviewed summary of foo.", text)
        self.assertNotIn("Raw docstring line.", text)
        self.assertNotIn("summary:", text)


if __name__ == "__main__":
    unittest.main()
