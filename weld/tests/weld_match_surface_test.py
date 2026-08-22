"""bd ph1g: the five matchers and the index must agree on the queryable fields.

A node's queryable surface used to be enumerated in six places -- the inverted
index plus five separate matchers -- each with a comment asking the next author
to keep it in step. Each of those copies had drifted from at least one other,
and every drift was discovered as a *ranking* bug, because a field wired into
the index but not into a matcher makes a node a candidate that is then silently
rejected. Nothing errors; the query just returns something else.

Five of the six now share :mod:`weld._match_surface`, so they cannot disagree
by construction. What is left is the pair that genuinely cannot be merged --
``query_index.node_tokens`` produces *tokens* while ``match_haystacks`` produces
*haystacks* -- and the one difference that is a decision rather than an
accident (the OR-fallback tier omits ``props.constants``, ADR 0075).

So this file pins two things:

1. every channel the index tokenizes is also matchable, asserted per channel
   against a single node that populates all of them;
2. the five matcher entry points return the same verdict for the same node, so
   a future "small and stable, so I'll just restate it" copy fails here.

The first assertion is a list this test states, not one it derives -- there is
no honest way to ask ``node_tokens`` which props it read. Adding a seventh
channel means adding it here too. That is a smaller obligation than the one it
replaces: with the matchers unified, the same edit no longer has to be made in
five other files as well.
"""

from __future__ import annotations

import unittest

from weld._coverage_admission import count_groups_hit, covered_group_count
from weld._federation_eager_index import _match_token_groups as _federation_match
from weld._graph_match import match_token_groups
from weld._match_surface import match_haystacks
from weld._sqlite_query import _match_token_groups as _sqlite_match
from weld.query_index import node_tokens

#: One node carrying a distinct, unmistakable value in every indexed channel.
#: The values are deliberately not words that appear in any other field, so a
#: hit proves *that* channel was read rather than a coincidence elsewhere.
_ALL_CHANNELS = {
    "type": "file",
    "label": "labelsentinel",
    "props": {
        "file": "pkg/filesentinel.py",
        "qualname": "mod.qualnamesentinel",
        "description": "prose carrying descriptionsentinel",
        "summary": "Opening line carrying summarysentinel.",
        "exports": ["exportsentinel"],
        "constants": ["CONSTANTSENTINEL"],
        "headings": ["Heading carrying headingsentinel"],
        "keywords": ["keywordsentinel"],
    },
}
_NID = "file:pkg/nidsentinel"

#: channel name -> the token that only that channel can supply.
_SENTINELS = {
    "nid": "nidsentinel",
    "label": "labelsentinel",
    "file": "filesentinel",
    "qualname": "qualnamesentinel",
    "description": "descriptionsentinel",
    "summary": "summarysentinel",
    "exports": "exportsentinel",
    "constants": "constantsentinel",
    "headings": "headingsentinel",
    "keywords": "keywordsentinel",
}

#: The five matcher entry points, all of which must agree. The two counting
#: forms are called through their public names rather than the shared helper --
#: the point is to catch a caller that stops delegating, which a test that only
#: exercised ``count_group_hits`` could never see.
_STRICT_MATCHERS = {
    "json": match_token_groups,
    "sqlite": _sqlite_match,
    "federation": _federation_match,
}


class EveryIndexedChannelIsMatchableTest(unittest.TestCase):
    """The drift that presents as a ranking bug."""

    def test_each_channel_reaches_the_index(self) -> None:
        tokens = node_tokens(_NID, _ALL_CHANNELS)
        for channel, sentinel in _SENTINELS.items():
            with self.subTest(channel=channel):
                self.assertTrue(
                    any(sentinel in token for token in tokens),
                    f"{channel} is not tokenized into the index",
                )

    def test_each_channel_reaches_the_match_surface(self) -> None:
        haystacks = match_haystacks(_NID, _ALL_CHANNELS)
        for channel, sentinel in _SENTINELS.items():
            with self.subTest(channel=channel):
                self.assertTrue(
                    any(sentinel in haystack for haystack in haystacks),
                    f"{channel} is indexed but not matchable -- a node that "
                    "becomes a candidate and is then rejected",
                )


class AllFiveMatchersAgreeTest(unittest.TestCase):
    """One node, one question, five callers, one answer."""

    def test_strict_matchers_admit_every_channel(self) -> None:
        for channel, sentinel in _SENTINELS.items():
            for name, matcher in _STRICT_MATCHERS.items():
                with self.subTest(channel=channel, matcher=name):
                    self.assertEqual(
                        matcher([[sentinel]], _NID, _ALL_CHANNELS), 1,
                        f"{name} does not match on {channel}",
                    )

    def test_strict_matchers_reject_a_missing_group_identically(self) -> None:
        """Widening the surface must never weaken strict-AND."""
        groups = [["summarysentinel"], ["nothing_matches_this"]]
        for name, matcher in _STRICT_MATCHERS.items():
            with self.subTest(matcher=name):
                self.assertEqual(matcher(groups, _NID, _ALL_CHANNELS), 0, name)

    def test_the_counting_matchers_do_not_short_circuit(self) -> None:
        """Admission and OR-fallback rank by partial coverage, so a miss counts.

        This is the one axis on which the five deliberately differ, and getting
        it backwards would silently turn the relaxation tiers into strict-AND.
        """
        groups = [["summarysentinel"], ["nothing_matches_this"]]
        self.assertEqual(covered_group_count(groups, _NID, _ALL_CHANNELS), 1)
        self.assertEqual(count_groups_hit(groups, _NID, _ALL_CHANNELS), 1)


class TheOneDeliberateDifferenceTest(unittest.TestCase):
    """ADR 0075: the OR-fallback tier keeps the field set it shipped with."""

    def test_or_fallback_does_not_see_constants(self) -> None:
        groups = [["constantsentinel"]]
        self.assertEqual(count_groups_hit(groups, _NID, _ALL_CHANNELS), 0)

    def test_admission_and_strict_and_both_do(self) -> None:
        groups = [["constantsentinel"]]
        self.assertEqual(covered_group_count(groups, _NID, _ALL_CHANNELS), 1)
        self.assertEqual(match_token_groups(groups, _NID, _ALL_CHANNELS), 1)


class SurfaceIsRobustToBadDataTest(unittest.TestCase):
    """A strategy filing the wrong shape must not take the read path down."""

    def test_non_string_bag_entries_are_skipped(self) -> None:
        node = {"label": "x", "props": {"keywords": ["ok", 7, None, ""]}}
        haystacks = match_haystacks("file:x", node)
        self.assertIn("ok", haystacks)
        self.assertEqual(match_token_groups([["ok"]], "file:x", node), 1)

    def test_a_node_with_no_props_still_matches_on_its_id(self) -> None:
        self.assertEqual(match_token_groups([["x"]], "file:x", {}), 1)

    def test_a_non_string_summary_is_coerced_not_crashed(self) -> None:
        node = {"label": "x", "props": {"summary": 7}}
        self.assertEqual(match_token_groups([["7"]], "file:x", node), 1)


if __name__ == "__main__":
    unittest.main()
