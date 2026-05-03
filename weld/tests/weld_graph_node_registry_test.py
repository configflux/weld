"""Unit tests for the unified ``ensure_node`` primitive (ADR 0041, Layer 2).

Covers ``weld._graph_node_registry.ensure_node``:

- Insert: a brand-new node creates a fully populated dict.
- Merge: a second claim on an existing ID reconciles authority,
  unions list-typed props, deep-merges dict-typed props, and obeys
  the lexicographic-min tie-break for scalars.
- Order-independence: the same set of claims in any order produces
  the same final state.
- Idempotence: ``merge(a, a) == a``.

The tests do not exercise any specific strategy; they assert the
primitive's invariants directly so call sites can rely on the
contract regardless of which strategy is calling.
"""

from __future__ import annotations

import sys
import unittest
from itertools import permutations
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld._graph_node_registry import ensure_node  # noqa: E402


class EnsureNodeInsertTest(unittest.TestCase):
    """Behaviour when *node_id* is absent from *nodes*."""

    def test_inserts_with_type_and_label(self) -> None:
        nodes: dict[str, dict] = {}
        ensure_node(
            nodes,
            "skill:generic:foo",
            "skill",
            source_strategy="agent_graph",
            source_path=".claude/skills/foo.md",
            authority="canonical",
        )
        self.assertIn("skill:generic:foo", nodes)
        n = nodes["skill:generic:foo"]
        self.assertEqual(n["type"], "skill")
        self.assertEqual(n["label"], "foo")
        self.assertEqual(n["props"]["authority"], "canonical")
        self.assertEqual(
            n["props"]["sources"],
            ["agent_graph:.claude/skills/foo.md"],
        )
        self.assertEqual(n["props"]["aliases"], [])

    def test_label_falls_back_to_name_prop(self) -> None:
        nodes: dict[str, dict] = {}
        ensure_node(
            nodes,
            "skill:generic:foo",
            "skill",
            source_strategy="x",
            source_path=None,
            authority="canonical",
            props={"name": "Pretty Foo"},
        )
        self.assertEqual(nodes["skill:generic:foo"]["label"], "Pretty Foo")

    def test_no_source_path_records_strategy_only(self) -> None:
        nodes: dict[str, dict] = {}
        ensure_node(
            nodes,
            "skill:generic:foo",
            "skill",
            source_strategy="x",
            source_path=None,
            authority="canonical",
        )
        self.assertEqual(nodes["skill:generic:foo"]["props"]["sources"], ["x"])


