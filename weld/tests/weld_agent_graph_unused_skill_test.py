"""Unit tests for ``weld._agent_graph_unused_skill.text_mentions_skill``.

Pins the word-boundary contract of the unused_skill audit suppression:
short or common skill names must NOT be silenced by incidental
substrings inside larger words. CLI integration coverage lives in
``weld_agent_graph_audit_cli_test.py``; this file pins the matcher
contract directly so behaviour regressions surface even if the
CLI-level fixtures are restructured.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld._agent_graph_unused_skill import (  # noqa: E402
    text_mentions_skill,
)


class TextMentionsSkillTest(unittest.TestCase):
    def test_whole_word_match_returns_true(self) -> None:
        self.assertTrue(text_mentions_skill(
            "test", ["please run test before push"],
        ))

    def test_substring_inside_larger_word_returns_false(self) -> None:
        self.assertFalse(text_mentions_skill(
            "test", ["attestation is required for release"],
        ))
        self.assertFalse(text_mentions_skill(
            "init", ["initialization happens at startup"],
        ))
        self.assertFalse(text_mentions_skill(
            "plan", ["plantation tour at noon"],
        ))

    def test_hyphenated_name_matches_literally(self) -> None:
        self.assertTrue(text_mentions_skill(
            "planner-helper",
            ["always invoke planner-helper before drafting"],
        ))

    def test_short_prefix_does_not_partially_match_hyphenated(self) -> None:
        # A skill named 'plan' must not match the prefix of
        # 'planner-helper' (which starts with 'plan' but continues with
        # word chars). 'planner' itself does match because the trailing
        # hyphen is a non-word char, so \\bplanner\\b lands cleanly --
        # this documents the boundary semantics.
        self.assertFalse(text_mentions_skill(
            "plan", ["always invoke planner-helper here"],
        ))
        self.assertTrue(text_mentions_skill(
            "planner", ["always invoke planner-helper here"],
        ))

    def test_case_insensitive_match(self) -> None:
        self.assertTrue(text_mentions_skill(
            "diagram-helper", ["Use Diagram-Helper for sketches."],
        ))

    def test_empty_name_returns_false(self) -> None:
        self.assertFalse(text_mentions_skill("", ["anything goes here"]))

    def test_no_bodies_returns_false(self) -> None:
        self.assertFalse(text_mentions_skill("test", []))

    def test_regex_metacharacters_in_name_are_escaped(self) -> None:
        # A skill name containing regex metachars must be treated
        # literally; otherwise '.' would match any single char and the
        # check would over-suppress.
        self.assertFalse(text_mentions_skill(
            "v1.0", ["v1X0 release notes"],
        ))
        self.assertTrue(text_mentions_skill(
            "v1.0", ["see v1.0 release notes"],
        ))

    def test_punctuation_around_name_still_matches(self) -> None:
        # Word boundaries around punctuation (commas, periods, parens)
        # should still allow the match to fire -- this is the most
        # common shape of a skill name in prose.
        self.assertTrue(text_mentions_skill(
            "test", ["before merge, run test."],
        ))
        self.assertTrue(text_mentions_skill(
            "test", ["(see test for details)"],
        ))


if __name__ == "__main__":
    unittest.main()
