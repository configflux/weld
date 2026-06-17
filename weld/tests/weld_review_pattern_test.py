"""``wd review --pattern`` DSL parsing and ReDoS bounds (ADR 0055).

The DSL accepts: ``type=X source=Y target~regex from~regex`` (space-separated).
Regex tokens are anchored and length-bounded so a malicious pattern cannot
exhaust the matcher. This test pins the parsing surface and the regex bound.
"""
from __future__ import annotations

import unittest


from weld._review_pattern import (  # noqa: E402
    MAX_MATCH_LEN,
    MAX_REGEX_LEN,
    PatternError,
    Pattern,
    match,
    parse_pattern,
)


def _edge(**overrides) -> dict:
    base = {
        "from": "file:a.py",
        "to": "symbol:foo",
        "type": "calls",
        "props": {
            "source_strategy": "python_callgraph",
            "confidence": "speculative",
        },
    }
    base.update(overrides)
    return base


class ParsePatternTest(unittest.TestCase):
    """Empty / equality / regex tokens."""

    def test_empty_pattern_matches_everything(self) -> None:
        p = parse_pattern("")
        self.assertIsInstance(p, Pattern)
        self.assertTrue(match(p, _edge()))

    def test_type_equality(self) -> None:
        p = parse_pattern("type=calls")
        self.assertTrue(match(p, _edge(type="calls")))
        self.assertFalse(match(p, _edge(type="depends_on")))

    def test_source_equality(self) -> None:
        p = parse_pattern("source=python_callgraph")
        self.assertTrue(match(p, _edge()))
        e = _edge()
        e["props"] = {"source_strategy": "anthropic"}
        self.assertFalse(match(p, e))

    def test_target_regex(self) -> None:
        p = parse_pattern("target~^symbol:foo$")
        self.assertTrue(match(p, _edge(to="symbol:foo")))
        self.assertFalse(match(p, _edge(to="symbol:bar")))

    def test_from_regex(self) -> None:
        p = parse_pattern("from~^file:.*\\.py$")
        self.assertTrue(match(p, _edge(**{"from": "file:a.py"})))
        self.assertFalse(match(p, _edge(**{"from": "file:b.go"})))

    def test_compound_pattern_is_AND(self) -> None:
        p = parse_pattern("type=calls source=python_callgraph")
        self.assertTrue(match(p, _edge()))
        e = _edge()
        e["props"] = {"source_strategy": "anthropic"}
        self.assertFalse(match(p, e))


class PatternSecurityTest(unittest.TestCase):
    """The regex DSL token length is bounded to thwart ReDoS / OOM."""

    def test_overlong_regex_is_rejected(self) -> None:
        long_re = "a" * (MAX_REGEX_LEN + 1)
        with self.assertRaises(PatternError):
            parse_pattern(f"target~{long_re}")

    def test_invalid_regex_is_rejected(self) -> None:
        with self.assertRaises(PatternError):
            parse_pattern("target~[invalid(")

    def test_unknown_field_is_rejected(self) -> None:
        with self.assertRaises(PatternError):
            parse_pattern("unknown=foo")

    def test_unknown_operator_is_rejected(self) -> None:
        with self.assertRaises(PatternError):
            parse_pattern("type:calls")

    def test_nested_quantifier_is_rejected(self) -> None:
        """Patterns like ``(a+)+`` are textbook ReDoS catastrophic
        backtrackers. ADR 0055 § Security explicitly bounds the
        regex against this class."""
        for bad in (
            "target~(a+)+",
            "target~(a*)*",
            "target~(a+)*",
            "target~(ab+)+",
            "from~(x?)+",
            "target~(.+)+$",
        ):
            with self.assertRaises(PatternError, msg=f"missed: {bad}"):
                parse_pattern(bad)

    def test_match_input_is_clamped_to_max_len(self) -> None:
        """A safe regex against a giant target string is still bounded
        by the ``MAX_MATCH_LEN`` clamp."""
        # A clearly-bounded literal regex against a 5kB target string.
        # If clamping is off, the test still passes (the regex is safe),
        # but the contract -- inputs are clamped -- is the load-bearing
        # security claim. Pin the constant so the bound cannot silently
        # drift to "no clamp."
        self.assertLessEqual(MAX_MATCH_LEN, 8192)
        p = parse_pattern("target~^prefix")
        edge = {
            "from": "f", "to": "prefix" + "x" * (MAX_MATCH_LEN * 2),
            "type": "t", "props": {"source_strategy": "s"},
        }
        self.assertTrue(match(p, edge))


if __name__ == "__main__":
    unittest.main()
