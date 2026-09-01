"""Finding 08 regression: wd brief must rank exact-identifier matches first.

When many nodes share a query token (files + package + a class symbol whose
own name is the query), ``wd brief`` used to emit results in id order (type
then lexical) and label every one 'direct match'. The exact class symbol --
the node the query names -- landed at position 13 of 20 (transcript 08),
while ``wd query`` ranked it #1 via the shared exact-identifier preference.

This module pins the fix, whose contract lives in :mod:`weld._brief_rank`:

  - :func:`weld._brief_rank.sort_key` applies the same
    :func:`weld.ranking.exact_symbol_match_rank` preference wd query uses,
    so an exact symbol label/qualname hit sorts to the top of its bucket
    regardless of node id.
  - :func:`weld._brief_rank.primary_relevance` discriminates the per-node
    ``relevance`` field 'exact match' vs 'token match'; neighbours get
    'related ...' from :mod:`weld.brief`, so callers can re-rank.

Coverage is a synthetic graph modelling the OrderReplayer collision.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld.brief import brief
from weld.contract import SCHEMA_VERSION
from weld.graph import Graph

_TS = "2026-08-29T12:00:00+00:00"


def _make_graph(nodes: dict, edges: list | None = None) -> Graph:
    tmpdir = Path(tempfile.mkdtemp())
    g = Graph(tmpdir)
    g.load()
    g._data = {
        "meta": {"version": SCHEMA_VERSION, "updated_at": _TS, "git_sha": "ex08"},
        "nodes": nodes,
        "edges": edges or [],
    }
    return g


def _order_replayer_nodes() -> dict:
    """Model transcript 08: many nodes share the 'orderreplayer' token.

    Files and a package sort ahead of the exact class symbol under the old
    id-order sort, burying the node the query actually names.
    """
    nodes: dict = {}
    # File nodes -- share the token via their path.
    for stem in (
        "IReplayTarget", "OrderLogEntry", "OrderReplayer",
        "ReplayOptions", "ReplayProgram", "ReplayUtilities",
    ):
        nid = f"file:src/OrderReplayer/{stem}"
        nodes[nid] = {
            "id": nid, "type": "file",
            "label": stem, "confidence": "definite",
            "props": {"path": f"src/OrderReplayer/{stem}"},
        }
    # Package node -- shares the token via its qualified name.
    pkg = "package:csharp:acme.platform.ordergateway.orderreplayer"
    nodes[pkg] = {
        "id": pkg, "type": "package",
        "label": "orderreplayer", "confidence": "definite", "props": {},
    }
    # Symbol nodes -- one of them (OrderReplayer:OrderReplayer) is the exact
    # class match whose label equals the query.
    symbols = [
        ("IReplayTarget", "IReplayTarget"),
        ("IReplayTarget", "Send"),
        ("OrderLogEntry", "OrderLogEntry"),
        ("OrderReplayer", "OrderReplayer"),   # <- exact class match
        ("OrderReplayer", "ReplayOrder"),
        ("ReplayOptions", "ReplayOptions"),
        ("ReplayProgram", "Main"),
    ]
    for owner, name in symbols:
        nid = f"symbol:csharp:src.OrderReplayer.{owner}:{name}"
        nodes[nid] = {
            "id": nid, "type": "symbol",
            "label": name, "confidence": "definite",
            "props": {"qualname": f"src.OrderReplayer.{owner}.{name}"},
        }
    return nodes


class BriefExactMatchOrderingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.graph = _make_graph(_order_replayer_nodes())

    def test_exact_symbol_ranks_first_in_primary(self) -> None:
        result = brief(self.graph, "OrderReplayer", limit=20)
        primary = result["primary"]
        self.assertTrue(primary, "expected primary matches for OrderReplayer")
        top = primary[0]
        self.assertEqual(
            top["id"],
            "symbol:csharp:src.OrderReplayer.OrderReplayer:OrderReplayer",
            f"exact class match must rank first, got {top['id']!r}",
        )

    def test_exact_match_relevance_discriminates(self) -> None:
        result = brief(self.graph, "OrderReplayer", limit=20)
        by_id = {n["id"]: n for n in result["primary"]}
        exact = by_id["symbol:csharp:src.OrderReplayer.OrderReplayer:OrderReplayer"]
        self.assertEqual(exact["relevance"], "exact match")
        # A non-exact direct match keeps a distinct, token-level label.
        other = by_id["symbol:csharp:src.OrderReplayer.OrderReplayer:ReplayOrder"]
        self.assertEqual(other["relevance"], "token match")
        # The three relevance vocabularies stay disjoint.
        self.assertNotEqual(exact["relevance"], other["relevance"])

    def test_all_primary_relevance_from_discriminating_vocab(self) -> None:
        result = brief(self.graph, "OrderReplayer", limit=20)
        allowed = {"exact match", "token match"}
        for node in result["primary"]:
            self.assertIn(
                node["relevance"], allowed,
                f"{node['id']} carried non-discriminating relevance "
                f"{node['relevance']!r}",
            )

    def test_exactly_one_exact_match_labelled(self) -> None:
        result = brief(self.graph, "OrderReplayer", limit=20)
        exact = [n for n in result["primary"] if n["relevance"] == "exact match"]
        self.assertEqual(
            len(exact), 1,
            "only the symbol whose label equals the query is an exact match",
        )

    def test_envelope_keys_unchanged(self) -> None:
        """Field set/order contract is preserved (non-interaction query)."""
        result = brief(self.graph, "OrderReplayer", limit=20)
        self.assertEqual(
            list(result.keys()),
            [
                "brief_version", "query", "primary", "interfaces",
                "docs", "build", "boundaries", "edges",
                "provenance", "warnings",
            ],
        )


if __name__ == "__main__":
    unittest.main()
