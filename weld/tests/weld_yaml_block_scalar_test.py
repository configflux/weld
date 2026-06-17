"""Unit tests for literal/folded block-scalar expansion in ``weld._yaml``.

Covers the block-scalar bug fix (bd kooo): the bundled minimal YAML parser
previously collapsed a ``run: |`` / ``path: |`` block scalar to the bare
indicator string ``'|'`` instead of expanding the body.

The matrix here exercises literal (``|``) and folded (``>``) styles with every
chomping indicator (clip / strip ``-`` / keep ``+``), at all three parse sites
the parser supports (top-level mapping value, the first ``key: value`` of a
sequence item, and a continuation ``key: value`` inside a sequence item), plus
varied indentation, interior blank lines, and a non-regression guard that plain
scalars and flow collections are unaffected.
"""

from __future__ import annotations

import unittest

from weld._yaml import parse_yaml
from weld._yaml_block_scalar import is_block_scalar_header


class IssueReproTest(unittest.TestCase):
    """The exact repro from the bug report must round-trip correctly."""

    def test_literal_run_in_sequence_item(self) -> None:
        text = "steps:\n  - run: |\n      echo a\n      echo b\n"
        self.assertEqual(
            parse_yaml(text),
            {"steps": [{"run": "echo a\necho b\n"}]},
        )

    def test_bug_no_longer_returns_bare_indicator(self) -> None:
        # Regression sentinel: the pre-fix behaviour returned '|'.
        got = parse_yaml("steps:\n  - run: |\n      echo a\n")["steps"][0]["run"]
        self.assertNotEqual(got, "|")
        self.assertEqual(got, "echo a\n")


class LiteralStyleTest(unittest.TestCase):
    """``|`` preserves interior newlines and relative indentation."""

    def test_clip_one_trailing_newline(self) -> None:
        self.assertEqual(parse_yaml("x: |\n  a\n  b\n"), {"x": "a\nb\n"})

    def test_strip_removes_trailing_newline(self) -> None:
        self.assertEqual(parse_yaml("x: |-\n  a\n  b\n"), {"x": "a\nb"})

    def test_keep_retains_trailing_blanks(self) -> None:
        self.assertEqual(parse_yaml("x: |+\n  a\n\n\n"), {"x": "a\n\n\n"})

    def test_relative_indentation_preserved(self) -> None:
        text = "x: |\n  line1\n    indented\n  line3\n"
        self.assertEqual(parse_yaml(text), {"x": "line1\n  indented\nline3\n"})

    def test_interior_blank_line_preserved(self) -> None:
        text = "x: |\n  a\n\n  b\n"
        self.assertEqual(parse_yaml(text), {"x": "a\n\nb\n"})

    def test_deep_indentation_detected_from_first_line(self) -> None:
        # Body indented 6 under a 0-indent key; auto-detection keys off the
        # first non-blank line, so the common 6 spaces are stripped.
        text = "x: |\n      a\n      b\n"
        self.assertEqual(parse_yaml(text), {"x": "a\nb\n"})


class FoldedStyleTest(unittest.TestCase):
    """``>`` folds consecutive non-empty lines and keeps blank lines."""

    def test_clip_folds_with_single_space(self) -> None:
        self.assertEqual(parse_yaml("x: >\n  a\n  b\n"), {"x": "a b\n"})

    def test_strip_folds_without_trailing_newline(self) -> None:
        self.assertEqual(parse_yaml("x: >-\n  a\n  b\n"), {"x": "a b"})

    def test_keep_retains_trailing_blanks(self) -> None:
        self.assertEqual(parse_yaml("x: >+\n  a\n  b\n\n\n"), {"x": "a b\n\n\n"})

    def test_blank_line_becomes_newline(self) -> None:
        text = "x: >\n  a\n  b\n\n  c\n"
        self.assertEqual(parse_yaml(text), {"x": "a b\nc\n"})

    def test_single_line_folded(self) -> None:
        self.assertEqual(parse_yaml("x: >\n  only\n"), {"x": "only\n"})


class EmptyBodyTest(unittest.TestCase):
    """An empty block scalar collapses to the empty string under clip/strip.

    Matches ``yaml.safe_load``: clip's "single trailing newline" applies only
    when the block actually has content. These guard the bug found in review
    where an empty body produced ``'\\n'`` instead of ``''``.
    """

    def test_literal_no_body_then_key(self) -> None:
        self.assertEqual(parse_yaml("x: |\ny: 1\n"), {"x": "", "y": 1})

    def test_folded_only_blank_lines(self) -> None:
        self.assertEqual(parse_yaml("x: >\n\n\ny: 1\n"), {"x": "", "y": 1})

    def test_nested_empty_block_then_dedent(self) -> None:
        self.assertEqual(parse_yaml("a:\n  x: |\nb: 1\n"), {"a": {"x": ""}, "b": 1})


