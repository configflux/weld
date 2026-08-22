"""``props.keywords``: the strategy-owned query channel (ADR 0105, bd vpzh).

A node's queryable surface is a closed enumeration of prop names written out
*twice* -- once in ``weld.query_index.node_tokens`` (the inverted-index
prefilter) and once in ``weld.graph.Graph._match_token_groups`` (the match
test). ``props.rule`` was on neither, so ``wd query "py_library"`` returned no
matches against a graph holding 43 build-targets.

The drift between those two lists is the failure this file is built around:
a channel wired into the index but not the match surface makes a node a
*candidate* that then fails to match, which presents as a ranking bug and is
harder to find than a missing channel. So the two are asserted together, and
the end-to-end test goes through ``Graph.query`` where both must agree.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from weld.graph import Graph
from weld.query_index import build_index, node_tokens
from weld.strategies.bazel import extract

_KEYWORD_NODE = {
    "type": "build-target",
    "label": "//weld/strategies:strategies",
    "props": {"keywords": ["py_library"]},
}


class IndexChannelTest(unittest.TestCase):
    """Read path 1: the inverted-index prefilter."""

    def test_keywords_are_tokenized_into_the_index(self) -> None:
        tokens = node_tokens("build-target://weld/strategies:strategies", _KEYWORD_NODE)
        self.assertIn("py_library", tokens)

    def test_keyword_separators_split_like_every_other_field(self) -> None:
        """``py_library`` must also reach the node by its parts."""
        tokens = node_tokens("build-target://x:y", _KEYWORD_NODE)
        self.assertIn("py", tokens)
        self.assertIn("library", tokens)

    def test_absent_keywords_prop_is_harmless(self) -> None:
        tokens = node_tokens("file:a/b", {"label": "b", "props": {}})
        self.assertTrue(tokens)

    def test_non_string_keyword_entries_are_skipped(self) -> None:
        node = {"label": "x", "props": {"keywords": ["ok", 7, None, ""]}}
        tokens = node_tokens("file:x", node)
        self.assertIn("ok", tokens)

    def test_index_maps_the_keyword_to_the_node(self) -> None:
        index = build_index({"build-target://a:b": _KEYWORD_NODE})
        self.assertIn("build-target://a:b", index["py_library"])


class MatchSurfaceTest(unittest.TestCase):
    """Read path 2: the match test that admits a candidate."""

    def test_keyword_group_counts_as_a_hit(self) -> None:
        hits = Graph._match_token_groups(
            [["py_library"]], "build-target://a:b", _KEYWORD_NODE
        )
        self.assertEqual(hits, 1)

    def test_unmatched_group_still_rejects(self) -> None:
        """The channel widens what matches; it must not weaken strict-AND."""
        hits = Graph._match_token_groups(
            [["py_library"], ["nonexistent_token"]],
            "build-target://a:b",
            _KEYWORD_NODE,
        )
        self.assertEqual(hits, 0)

    def test_non_string_keyword_entries_do_not_crash_the_matcher(self) -> None:
        node = {"label": "x", "props": {"keywords": ["ok", 7, None]}}
        self.assertEqual(Graph._match_token_groups([["ok"]], "file:x", node), 1)


class BothReadPathsAgreeTest(unittest.TestCase):
    """The two enumerations must not drift apart again."""

    def test_every_keyword_indexed_is_also_matchable(self) -> None:
        nid = "build-target://a:b"
        for keyword in _KEYWORD_NODE["props"]["keywords"]:
            self.assertIn(
                keyword, node_tokens(nid, _KEYWORD_NODE), f"{keyword} not indexed"
            )
            self.assertEqual(
                Graph._match_token_groups([[keyword]], nid, _KEYWORD_NODE),
                1,
                f"{keyword} indexed but not matchable -- read paths have drifted",
            )


class BazelStrategyDeclaresKeywordsTest(unittest.TestCase):
    """The first consumer: every target declares its rule kind."""

    def _extract(self, build_text: str) -> dict:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "weld" / "strategies"
            pkg.mkdir(parents=True)
            (pkg / "BUILD.bazel").write_text(build_text)
            return extract(root, {"glob": "**/BUILD.bazel"}, {}).nodes

    def test_build_target_carries_its_rule_kind(self) -> None:
        nodes = self._extract('py_library(name = "strategies", srcs = ["a.py"])')
        props = nodes["build-target://weld/strategies:strategies"]["props"]
        self.assertEqual(props["keywords"], ["py_library"])

    def test_test_target_carries_its_own_kind(self) -> None:
        nodes = self._extract('py_test(name = "a_test", srcs = ["a_test.py"])')
        props = nodes["test-target://weld/strategies:a_test"]["props"]
        self.assertEqual(props["keywords"], ["py_test"])

    def test_node_type_is_not_duplicated_into_keywords(self) -> None:
        """It is the node ID's own prefix, so the nid channel already has it.

        Spending index on a token that is already indexed is the cost this bag
        cannot afford: ``candidate_nodes`` scans every indexed token per query
        token, so the bag stays short or it stops being affordable.
        """
        nodes = self._extract('py_library(name = "strategies", srcs = ["a.py"])')
        nid = "build-target://weld/strategies:strategies"
        self.assertNotIn("build-target", nodes[nid]["props"]["keywords"])
        self.assertIn("build", node_tokens(nid, nodes[nid]))
        self.assertIn("target", node_tokens(nid, nodes[nid]))

    def test_rule_prop_is_unchanged(self) -> None:
        """``keywords`` is additive -- ``props.rule`` stays the structured field."""
        nodes = self._extract('py_library(name = "strategies", srcs = ["a.py"])')
        props = nodes["build-target://weld/strategies:strategies"]["props"]
        self.assertEqual(props["rule"], "py_library")


class EndToEndQueryTest(unittest.TestCase):
    """The originally-failing query, through the real ``Graph.query``."""

    def test_py_library_returns_the_libraries(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".weld").mkdir()
            (root / ".weld" / "graph.json").write_text(
                json.dumps(
                    {
                        "meta": {},
                        "nodes": {
                            "build-target://weld/strategies:strategies": {
                                "type": "build-target",
                                "label": "//weld/strategies:strategies",
                                "props": {
                                    "rule": "py_library",
                                    "keywords": ["py_library"],
                                },
                            },
                            "test-target://weld/tests:a_test": {
                                "type": "test-target",
                                "label": "//weld/tests:a_test",
                                "props": {
                                    "rule": "py_test",
                                    "keywords": ["py_test"],
                                },
                            },
                        },
                        "edges": [],
                    }
                )
            )
            graph = Graph(root)
            graph.load()
            result = graph.query("py_library")
            found = {m["id"] for m in result.get("matches", [])}
            self.assertIn("build-target://weld/strategies:strategies", found)
            self.assertNotIn("test-target://weld/tests:a_test", found)


if __name__ == "__main__":
    unittest.main()
