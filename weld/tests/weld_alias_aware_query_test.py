"""Alias-aware ``Graph.query`` coverage (ADR 0041 PR 2/4 follow-up).

The query path resolves a legacy node ID through the alias index and
returns the canonical node as the only match, with its 1-hop
neighborhood. This lets external transcripts that reference the
pre-rename ID keep working through ``wd query``.

Behaviour pinned here:

- Querying by canonical id returns the canonical node.
- Querying by a legacy id registered under
  ``props.aliases = [<legacy>]`` returns the same canonical node.
- The match's ``id`` is always the canonical id, never the alias.
- The neighborhood payload includes the canonical node's neighbors
  even when the lookup came in via an alias.
- An unknown id (neither canonical nor alias) falls through to the
  normal BM25 path and returns no matches when nothing else hits.
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
    """Return a loaded Graph carrying:

    - ``skill:generic:foo`` (canonical) with alias
      ``skill:generic:foo:abc12345`` -- the pre-rename legacy id.
    - ``skill:generic:bar`` (canonical, no aliases) for cross-checks.
    - one ``contains`` edge so the neighborhood payload is non-empty.
    """
    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    g = Graph(root)
    g.load()
    g.add_node(
        "skill:generic:foo", "skill", "foo",
        {"aliases": ["skill:generic:foo:abc12345"]},
    )
    g.add_node("skill:generic:bar", "skill", "bar", {"aliases": []})
    g.add_edge("skill:generic:foo", "skill:generic:bar", "contains", {})
    return g


class AliasAwareQueryTest(unittest.TestCase):
    def test_canonical_id_returns_canonical_node(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            g = _build_graph_with_alias(Path(td))
            res = g.query("skill:generic:foo")
            ids = [m["id"] for m in res["matches"]]
            self.assertEqual(ids, ["skill:generic:foo"])

    def test_legacy_id_resolves_to_canonical_node(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            g = _build_graph_with_alias(Path(td))
            res = g.query("skill:generic:foo:abc12345")
            ids = [m["id"] for m in res["matches"]]
            # The match must be the canonical id, not the alias.
            self.assertEqual(ids, ["skill:generic:foo"])
            # Envelope still names the input term so callers can echo it.
            self.assertEqual(res["query"], "skill:generic:foo:abc12345")

    def test_alias_lookup_includes_neighborhood(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            g = _build_graph_with_alias(Path(td))
            res = g.query("skill:generic:foo:abc12345")
            neighbor_ids = {n["id"] for n in res["neighbors"]}
            self.assertIn("skill:generic:bar", neighbor_ids)

    def test_unknown_id_returns_no_matches(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            g = _build_graph_with_alias(Path(td))
            res = g.query("skill:generic:does-not-exist")
            # Nothing in the BM25 corpus matches the bare unknown id
            # either; envelope is empty.
            self.assertEqual(res["matches"], [])

    def test_alias_collision_with_canonical_does_not_shadow(self) -> None:
        """Adversarial: ``attacker`` aliases the ``victim`` canonical id.

        The lookup-side guard in ``build_alias_index`` drops the
        shadowing alias, so the query for ``victim`` resolves to
        ``victim`` itself, not the attacker.
        """
        with tempfile.TemporaryDirectory() as td:
            g = Graph(Path(td))
            g.load()
            g.add_node("skill:generic:victim", "skill", "victim", {"aliases": []})
            g.add_node(
                "skill:generic:attacker", "skill", "attacker",
                # Adversarial alias that names the victim's canonical id.
                {"aliases": ["skill:generic:victim"]},
            )
            res = g.query("skill:generic:victim")
            ids = [m["id"] for m in res["matches"]]
            self.assertEqual(ids, ["skill:generic:victim"])


if __name__ == "__main__":
    unittest.main()
