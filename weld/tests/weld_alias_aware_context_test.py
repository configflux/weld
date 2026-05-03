"""Alias-aware ``Graph.context`` coverage (ADR 0041 PR 2/4 follow-up).

The 1-hop context lookup resolves a legacy node ID through the alias
index before the neighborhood query, so external transcripts that
reference the pre-rename ID keep working through ``wd context``.

Behaviour pinned here:

- Querying by canonical id returns the canonical node + its
  neighborhood, no ``resolved_from`` tag.
- Querying by a legacy id registered under
  ``props.aliases = [<legacy>]`` returns the same canonical node +
  neighborhood. Alias resolution is a guaranteed-equivalence
  rewrite and must NOT add a ``resolved_from`` tag (that tag is
  reserved for the soft BM25 fallback).
- An unknown id falls through to the BM25 fallback and then to
  ``error: node not found`` when nothing else hits.
- Adversarial alias-shadowing does not redirect a query for the
  shadowed canonical id to the attacker.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.graph import Graph  # noqa: E402


def _build_graph_with_alias(root: Path) -> Graph:
    g = Graph(root)
    g.load()
    g.add_node(
        "skill:generic:foo", "skill", "foo",
        {"aliases": ["skill:generic:foo:abc12345"]},
    )
    g.add_node("skill:generic:bar", "skill", "bar", {"aliases": []})
    g.add_edge("skill:generic:foo", "skill:generic:bar", "contains", {})
    return g


class AliasAwareContextTest(unittest.TestCase):
    def test_canonical_id_returns_canonical_context(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            g = _build_graph_with_alias(Path(td))
            res = g.context("skill:generic:foo")
            self.assertIn("node", res)
            self.assertEqual(res["node"]["id"], "skill:generic:foo")
            self.assertNotIn("resolved_from", res)

    def test_legacy_id_resolves_to_canonical_context(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            g = _build_graph_with_alias(Path(td))
            res = g.context("skill:generic:foo:abc12345")
            self.assertIn("node", res)
            self.assertEqual(res["node"]["id"], "skill:generic:foo")
            # Alias resolution is a guaranteed rewrite, not a soft
            # fallback; ``resolved_from`` must NOT appear.
            self.assertNotIn("resolved_from", res)
            neighbor_ids = {n["id"] for n in res["neighbors"]}
            self.assertIn("skill:generic:bar", neighbor_ids)

    def test_unknown_id_returns_error_with_no_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            g = _build_graph_with_alias(Path(td))
            res = g.context("skill:generic:does-not-exist", fallback=False)
            self.assertIn("error", res)

    def test_alias_collision_with_canonical_does_not_shadow(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            g = Graph(Path(td))
            g.load()
            g.add_node("skill:generic:victim", "skill", "victim", {"aliases": []})
            g.add_node(
                "skill:generic:attacker", "skill", "attacker",
                {"aliases": ["skill:generic:victim"]},
            )
            res = g.context("skill:generic:victim")
            self.assertEqual(res["node"]["id"], "skill:generic:victim")


if __name__ == "__main__":
    unittest.main()
