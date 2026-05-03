"""Coverage for the alias-index resolver (ADR 0041 PR 2/4 follow-up).

:func:`weld._alias_index.build_alias_index` walks the merged ``nodes``
dict and produces a flat ``alias -> canonical_id`` mapping that
:func:`weld._alias_index.resolve_id` consumes at lookup time. The
contract is documented in detail in :mod:`weld._alias_index`; this
test file pins the behaviour:

- happy path: a node with one alias resolves both by canonical id
  and by alias to the same canonical id;
- collision guard: an alias whose value equals an unrelated
  canonical id is dropped from the index, so a query for that alias
  reaches the canonical owner of the id, never the aliasing node;
- self-alias: an alias equal to the host's own canonical id is a
  no-op (defensive; ``ensure_node`` already strips this at write
  time);
- duplicate claim: when two nodes claim the same alias, the first
  writer wins and a warning is emitted (deterministic across runs
  because each node's alias list is sorted at write time);
- malformed input: non-dict nodes, missing ``props``, non-list
  ``aliases``, empty / non-str alias entries are tolerated;
- missing query: :func:`resolve_id` returns ``None`` for an unknown
  query;
- stale-target guard: a canonical id removed between index build
  and lookup yields ``None`` rather than a dangling pointer.
"""

from __future__ import annotations

import logging
import sys
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld._alias_index import build_alias_index, resolve_id  # noqa: E402


def _node(canonical: str, *aliases: str) -> dict:
    return {
        "type": "skill",
        "label": canonical,
        "props": {"aliases": list(aliases)},
    }


class BuildAliasIndexHappyPathTest(unittest.TestCase):
    def test_single_alias_indexed(self) -> None:
        nodes = {"skill:generic:foo": _node(
            "skill:generic:foo", "skill:generic:foo:abc12345")}
        index = build_alias_index(nodes)
        self.assertEqual(
            index, {"skill:generic:foo:abc12345": "skill:generic:foo"})

    def test_resolve_canonical_returns_canonical(self) -> None:
        nodes = {"skill:generic:foo": _node(
            "skill:generic:foo", "skill:generic:foo:abc12345")}
        index = build_alias_index(nodes)
        self.assertEqual(
            resolve_id("skill:generic:foo", nodes, index),
            "skill:generic:foo",
        )

    def test_resolve_alias_returns_canonical(self) -> None:
        nodes = {"skill:generic:foo": _node(
            "skill:generic:foo", "skill:generic:foo:abc12345")}
        index = build_alias_index(nodes)
        self.assertEqual(
            resolve_id("skill:generic:foo:abc12345", nodes, index),
            "skill:generic:foo",
        )

    def test_multiple_aliases_all_indexed(self) -> None:
        nodes = {"skill:generic:foo": _node(
            "skill:generic:foo",
            "skill:generic:foo:abc12345",
            "skill:generic:foo:cd481235",
        )}
        index = build_alias_index(nodes)
        self.assertEqual(index, {
            "skill:generic:foo:abc12345": "skill:generic:foo",
            "skill:generic:foo:cd481235": "skill:generic:foo",
        })


class BuildAliasIndexCollisionGuardTest(unittest.TestCase):
    """An alias must NEVER shadow an unrelated canonical id."""

    def test_alias_shadowing_canonical_is_dropped(self) -> None:
        # Adversarial: ``attacker`` claims ``victim``'s canonical id as
        # an alias. The lookup-side guard must drop that entry so a
        # query for the victim id never lands on the attacker.
        nodes = {
            "skill:generic:victim": _node("skill:generic:victim"),
            "skill:generic:attacker": _node(
                "skill:generic:attacker", "skill:generic:victim"),
        }
        with self.assertLogs("weld._alias_index", level="WARNING") as caplog:
            index = build_alias_index(nodes)
        self.assertNotIn("skill:generic:victim", index)
        # The victim id resolves to the victim, not the attacker.
        self.assertEqual(
            resolve_id("skill:generic:victim", nodes, index),
            "skill:generic:victim",
        )
        self.assertTrue(any("shadow" in msg for msg in caplog.output))

    def test_self_alias_is_dropped(self) -> None:
        nodes = {"skill:generic:foo": _node(
            "skill:generic:foo", "skill:generic:foo")}
        index = build_alias_index(nodes)
        self.assertEqual(index, {})

    def test_duplicate_claim_first_writer_wins(self) -> None:
        # Two nodes both claim the same alias. Iteration order in the
        # nodes dict is insertion order (CPython 3.7+); the first
        # writer (``foo``) wins. The deterministic sort happens upstream
        # in ``ensure_node``; here we only verify that the lookup-side
        # guard is stable and emits a warning rather than silently
        # overwriting.
        nodes = {
            "skill:generic:foo": _node(
                "skill:generic:foo", "skill:generic:legacy"),
            "skill:generic:bar": _node(
                "skill:generic:bar", "skill:generic:legacy"),
        }
        with self.assertLogs("weld._alias_index", level="WARNING") as caplog:
            index = build_alias_index(nodes)
        self.assertEqual(
            index["skill:generic:legacy"], "skill:generic:foo")
        self.assertTrue(any("duplicate claim" in msg for msg in caplog.output))


