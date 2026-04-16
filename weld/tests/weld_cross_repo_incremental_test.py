"""Tests for incremental cross-repo resolver orchestration.

Covers :func:`run_resolvers_incremental` -- skips resolvers for stable
children, re-runs for drifted children, carries forward prior edges,
drops edges referencing removed children, and produces output equivalent
to a full resolve pass.

Drift detection tests live in ``weld_drift_detect_test.py``.
"""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr

from weld.cross_repo.base import (
    CrossRepoEdge,
    CrossRepoResolver,
    ResolverContext,
    register_resolver,
    resolver_names,
    run_resolvers,
)
from weld.cross_repo.incremental import DriftResult, run_resolvers_incremental
from weld.workspace import UNIT_SEPARATOR


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeGraph:
    """Minimal stand-in for weld.graph.Graph in unit tests."""

    def __init__(self, data: dict) -> None:
        self._data = data

    def dump(self) -> dict:
        return dict(self._data)

    def nodes(self) -> dict[str, dict]:
        return dict(self._data.get("nodes", {}))


def _ctx(
    strategies: list[str],
    children: dict[str, tuple[object, bytes]],
) -> ResolverContext:
    return ResolverContext(
        workspace_root="/tmp/workspace",
        cross_repo_strategies=strategies,
        children={n: g for n, (g, _) in children.items()},
        child_hashes={n: ResolverContext.hash_bytes(b) for n, (_, b) in children.items()},
    )


def _pref(child: str, nid: str) -> str:
    return f"{child}{UNIT_SEPARATOR}{nid}"