class ParseSiteTest(unittest.TestCase):
    """Block scalars expand at every site the parser recognises."""

    def test_top_level_mapping_value(self) -> None:
        self.assertEqual(parse_yaml("desc: |\n  one\n  two\n"), {"desc": "one\ntwo\n"})

    def test_sequence_item_first_value(self) -> None:
        text = "items:\n  - run: |\n      cmd1\n      cmd2\n"
        self.assertEqual(parse_yaml(text), {"items": [{"run": "cmd1\ncmd2\n"}]})

    def test_sequence_item_continuation_value(self) -> None:
        # The block scalar is NOT the first key of the item; a preceding
        # single-line key (`name`) and a following one (`shell`) must survive.
        text = (
            "steps:\n"
            "  - name: build\n"
            "    run: |\n"
            "      make\n"
            "      make test\n"
            "    shell: bash\n"
        )
        self.assertEqual(
            parse_yaml(text),
            {
                "steps": [
                    {"name": "build", "run": "make\nmake test\n", "shell": "bash"}
                ]
            },
        )

    def test_sibling_keys_after_block_in_nested_mapping(self) -> None:
        # Mirrors the workflow `with:` block: a `path: |` scalar followed by
        # two more keys at the same indent.
        text = (
            "with:\n"
            "  name: artifact\n"
            "  path: |\n"
            "    a.txt\n"
            "    b.txt\n"
            "  if-no-files-found: error\n"
            "  retention-days: 30\n"
        )
        self.assertEqual(
            parse_yaml(text),
            {
                "with": {
                    "name": "artifact",
                    "path": "a.txt\nb.txt\n",
                    "if-no-files-found": "error",
                    "retention-days": 30,
                }
            },
        )

    def test_block_scalar_in_top_mapping_followed_by_key(self) -> None:
        text = "body: |\n  l1\n  l2\nnext: done\n"
        self.assertEqual(parse_yaml(text), {"body": "l1\nl2\n", "next": "done"})


class HeaderRecognitionTest(unittest.TestCase):
    """``is_block_scalar_header`` accepts only true block headers."""

    def test_accepts_all_indicator_forms(self) -> None:
        for header in ("|", ">", "|-", "|+", ">-", ">+", "|2", "|2-", "|-2"):
            self.assertTrue(is_block_scalar_header(header), header)

    def test_tolerates_trailing_comment(self) -> None:
        self.assertTrue(is_block_scalar_header("| # keep this literal"))

    def test_rejects_plain_and_flow_values(self) -> None:
        for value in ("hello", "[1, 2]", "{a: 1}", "|foo", ">bar", "", "true"):
            self.assertFalse(is_block_scalar_header(value), value)


class ChompedHeaderBodyTest(unittest.TestCase):
    """Explicit-indentation and chomp combinations expand correctly."""

    def test_explicit_indent_indicator(self) -> None:
        # ``|2`` fixes the content column 2 past the key indent regardless of
        # the first body line's deeper indent (which is then literal).
        text = "x: |2\n    a\n      b\n"
        self.assertEqual(parse_yaml(text), {"x": "  a\n    b\n"})

    def test_strip_with_explicit_indent(self) -> None:
        text = "x: |2-\n    a\n    b\n"
        self.assertEqual(parse_yaml(text), {"x": "  a\n  b"})


class NonRegressionTest(unittest.TestCase):
    """Existing parser behaviour for non-block values is unchanged."""

    def test_plain_scalars_and_flow_collections(self) -> None:
        text = "a: hello\nb: [1, 2, 3]\nc: {x: 1, y: 2}\nd: true\n"
        self.assertEqual(
            parse_yaml(text),
            {"a": "hello", "b": [1, 2, 3], "c": {"x": 1, "y": 2}, "d": True},
        )

    def test_single_line_run_value_unchanged(self) -> None:
        text = "steps:\n  - run: echo hi\n    name: greet\n"
        self.assertEqual(
            parse_yaml(text),
            {"steps": [{"run": "echo hi", "name": "greet"}]},
        )

    def test_multiline_flow_list_still_collected(self) -> None:
        text = "vals: [\n  1,\n  2,\n  3,\n]\n"
        self.assertEqual(parse_yaml(text), {"vals": [1, 2, 3]})


if __name__ == "__main__":
    unittest.main()
