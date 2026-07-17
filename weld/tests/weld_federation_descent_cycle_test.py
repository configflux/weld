"""Root-less containment-cycle descent for federated ``repo:<name>`` nodes.

ADR 0091: a child whose ``contains`` edges form a root-less cycle (``A contains
B``, ``B contains A``) has no in-degree-0 containment root, so the ADR 0081 rule
would strand the whole component -- unreachable from ``repo:<name>`` via
``context``/``path``. Descent now anchors one deterministic representative (the
lexicographically-smallest id) per *source* strongly-connected component, so the
component is reachable while acyclic children stay byte-identical and the
containment-root invariant holds. Split from ``weld_federation_descent_test.py``
so neither file breaches the 400-line cap; shared scaffolding lives in
``_federation_descent_fixtures.py``.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from weld._federation_descent import child_containment_roots, descent_edges_for
from weld.federation import FederatedGraph, prefix_node_id
from weld.tests._federation_descent_fixtures import (
    _child_payload,
    _init_repo,
    _write_graph,
    _write_root_graph,
    _write_workspaces,
)
from weld.workspace import ChildEntry


class FederatedDescentCycleTest(unittest.TestCase):
    # Item 3: a pure containment cycle -- reachable via its SCC anchor (ADR 0091).
    def _cycle_workspace(self) -> Path:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        repo = _init_repo(root / "repo-cycle")
        _write_graph(repo, _child_payload(
            {
                "symbol:cycle-a": {"type": "symbol", "label": "a", "props": {}},
                "symbol:cycle-b": {"type": "symbol", "label": "b", "props": {}},
            },
            [
                {"from": "symbol:cycle-a", "to": "symbol:cycle-b", "type": "contains", "props": {}},
                {"from": "symbol:cycle-b", "to": "symbol:cycle-a", "type": "contains", "props": {}},
            ],
        ))
        _write_workspaces(root, [ChildEntry(name="repo-cycle", path="repo-cycle")])
        _write_root_graph(root, ["repo-cycle"])
        return root

    def test_contains_cycle_child_is_reachable_via_scc_anchor(self) -> None:
        """FIXED (bd j995 / ADR 0091): a root-less containment cycle is reachable.

        Both nodes are the ``to`` of a ``contains`` edge, so the child has no
        in-degree-0 containment root. ADR 0091 anchors descent to the
        lexicographically-smallest id of the cycle's source SCC
        (``symbol:cycle-a``); ``context`` then surfaces it and ``path`` descends
        into either member through the child-internal ``contains`` edges. This
        flips the previously-pinned limitation.
        """
        root = self._cycle_workspace()
        anchor = prefix_node_id("repo-cycle", "symbol:cycle-a")
        partner = prefix_node_id("repo-cycle", "symbol:cycle-b")
        with FederatedGraph(root) as graph:
            child = graph._load_child("repo-cycle")
            # The min-id member represents the otherwise root-less component.
            self.assertEqual(child_containment_roots(child), ["symbol:cycle-a"])
            edges = descent_edges_for(graph, "repo:repo-cycle")
            ctx = graph.context("repo:repo-cycle")
            to_anchor = graph.path("repo:repo-cycle", anchor)
            to_partner = graph.path("repo:repo-cycle", partner)
        # Exactly one synthetic descent edge, onto the representative anchor.
        self.assertEqual(
            [(e["from"], e["to"], e["type"]) for e in edges],
            [("repo:repo-cycle", anchor, "contains")],
        )
        self.assertEqual({n["id"] for n in ctx["neighbors"]}, {anchor})
        # The anchor is a direct descent target; its cycle partner is reached
        # one hop further, through the child-internal contains edge.
        self.assertEqual(
            [n["display_id"] for n in to_anchor["path"]],
            ["repo:repo-cycle", "repo-cycle::symbol:cycle-a"],
        )
        self.assertEqual(
            [n["display_id"] for n in to_partner["path"]],
            ["repo:repo-cycle", "repo-cycle::symbol:cycle-a", "repo-cycle::symbol:cycle-b"],
        )

    # Item 3b: descent over a root-less cycle is deterministic and picks the
    # lexicographic min regardless of node/edge declaration order (ADR 0012).
    def _wide_cycle_workspace(self) -> Path:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        repo = _init_repo(root / "repo-wide")
        ring = ["symbol:w-d", "symbol:w-c", "symbol:w-b", "symbol:w-a"]  # reversed
        _write_graph(repo, _child_payload(
            {n: {"type": "symbol", "label": n, "props": {}} for n in ring},
            [
                {"from": ring[i], "to": ring[(i + 1) % len(ring)],
                 "type": "contains", "props": {}}
                for i in range(len(ring))
            ],
        ))
        _write_workspaces(root, [ChildEntry(name="repo-wide", path="repo-wide")])
        _write_root_graph(root, ["repo-wide"])
        return root

    def test_cycle_descent_is_deterministic_and_min_representative(self) -> None:
        root = self._wide_cycle_workspace()
        anchor = prefix_node_id("repo-wide", "symbol:w-a")
        with FederatedGraph(root) as graph:
            first = descent_edges_for(graph, "repo:repo-wide")
            second = descent_edges_for(graph, "repo:repo-wide")
            ctx1 = graph.context("repo:repo-wide")
            ctx2 = graph.context("repo:repo-wide")
        # Two runs -> byte-identical descent edges (no set/hash-order leakage).
        self.assertEqual(first, second)
        self.assertEqual(ctx1["edges"], ctx2["edges"])
        self.assertEqual(ctx1["neighbors"], ctx2["neighbors"])
        # The anchor is the lexicographic min of the ring, not the first
        # declared (``symbol:w-d``); one edge only, onto that representative.
        self.assertEqual([e["to"] for e in first], [anchor])

    # Item 3c: an acyclic root and an independent root-less cycle in one child;
    # both get an anchor, existing roots unchanged (containment-root invariant).
    def _mixed_root_and_cycle_workspace(self) -> Path:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        repo = _init_repo(root / "repo-mixed")
        _write_graph(repo, _child_payload(
            {
                "file:src/root.py": {"type": "file", "label": "r", "props": {}},
                "symbol:leaf": {"type": "symbol", "label": "l", "props": {}},
                "symbol:cyc-a": {"type": "symbol", "label": "a", "props": {}},
                "symbol:cyc-b": {"type": "symbol", "label": "b", "props": {}},
            },
            [
                {"from": "file:src/root.py", "to": "symbol:leaf", "type": "contains", "props": {}},
                {"from": "symbol:cyc-a", "to": "symbol:cyc-b", "type": "contains", "props": {}},
                {"from": "symbol:cyc-b", "to": "symbol:cyc-a", "type": "contains", "props": {}},
            ],
        ))
        _write_workspaces(root, [ChildEntry(name="repo-mixed", path="repo-mixed")])
        _write_root_graph(root, ["repo-mixed"])
        return root

    def test_acyclic_root_and_cycle_component_both_anchor(self) -> None:
        root = self._mixed_root_and_cycle_workspace()
        acyclic_root = prefix_node_id("repo-mixed", "file:src/root.py")
        leaf = prefix_node_id("repo-mixed", "symbol:leaf")
        cyc_anchor = prefix_node_id("repo-mixed", "symbol:cyc-a")
        cyc_partner = prefix_node_id("repo-mixed", "symbol:cyc-b")
        with FederatedGraph(root) as graph:
            child = graph._load_child("repo-mixed")
            # Acyclic root anchors exactly as before; cycle adds one min-id rep.
            # The contained leaf is NOT a direct anchor (reached through its root).
            self.assertEqual(
                child_containment_roots(child), ["file:src/root.py", "symbol:cyc-a"],
            )
            ctx = graph.context("repo:repo-mixed")
            to_leaf = graph.path("repo:repo-mixed", leaf)
            to_partner = graph.path("repo:repo-mixed", cyc_partner)
        self.assertEqual({n["id"] for n in ctx["neighbors"]}, {acyclic_root, cyc_anchor})
        # The acyclic subtree reaches its leaf through the root, as before.
        self.assertEqual(
            [n["display_id"] for n in to_leaf["path"]],
            ["repo:repo-mixed", "repo-mixed::file:src/root.py", "repo-mixed::symbol:leaf"],
        )
        # The cycle partner is reached through the cycle representative.
        self.assertEqual(
            [n["display_id"] for n in to_partner["path"]],
            ["repo:repo-mixed", "repo-mixed::symbol:cyc-a", "repo-mixed::symbol:cyc-b"],
        )


if __name__ == "__main__":
    unittest.main()