def _graph(nodes: dict | None = None) -> _FakeGraph:
    return _FakeGraph({"nodes": nodes or {"n1": {"type": "mod"}}, "edges": []})


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class IncrementalResolverTests(unittest.TestCase):
    """Incremental resolver orchestration with drift-aware skipping."""

    def setUp(self) -> None:
        self._snapshot = set(resolver_names())

    def tearDown(self) -> None:
        current = set(resolver_names())
        for name in current - self._snapshot:
            from weld.cross_repo import base as base_mod
            base_mod._REGISTRY.pop(name, None)

    def _register_counter(self, name: str, log: list[str]):
        """Register a resolver that logs calls and emits cross-child edges."""
        captured = name
        cls = type(
            f"_Counter_{name}", (CrossRepoResolver,),
            {"name": name,
             "resolve": lambda s, c: self._emit(captured, log, c)},
        )
        register_resolver(name)(cls)
        return cls

    @staticmethod
    def _emit(name: str, log: list[str], ctx: ResolverContext) -> list[CrossRepoEdge]:
        log.append(name)
        edges = []
        kids = sorted(ctx.children)
        for i, a in enumerate(kids):
            for b in kids[i + 1:]:
                edges.append(CrossRepoEdge(
                    from_id=_pref(a, "n1"), to_id=_pref(b, "n1"),
                    type="cross_link", props={"resolver": name},
                ))
        return edges

    def test_no_prior_edges_forces_full_resolve(self) -> None:
        log: list[str] = []
        self._register_counter("inc_full", log)
        ctx = _ctx(["inc_full"], {"a": (_graph(), b'a'), "b": (_graph(), b'b')})
        drift = DriftResult(drifted=set(), stable={"a", "b"}, added=set(), removed=set())
        edges, status = run_resolvers_incremental(ctx, drift=drift, prior_edges=None)
        self.assertEqual(len(log), 1)
        self.assertEqual(len(edges), 1)
        self.assertEqual(status["a"], "resolved")

    def test_no_drift_skips_all_resolvers(self) -> None:
        log: list[str] = []
        self._register_counter("inc_skip", log)
        ctx = _ctx(["inc_skip"], {"a": (_graph(), b'a'), "b": (_graph(), b'b')})
        prior = [CrossRepoEdge(from_id=_pref("a", "n1"), to_id=_pref("b", "n1"), type="x")]
        drift = DriftResult(drifted=set(), stable={"a", "b"}, added=set(), removed=set())
        edges, status = run_resolvers_incremental(ctx, drift=drift, prior_edges=prior)
        self.assertEqual(log, [])
        self.assertEqual(len(edges), 1)
        self.assertEqual(status["a"], "skipped")
        self.assertEqual(status["b"], "skipped")

    def test_single_child_drift_reruns_resolvers(self) -> None:
        log: list[str] = []
        self._register_counter("inc_drift", log)
        ctx = _ctx(["inc_drift"], {"a": (_graph(), b'a'), "b": (_graph(), b'b')})
        prior = [CrossRepoEdge(from_id=_pref("a", "n1"), to_id=_pref("b", "n1"), type="x")]
        drift = DriftResult(drifted={"b"}, stable={"a"}, added=set(), removed=set())
        buf = io.StringIO()
        with redirect_stderr(buf):
            edges, status = run_resolvers_incremental(ctx, drift=drift, prior_edges=prior)
        self.assertEqual(len(log), 1)
        self.assertEqual(status["b"], "resolved")
        self.assertEqual(status["a"], "skipped")
        self.assertIn("drifted", buf.getvalue())
        self.assertIn("unchanged", buf.getvalue())

    def test_equivalence_with_full_resolve(self) -> None:
        """Incremental output matches full resolve on the same state."""
        log: list[str] = []
        self._register_counter("inc_eq", log)
        ctx = _ctx(["inc_eq"], {"a": (_graph(), b'a'), "b": (_graph(), b'b')})
        full = sorted([e.to_dict() for e in run_resolvers(ctx)],
                      key=lambda d: (d["from"], d["to"], d["type"]))
        prior = [CrossRepoEdge(
            from_id=_pref("a", "n1"), to_id=_pref("b", "n1"),
            type="cross_link", props={"resolver": "inc_eq"},
        )]
        drift = DriftResult(drifted={"b"}, stable={"a"}, added=set(), removed=set())
        buf = io.StringIO()
        with redirect_stderr(buf):
            inc, _ = run_resolvers_incremental(ctx, drift=drift, prior_edges=prior)
        inc_sorted = sorted([e.to_dict() for e in inc],
                            key=lambda d: (d["from"], d["to"], d["type"]))
        self.assertEqual(full, inc_sorted)

    def test_removed_child_drops_edges(self) -> None:
        log: list[str] = []
        self._register_counter("inc_rm", log)
        ctx = _ctx(["inc_rm"], {"a": (_graph(), b'a')})
        prior = [CrossRepoEdge(from_id=_pref("a", "n1"), to_id=_pref("b", "n1"), type="x")]
        drift = DriftResult(drifted=set(), stable={"a"}, added=set(), removed={"b"})
        buf = io.StringIO()
        with redirect_stderr(buf):
            edges, _ = run_resolvers_incremental(ctx, drift=drift, prior_edges=prior)
        self.assertEqual(edges, [])

    def test_two_children_drift_simultaneously(self) -> None:
        log: list[str] = []
        self._register_counter("inc_multi", log)
        ctx = _ctx(["inc_multi"], {
            "a": (_graph(), b'a'), "b": (_graph(), b'b'), "c": (_graph(), b'c'),
        })
        drift = DriftResult(drifted={"a", "b"}, stable={"c"}, added=set(), removed=set())
        buf = io.StringIO()
        with redirect_stderr(buf):
            edges, status = run_resolvers_incremental(ctx, drift=drift, prior_edges=[])
        self.assertEqual(status["a"], "resolved")
        self.assertEqual(status["b"], "resolved")
        self.assertEqual(status["c"], "skipped")
        self.assertEqual(len(edges), 3)

    def test_added_child_triggers_resolve(self) -> None:
        log: list[str] = []
        self._register_counter("inc_add", log)
        ctx = _ctx(["inc_add"], {"a": (_graph(), b'a'), "b": (_graph(), b'b')})
        drift = DriftResult(drifted=set(), stable={"a"}, added={"b"}, removed=set())
        buf = io.StringIO()
        with redirect_stderr(buf):
            edges, status = run_resolvers_incremental(ctx, drift=drift, prior_edges=[])
        self.assertEqual(status["b"], "resolved")
        self.assertEqual(len(edges), 1)

    def test_stable_children_produce_skipped_log(self) -> None:
        log: list[str] = []
        self._register_counter("inc_stbl", log)
        ctx = _ctx(["inc_stbl"], {"a": (_graph(), b'a'), "b": (_graph(), b'b')})
        prior = [CrossRepoEdge(from_id=_pref("a", "n1"), to_id=_pref("b", "n1"), type="x")]
        drift = DriftResult(drifted=set(), stable={"a", "b"}, added=set(), removed=set())
        edges, status = run_resolvers_incremental(ctx, drift=drift, prior_edges=prior)
        self.assertEqual(log, [])
        self.assertEqual(status["a"], "skipped")
        self.assertEqual(status["b"], "skipped")
        self.assertEqual(len(edges), 1)

    def test_undo_drift_removes_previously_emitted_edge(self) -> None:
        """Reverting a child drops edges the prior state produced."""
        log: list[str] = []

        @register_resolver("inc_undo")
        class _Cond(CrossRepoResolver):
            name = "inc_undo"
            def resolve(self, context: ResolverContext) -> list[CrossRepoEdge]:
                log.append("inc_undo")
                cb = context.children.get("b")
                if cb and hasattr(cb, "nodes") and "marker" in cb.nodes():
                    return [CrossRepoEdge(
                        from_id=_pref("a", "n1"), to_id=_pref("b", "marker"),
                        type="conditional",
                    )]
                return []

        ga = _graph()
        gb_mark = _FakeGraph({"nodes": {"n1": {"type": "mod"}, "marker": {"type": "f"}}, "edges": []})
        ctx1 = _ctx(["inc_undo"], {"a": (ga, b'a'), "b": (gb_mark, b'b2')})
        edges1 = run_resolvers(ctx1)
        self.assertEqual(len(edges1), 1)

        gb_clean = _graph()
        ctx2 = _ctx(["inc_undo"], {"a": (ga, b'a'), "b": (gb_clean, b'b1')})
        drift = DriftResult(drifted={"b"}, stable={"a"}, added=set(), removed=set())
        buf = io.StringIO()
        with redirect_stderr(buf):
            edges2, status = run_resolvers_incremental(ctx2, drift=drift, prior_edges=edges1)
        self.assertEqual(edges2, [])
        self.assertEqual(status["b"], "resolved")


if __name__ == "__main__":
    unittest.main()
