"""Alias-aware MCP-tool coverage (ADR 0041 PR 2/4 follow-up).

The MCP node-id-taking tools (``weld_context``, ``weld_path``,
``weld_export``, ``weld_trace``, ``weld_impact``, ``weld_enrich``)
must transparently rewrite a legacy node ID to its canonical form
through the alias index recorded on each node's ``props.aliases``.

Coverage in this file pins the wiring at the MCP boundary:

- ``weld_context`` returns the canonical node when called with
  either canonical or alias.
- ``weld_path`` resolves both endpoints through the alias index.
- ``mcp_helpers.resolve_node_id_via_alias`` is exercised directly
  to cover its safety properties (None pass-through, canonical
  pass-through, missing target pass-through, attacker-shadow drop).
- An MCP context call with a canonical id whose alias slot
  contains an attacker-shadow does NOT redirect.

Tests run end-to-end through the MCP entry points, not by hand-
constructing graphs and calling internal helpers, so a regression
that wires alias resolution somewhere other than ``Graph.context`` /
``Graph.path`` / the ``mcp_helpers`` resolver is caught here.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.graph import Graph  # noqa: E402
from weld.mcp_helpers import resolve_node_id_via_alias  # noqa: E402
from weld.mcp_server import weld_context, weld_path  # noqa: E402


def _seed_graph(root: Path) -> None:
    """Write a tiny graph.json with a canonical node carrying one alias.

    Layout:
      skill:generic:foo  (canonical)  aliases=[skill:generic:foo:abc12345]
      skill:generic:bar  (canonical)  aliases=[]
      contains edge: foo -> bar
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
    g.save()


class WeldContextAliasTest(unittest.TestCase):
    def test_canonical_id_resolves(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _seed_graph(Path(td))
            res = weld_context("skill:generic:foo", root=td)
            self.assertEqual(res["node"]["id"], "skill:generic:foo")

    def test_legacy_alias_id_resolves(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _seed_graph(Path(td))
            res = weld_context("skill:generic:foo:abc12345", root=td)
            self.assertEqual(res["node"]["id"], "skill:generic:foo")
            self.assertNotIn("resolved_from", res)


class WeldPathAliasTest(unittest.TestCase):
    def test_path_resolves_both_endpoints_through_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _seed_graph(Path(td))
            res = weld_path(
                "skill:generic:foo:abc12345",  # alias on the from side
                "skill:generic:bar",            # canonical on the to side
                root=td,
            )
            self.assertIsNotNone(res.get("path"))
            ids = [n["id"] for n in res["path"]]
            self.assertEqual(ids[0], "skill:generic:foo")
            self.assertEqual(ids[-1], "skill:generic:bar")


class ResolveHelperContractTest(unittest.TestCase):
    def test_none_passes_through(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _seed_graph(Path(td))
            g = Graph(Path(td))
            g.load()
            self.assertIsNone(resolve_node_id_via_alias(g, None))

    def test_canonical_passes_through(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _seed_graph(Path(td))
            g = Graph(Path(td))
            g.load()
            self.assertEqual(
                resolve_node_id_via_alias(g, "skill:generic:foo"),
                "skill:generic:foo",
            )

    def test_alias_resolves(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _seed_graph(Path(td))
            g = Graph(Path(td))
            g.load()
            self.assertEqual(
                resolve_node_id_via_alias(g, "skill:generic:foo:abc12345"),
                "skill:generic:foo",
            )

    def test_unknown_passes_through_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _seed_graph(Path(td))
            g = Graph(Path(td))
            g.load()
            self.assertEqual(
                resolve_node_id_via_alias(g, "skill:generic:nope"),
                "skill:generic:nope",
            )


class WeldContextSecurityTest(unittest.TestCase):
    """Adversarial: an alias that names an unrelated canonical id must
    NOT redirect a query for the shadowed canonical id to the attacker.
    """

    def test_attacker_alias_does_not_shadow_victim(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".weld").mkdir(parents=True, exist_ok=True)
            # Hand-write a graph.json with the attacker / victim layout
            # so we can stuff an attacker alias that names the victim's
            # canonical id directly into the on-disk graph (bypassing
            # ``ensure_node``'s write-side guard) and verify the
            # lookup-side guard in ``build_alias_index`` still wins.
            graph = {
                "meta": {"version": 7, "schema_version": 1, "updated_at": ""},
                "nodes": {
                    "skill:generic:victim": {
                        "type": "skill", "label": "victim",
                        "props": {"aliases": []},
                    },
                    "skill:generic:attacker": {
                        "type": "skill", "label": "attacker",
                        "props": {"aliases": ["skill:generic:victim"]},
                    },
                },
                "edges": [],
            }
            (root / ".weld" / "graph.json").write_text(
                json.dumps(graph), encoding="utf-8")
            res = weld_context("skill:generic:victim", root=str(root))
            # The victim id must resolve to the victim, not the attacker.
            self.assertEqual(res["node"]["id"], "skill:generic:victim")


if __name__ == "__main__":
    unittest.main()
