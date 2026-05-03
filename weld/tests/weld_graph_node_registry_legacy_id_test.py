"""Coverage for the ``legacy_id`` parameter on ``ensure_node`` (ADR 0041).

The optional ``legacy_id`` parameter on
:func:`weld._graph_node_registry.ensure_node` records the pre-rename ID
form on the merged node's ``props.aliases`` list so the alias-aware
lookup in :mod:`weld.graph_query` and :mod:`weld.graph_context` can
resolve old IDs transparently for one minor version
(see ADR 0041 § Migration).

Behaviour covered here:

- When supplied and different from ``node_id``, the legacy form is
  appended to ``props.aliases`` (sorted, deduped) on insert.
- The same rule applies on merge.
- When equal to ``node_id``, ``legacy_id`` is silently ignored
  (no rename actually occurred for this entity).
- Existing aliases dedupe with the incoming ``legacy_id``.
- When the legacy form would shadow an existing canonical node id,
  the call raises :class:`ValueError` rather than poison the
  alias index. This is the security guard called out in the
  task brief ("never alias an existing canonical").

Split from :mod:`weld.tests.weld_graph_node_registry_test` to keep both
files inside the 400-line cap (ADR-policy line-count rule).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld._graph_node_registry import ensure_node  # noqa: E402


class EnsureNodeLegacyIdInsertTest(unittest.TestCase):
    """``legacy_id`` is recorded on the new node's ``aliases`` list."""

    def test_legacy_id_recorded_in_aliases_on_insert(self) -> None:
        nodes: dict[str, dict] = {}
        ensure_node(
            nodes,
            "skill:generic:foo",
            "skill",
            source_strategy="agent_graph",
            source_path="skills/foo/SKILL.md",
            authority="external",
            legacy_id="skill:generic:foo:abc12345",
        )
        self.assertEqual(
            nodes["skill:generic:foo"]["props"]["aliases"],
            ["skill:generic:foo:abc12345"],
        )

    def test_legacy_id_equal_to_node_id_is_ignored(self) -> None:
        nodes: dict[str, dict] = {}
        ensure_node(
            nodes,
            "skill:generic:foo",
            "skill",
            source_strategy="x",
            source_path=None,
            authority="canonical",
            legacy_id="skill:generic:foo",
        )
        self.assertEqual(nodes["skill:generic:foo"]["props"]["aliases"], [])


class EnsureNodeLegacyIdMergeTest(unittest.TestCase):
    """``legacy_id`` merges into an existing node's ``aliases`` list."""

    def test_legacy_id_recorded_in_aliases_on_merge(self) -> None:
        nodes: dict[str, dict] = {}
        ensure_node(
            nodes,
            "skill:generic:foo",
            "skill",
            source_strategy="a",
            source_path="x.md",
            authority="external",
        )
        ensure_node(
            nodes,
            "skill:generic:foo",
            "skill",
            source_strategy="b",
            source_path="y.md",
            authority="external",
            legacy_id="skill:generic:foo:cd481235",
        )
        self.assertEqual(
            nodes["skill:generic:foo"]["props"]["aliases"],
            ["skill:generic:foo:cd481235"],
        )

    def test_legacy_id_dedup_with_existing_aliases(self) -> None:
        nodes: dict[str, dict] = {}
        ensure_node(
            nodes,
            "skill:generic:foo",
            "skill",
            source_strategy="x",
            source_path=None,
            authority="canonical",
            props={"aliases": ["skill:generic:foo:abc12345"]},
            legacy_id="skill:generic:foo:abc12345",
        )
        self.assertEqual(
            nodes["skill:generic:foo"]["props"]["aliases"],
            ["skill:generic:foo:abc12345"],
        )


class EnsureNodeLegacyIdSecurityTest(unittest.TestCase):
    """Security: a ``legacy_id`` must never shadow an unrelated canonical node.

    If a strategy with attacker-controlled inputs (e.g., a path derived
    from a user-supplied filename) produced a ``legacy_id`` equal to
    some other node's canonical id, alias resolution would silently
    steer queries for the attacker's legacy ID to the victim node's
    identity -- a node-shadowing attack. ``ensure_node`` must refuse
    to alias into an existing canonical key.
    """

    def test_collision_with_existing_canonical_raises(self) -> None:
        nodes: dict[str, dict] = {}
        ensure_node(
            nodes,
            "skill:generic:victim",
            "skill",
            source_strategy="a",
            source_path="victim.md",
            authority="canonical",
        )
        with self.assertRaises(ValueError):
            ensure_node(
                nodes,
                "skill:generic:attacker",
                "skill",
                source_strategy="b",
                source_path="attacker.md",
                authority="external",
                legacy_id="skill:generic:victim",
            )
        # Victim node remains untouched; attacker node never created.
        self.assertEqual(sorted(nodes.keys()), ["skill:generic:victim"])
        self.assertEqual(
            nodes["skill:generic:victim"]["props"].get("aliases"), [],
        )


if __name__ == "__main__":
    unittest.main()