class EnsureNodeMergeTest(unittest.TestCase):
    """Behaviour when *node_id* already exists in *nodes*."""

    def test_authority_precedence(self) -> None:
        nodes: dict[str, dict] = {}
        ensure_node(
            nodes,
            "skill:generic:foo",
            "skill",
            source_strategy="reference",
            source_path="a.md",
            authority="referenced",
        )
        ensure_node(
            nodes,
            "skill:generic:foo",
            "skill",
            source_strategy="canonical_disco",
            source_path="b.md",
            authority="canonical",
        )
        self.assertEqual(
            nodes["skill:generic:foo"]["props"]["authority"], "canonical"
        )

    def test_authority_max_is_used_regardless_of_arrival_order(self) -> None:
        for first, second in [("referenced", "canonical"), ("canonical", "referenced")]:
            nodes: dict[str, dict] = {}
            ensure_node(
                nodes,
                "skill:generic:foo",
                "skill",
                source_strategy="x",
                source_path="x.md",
                authority=first,  # type: ignore[arg-type]
            )
            ensure_node(
                nodes,
                "skill:generic:foo",
                "skill",
                source_strategy="y",
                source_path="y.md",
                authority=second,  # type: ignore[arg-type]
            )
            self.assertEqual(
                nodes["skill:generic:foo"]["props"]["authority"], "canonical"
            )

    def test_sources_set_union(self) -> None:
        nodes: dict[str, dict] = {}
        ensure_node(
            nodes,
            "skill:generic:foo",
            "skill",
            source_strategy="a",
            source_path="x.md",
            authority="referenced",
        )
        ensure_node(
            nodes,
            "skill:generic:foo",
            "skill",
            source_strategy="b",
            source_path="y.md",
            authority="canonical",
        )
        self.assertEqual(
            nodes["skill:generic:foo"]["props"]["sources"],
            ["a:x.md", "b:y.md"],
        )

    def test_aliases_set_union(self) -> None:
        nodes: dict[str, dict] = {}
        ensure_node(
            nodes,
            "skill:generic:foo",
            "skill",
            source_strategy="a",
            source_path=None,
            authority="canonical",
            props={"aliases": ["skill:generic:foo:abc12345"]},
        )
        ensure_node(
            nodes,
            "skill:generic:foo",
            "skill",
            source_strategy="b",
            source_path=None,
            authority="canonical",
            props={"aliases": ["skill:generic:foo:cd481235"]},
        )
        self.assertEqual(
            nodes["skill:generic:foo"]["props"]["aliases"],
            [
                "skill:generic:foo:abc12345",
                "skill:generic:foo:cd481235",
            ],
        )

    def test_higher_authority_wins_scalar_props(self) -> None:
        nodes: dict[str, dict] = {}
        ensure_node(
            nodes,
            "topic:cmd_vel",
            "topic",
            source_strategy="ref",
            source_path=None,
            authority="referenced",
            props={"description": "stub"},
        )
        ensure_node(
            nodes,
            "topic:cmd_vel",
            "topic",
            source_strategy="disco",
            source_path=None,
            authority="canonical",
            props={"description": "Velocity command topic"},
        )
        self.assertEqual(
            nodes["topic:cmd_vel"]["props"]["description"],
            "Velocity command topic",
        )

    def test_equal_authority_scalar_lexicographic_min(self) -> None:
        nodes: dict[str, dict] = {}
        ensure_node(
            nodes,
            "x:y",
            "x",
            source_strategy="a",
            source_path=None,
            authority="canonical",
            props={"description": "zzz"},
        )
        ensure_node(
            nodes,
            "x:y",
            "x",
            source_strategy="b",
            source_path=None,
            authority="canonical",
            props={"description": "aaa"},
        )
        # lex-min of the two strings.
        self.assertEqual(nodes["x:y"]["props"]["description"], "aaa")

    def test_list_props_set_union_and_sorted(self) -> None:
        nodes: dict[str, dict] = {}
        ensure_node(
            nodes,
            "file:weld/x",
            "file",
            source_strategy="a",
            source_path=None,
            authority="canonical",
            props={"exports": ["foo", "bar"]},
        )
        ensure_node(
            nodes,
            "file:weld/x",
            "file",
            source_strategy="b",
            source_path=None,
            authority="canonical",
            props={"exports": ["baz", "bar"]},
        )
        self.assertEqual(
            nodes["file:weld/x"]["props"]["exports"],
            ["bar", "baz", "foo"],
        )

    def test_dict_props_deep_merge(self) -> None:
        nodes: dict[str, dict] = {}
        ensure_node(
            nodes,
            "x:y",
            "x",
            source_strategy="a",
            source_path=None,
            authority="canonical",
            props={"enrichment": {"summary": "left", "tags": ["one"]}},
        )
        ensure_node(
            nodes,
            "x:y",
            "x",
            source_strategy="b",
            source_path=None,
            authority="canonical",
            props={"enrichment": {"summary": "left", "tags": ["two"]}},
        )
        self.assertEqual(
            nodes["x:y"]["props"]["enrichment"],
            {"summary": "left", "tags": ["one", "two"]},
        )


class EnsureNodeOrderIndependenceTest(unittest.TestCase):
    """The merge is provably order-independent (ADR 0041 § Layer 2)."""

    def _apply(self, claims: list[dict]) -> dict:
        nodes: dict[str, dict] = {}
        for claim in claims:
            ensure_node(nodes, **claim)
        return nodes["x:y"]

    def test_three_way_merge_is_order_independent(self) -> None:
        claims = [
            {
                "node_id": "x:y",
                "node_type": "x",
                "source_strategy": "alpha",
                "source_path": "a",
                "authority": "referenced",
                "props": {"exports": ["a"], "description": "b"},
            },
            {
                "node_id": "x:y",
                "node_type": "x",
                "source_strategy": "beta",
                "source_path": "b",
                "authority": "canonical",
                "props": {"exports": ["b"], "description": "c"},
            },
            {
                "node_id": "x:y",
                "node_type": "x",
                "source_strategy": "gamma",
                "source_path": "c",
                "authority": "derived",
                "props": {"exports": ["c"], "description": "a"},
            },
        ]
        results = [self._apply(list(perm)) for perm in permutations(claims)]
        for r in results[1:]:
            self.assertEqual(r, results[0])

    def test_four_way_merge_is_order_independent(self) -> None:
        claims = [
            {
                "node_id": "x:y",
                "node_type": "x",
                "source_strategy": s,
                "source_path": s,
                "authority": auth,
                "props": {"exports": [s], "description": s},
            }
            for s, auth in [
                ("a", "referenced"),
                ("b", "derived"),
                ("c", "external"),
                ("d", "canonical"),
            ]
        ]
        results = [self._apply(list(perm)) for perm in permutations(claims)]
        for r in results[1:]:
            self.assertEqual(r, results[0])

    def test_idempotence(self) -> None:
        nodes_a: dict[str, dict] = {}
        nodes_b: dict[str, dict] = {}
        ensure_node(
            nodes_a,
            "x:y",
            "x",
            source_strategy="s",
            source_path="p",
            authority="canonical",
            props={"exports": ["foo"]},
        )
        for _ in range(3):
            ensure_node(
                nodes_b,
                "x:y",
                "x",
                source_strategy="s",
                source_path="p",
                authority="canonical",
                props={"exports": ["foo"]},
            )
        self.assertEqual(nodes_a, nodes_b)


if __name__ == "__main__":
    unittest.main()
