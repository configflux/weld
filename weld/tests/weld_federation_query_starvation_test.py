"""Federated multi-token fan-out must not starve later children (bd v5t0).

At a federated root a MULTI-TOKEN query used to fill the result limit from
children in lexicographic name order and early-return, so an early-alphabet
child could consume the whole budget and later children never contributed.
The fix collects up to ``source_limit`` candidates from root + every child
and merges them with a fair, deterministic global rank before truncating.

These tests pin:

* the fan-out fairness (every source's top hit beats any source's 2nd hit);
* determinism / stability independent of child iteration order;
* single-repo parity (no children => root order preserved);
* the exact-style (single-token) path still surfaces exact symbol matches
  regardless of which child holds them (guards the shared collector refactor);
* an end-to-end guard over a real :class:`FederatedGraph`.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from weld._federation_query import query_federated
from weld.federation import FederatedGraph
from weld.tests._federation_sqlite_fixtures import graph_payload, make_workspace

_SEP = "\x1f"


class _FakeRootGraph:
    """Minimal root graph exposing the ``query`` seam the fan-out uses."""

    def __init__(self, matches: list[dict]) -> None:
        self._matches = matches

    def query(self, term: str, limit: int) -> dict:
        return {"matches": list(self._matches[:limit])}


class _FakeFederation:
    """In-memory stand-in for the federation seams ``query_federated`` calls.

    Only the data-source seams are faked; the real ranking/merge logic in
    ``weld._federation_query`` runs unchanged, so the ordering contract is
    what these tests actually exercise.
    """

    def __init__(
        self, root_matches: list[dict], children: dict[str, list[dict]],
    ) -> None:
        self._root_graph = _FakeRootGraph(root_matches)
        self._child_matches = children
        self._children = list(children)

    def _child_query_matches(
        self, name: str, term: str, limit: int,
    ) -> list[dict]:
        return list(self._child_matches.get(name, [])[:limit])

    def _prefix_node(self, name: str, node: dict) -> dict:
        prefixed = dict(node)
        prefixed["id"] = f"{name}{_SEP}{node['id']}"
        return prefixed

    def _decorate_node(self, node: dict) -> dict:
        return dict(node)

    def _query_payload(self, term: str, matches: list[dict]) -> dict:
        return {"query": term, "matches": list(matches)}


def _match(node_id: str, label: str, node_type: str = "symbol") -> dict:
    return {"id": node_id, "type": node_type, "label": label, "props": {}}


def _child_hits(name: str, count: int) -> list[dict]:
    return [
        _match(f"symbol:{name}:{i}", f"{name} payment gateway {i}")
        for i in range(count)
    ]


def _child_prefixes(matches: list[dict]) -> set[str]:
    return {str(m["id"]).split(_SEP, 1)[0] for m in matches}


class MultiTokenFanOutTest(unittest.TestCase):
    def test_multi_token_draws_from_every_child(self) -> None:
        """Fan-out must consider each child's top hit before any 2nd hit.

        Under the old lexicographic-fill path, ``alpha`` (5 hits) would have
        consumed the whole ``limit=4`` budget and beta/gamma/delta would have
        been starved. The fair merge surfaces every child's top hit.
        """
        children = {
            name: _child_hits(name, 5)
            for name in ("alpha", "beta", "gamma", "delta")
        }
        result = query_federated(_FakeFederation([], children), "payment gateway", 4)
        ids = [m["id"] for m in result["matches"]]
        self.assertEqual(4, len(ids))
        self.assertEqual(
            {"alpha", "beta", "gamma", "delta"},
            _child_prefixes(result["matches"]),
            "every child's top hit must survive the truncation, no starvation",
        )

    def test_multi_token_ordering_is_deterministic_across_child_order(self) -> None:
        """Output must not depend on child registration/iteration order."""
        data = {name: _child_hits(name, 4) for name in ("alpha", "beta", "gamma")}
        forward = {k: data[k] for k in ("alpha", "beta", "gamma")}
        shuffled = {k: data[k] for k in ("gamma", "alpha", "beta")}
        ids_forward = [
            m["id"]
            for m in query_federated(
                _FakeFederation([], forward), "payment gateway", 6,
            )["matches"]
        ]
        ids_shuffled = [
            m["id"]
            for m in query_federated(
                _FakeFederation([], shuffled), "payment gateway", 6,
            )["matches"]
        ]
        self.assertEqual(ids_forward, ids_shuffled)
        self.assertGreater(
            len(_child_prefixes([{"id": i} for i in ids_forward])), 1,
            "sanity: the deterministic result spans more than one child",
        )

    def test_single_repo_preserves_root_order(self) -> None:
        """No children => the root's own ranking is preserved (parity)."""
        root = [
            _match(f"symbol:r{i}", f"root payment gateway {i}") for i in range(3)
        ]
        result = query_federated(_FakeFederation(root, {}), "payment gateway", 10)
        self.assertEqual(
            ["symbol:r0", "symbol:r1", "symbol:r2"],
            [m["id"] for m in result["matches"]],
        )

    def test_zero_limit_returns_no_matches(self) -> None:
        children = {"alpha": _child_hits("alpha", 3)}
        result = query_federated(_FakeFederation([], children), "payment gateway", 0)
        self.assertEqual([], result["matches"])

    def test_exact_style_surfaces_exact_symbol_from_any_child(self) -> None:
        """Single-token exact-style path still floats the exact match up.

        Guards the shared-collector refactor: an exact symbol match living in
        the lexicographically-last child must still rank first.
        """
        children = {
            "alpha": [_match("symbol:alpha:g", "gateway helper")],
            "zeta": [_match("symbol:zeta:gateway", "gateway")],
        }
        result = query_federated(_FakeFederation([], children), "gateway", 5)
        self.assertEqual(
            f"zeta{_SEP}symbol:zeta:gateway",
            result["matches"][0]["id"],
            "exact symbol match must win regardless of child name order",
        )


def _payment_child(label: str, count: int) -> dict:
    nodes = {}
    for i in range(count):
        nodes[f"service:{label}:pay{i}"] = {
            "type": "service",
            "label": f"{label} payment gateway {i}",
            "props": {
                "file": f"{label}/pay{i}.py",
                "description": f"payment gateway service {i}",
            },
        }
    return graph_payload(nodes)


class MultiTokenFederationIntegrationTest(unittest.TestCase):
    def test_multi_token_query_draws_from_multiple_children(self) -> None:
        """Real FederatedGraph: a multi-token query spans multiple children."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_workspace(
                root,
                children=[
                    ("alpha", _payment_child("alpha", 6), False),
                    ("beta", _payment_child("beta", 6), False),
                    ("gamma", _payment_child("gamma", 6), False),
                ],
            )
            fg = FederatedGraph(root)
            try:
                matches = fg.query("payment gateway", limit=4)["matches"]
            finally:
                fg.close()
            self.assertEqual(4, len(matches))
            prefixes = _child_prefixes(matches)
            self.assertGreaterEqual(
                len(prefixes), 2,
                "multi-token fan-out must span more than the first child",
            )
            self.assertIn(
                "gamma", prefixes,
                "the lexicographically-last child must not be starved",
            )


if __name__ == "__main__":
    unittest.main()
