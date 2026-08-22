"""Alias-aware MCP-tool coverage (ADR 0041 PR 2/4 follow-up).

The MCP node-id-taking tools (``weld_context``, ``weld_path``,
``weld_export``, ``weld_trace``, ``weld_impact``, ``weld_enrich``)
must transparently rewrite a legacy node ID to its canonical form
through the alias index recorded on each node's ``props.aliases``.

Coverage in this file pins the wiring at the MCP boundary:

- ``weld_context`` returns the canonical node when called with
  either canonical or alias.
- ``weld_path`` resolves both endpoints through the alias index.
- ``weld_enrich`` enriches the canonical node when handed a legacy id,
  and still reports a genuine miss. Its rewrite is the one that does
  *not* happen at this boundary: enrichment resolves in the selection
  oracle it shares with the CLI, so the end-to-end call is what proves
  the tool kept the behaviour.
- ``mcp_helpers.resolve_node_id_via_alias`` is exercised directly
  to cover its safety properties (None pass-through, canonical
  pass-through, missing target pass-through, attacker-shadow drop).
- An MCP context call with a canonical id whose alias slot
  contains an attacker-shadow does NOT redirect.

Tests run end-to-end through the MCP entry points, not by hand-
constructing graphs and calling internal helpers, so a regression is
caught here wherever the resolution happens to be wired -- today
``Graph.context`` / ``Graph.path``, the ``mcp_helpers`` resolver, and
``weld._enrich_selection`` for enrichment.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


from weld.graph import Graph  # noqa: E402
from weld.mcp_helpers import resolve_node_id_via_alias  # noqa: E402
from weld.mcp_server import weld_context, weld_enrich, weld_path  # noqa: E402
from weld.providers import EnrichmentResult  # noqa: E402


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


class _StubProvider:
    """Enriches whatever node the selection handed it."""

    DEFAULT_MODEL = "stub-model"

    def enrich(self, node: dict, neighbors: list[dict], *, model: str):
        return EnrichmentResult(
            description=f"desc for {node['id']}", purpose=None,
            complexity_hint=None, suggested_tags=[], tokens_used=0,
            cost_usd=0.0,
        )


class WeldEnrichAliasTest(unittest.TestCase):
    """The provider-backed ``weld_enrich`` enriches through a legacy id.

    ``weld_enrich`` used to rewrite the id at its own boundary. The
    rewrite now happens in the selection oracle both enrichment paths
    share (so ``wd enrich --node <legacy>`` inherits it too), which makes
    the end-to-end call -- not the helper it used to reach for -- the
    thing worth pinning here.
    """

    def test_legacy_alias_id_enriches_the_canonical_node(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _seed_graph(Path(td))
            with mock.patch(
                "weld.enrich.resolve_provider", return_value=_StubProvider(),
            ):
                res = weld_enrich(
                    node_id="skill:generic:foo:abc12345",
                    provider="stub",
                    root=td,
                )

            self.assertNotIn("error", res)
            self.assertEqual(res["enriched"], ["skill:generic:foo"])

    def test_unknown_node_id_is_still_reported_as_a_miss(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _seed_graph(Path(td))
            with mock.patch(
                "weld.enrich.resolve_provider", return_value=_StubProvider(),
            ):
                res = weld_enrich(
                    node_id="skill:generic:nope", provider="stub", root=td,
                )

            self.assertIn("error", res)
            self.assertIn("skill:generic:nope", res["error"])


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