class BuildAliasIndexMalformedInputTest(unittest.TestCase):
    """Malformed nodes are tolerated -- a sidecar build never raises."""

    def test_non_dict_node_skipped(self) -> None:
        # Use a plain dict with a non-dict value; build_alias_index
        # iterates ``nodes.items()`` so a stray ``None`` value must
        # not crash the build. The resulting index simply omits it.
        nodes: dict = {
            "skill:generic:foo": None,
            "skill:generic:bar": _node(
                "skill:generic:bar", "skill:generic:bar:legacy"),
        }
        # ``nodes`` is dict[str, Optional[dict]]; the type-narrow on
        # the first arg in build_alias_index is informational, not
        # enforced.
        index = build_alias_index(nodes)  # type: ignore[arg-type]
        self.assertEqual(
            index, {"skill:generic:bar:legacy": "skill:generic:bar"})

    def test_missing_props_skipped(self) -> None:
        nodes = {
            "skill:generic:foo": {"type": "skill", "label": "foo"},
            "skill:generic:bar": _node(
                "skill:generic:bar", "skill:generic:bar:legacy"),
        }
        index = build_alias_index(nodes)
        self.assertEqual(
            index, {"skill:generic:bar:legacy": "skill:generic:bar"})

    def test_non_list_aliases_skipped(self) -> None:
        nodes = {
            "skill:generic:foo": {
                "type": "skill", "label": "foo",
                "props": {"aliases": "not-a-list"},
            },
            "skill:generic:bar": _node(
                "skill:generic:bar", "skill:generic:bar:legacy"),
        }
        index = build_alias_index(nodes)
        self.assertEqual(
            index, {"skill:generic:bar:legacy": "skill:generic:bar"})

    def test_empty_and_non_str_aliases_skipped(self) -> None:
        nodes = {
            "skill:generic:foo": {
                "type": "skill", "label": "foo",
                "props": {"aliases": ["", None, 42, "skill:generic:foo:legacy"]},
            },
        }
        index = build_alias_index(nodes)
        self.assertEqual(
            index, {"skill:generic:foo:legacy": "skill:generic:foo"})


class ResolveIdMissingTest(unittest.TestCase):
    def test_unknown_query_returns_none(self) -> None:
        nodes = {"skill:generic:foo": _node("skill:generic:foo")}
        index = build_alias_index(nodes)
        self.assertIsNone(resolve_id("skill:generic:nope", nodes, index))

    def test_empty_query_returns_none(self) -> None:
        nodes = {"skill:generic:foo": _node("skill:generic:foo")}
        index = build_alias_index(nodes)
        self.assertIsNone(resolve_id("", nodes, index))

    def test_non_str_query_returns_none(self) -> None:
        nodes = {"skill:generic:foo": _node("skill:generic:foo")}
        index = build_alias_index(nodes)
        # Defensive: the public API is typed for str but callers may
        # forward arbitrary user-supplied values.
        self.assertIsNone(resolve_id(None, nodes, index))  # type: ignore[arg-type]

    def test_stale_alias_target_returns_none(self) -> None:
        # Index built when ``foo`` existed; the canonical was then
        # removed. Resolving by alias must NOT return a dangling
        # canonical id.
        original = {"skill:generic:foo": _node(
            "skill:generic:foo", "skill:generic:foo:legacy")}
        index = build_alias_index(original)
        nodes_after_removal: dict[str, dict] = {}
        self.assertIsNone(
            resolve_id("skill:generic:foo:legacy", nodes_after_removal, index))


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    unittest.main()
