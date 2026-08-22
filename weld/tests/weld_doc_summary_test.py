"""Unit tests for the shared paragraph/collapse/bound reduction (bd 5038-009x).

ADR 0114/0118's reduction (formerly the private
``weld.strategies._python_anchor._summary_from_docstring``) moved to
:mod:`weld.strategies._doc_summary` as :func:`collapse_summary` so a
non-Python doc-comment writer (:mod:`weld.strategies._ts_doc_comments`) could
reuse the exact contract rather than duplicate it. The Python-specific
callers (``module_summary`` / ``symbol_summary``) already have exhaustive
coverage of this reduction via :mod:`weld.tests.weld_module_summary_test` and
:mod:`weld.tests.weld_symbol_summary_test`, not re-derived here; these tests
pin :func:`collapse_summary` directly at its new home so the move itself, and
every caller of it, is covered by a test that imports nothing Python-specific.
"""

from __future__ import annotations

import unittest

from weld.strategies._doc_summary import MAX_SUMMARY_LEN, collapse_summary


class CollapseSummaryTest(unittest.TestCase):
    def test_empty_input_is_empty_output(self) -> None:
        self.assertEqual(collapse_summary(""), "")

    def test_single_paragraph_passes_through(self) -> None:
        self.assertEqual(
            collapse_summary("Axis-aligned rectangle."), "Axis-aligned rectangle.",
        )

    def test_only_the_opening_paragraph_is_kept(self) -> None:
        self.assertEqual(
            collapse_summary("First paragraph.\n\nSecond paragraph."),
            "First paragraph.",
        )

    def test_internal_whitespace_and_newlines_collapse_to_one_line(self) -> None:
        self.assertEqual(
            collapse_summary("Line one\nLine two\n   Line three"),
            "Line one Line two Line three",
        )

    def test_a_whitespace_only_separator_line_still_counts_as_a_paragraph_break(
        self,
    ) -> None:
        # A joined doc-comment run may carry a separator that is a comment
        # marker with nothing else on the line (e.g. a bare Go "//" or Rust
        # "///"), which reduces to whitespace-only text between two real
        # lines -- must still split, matching a truly blank docstring line.
        self.assertEqual(
            collapse_summary("First.\n   \nSecond."), "First.",
        )

    def test_bound_truncates_on_a_word_boundary(self) -> None:
        long_text = " ".join(["word"] * 200)
        result = collapse_summary(long_text)
        self.assertLessEqual(len(result), MAX_SUMMARY_LEN)
        self.assertFalse(result.endswith("wor"))
        self.assertTrue(result.startswith("word word"))

    def test_bound_with_no_word_boundary_hard_truncates(self) -> None:
        no_spaces = "x" * (MAX_SUMMARY_LEN + 50)
        result = collapse_summary(no_spaces)
        self.assertEqual(len(result), MAX_SUMMARY_LEN)

    def test_exactly_at_the_bound_is_unchanged(self) -> None:
        exact = "y" * MAX_SUMMARY_LEN
        self.assertEqual(collapse_summary(exact), exact)


if __name__ == "__main__":
    unittest.main()
