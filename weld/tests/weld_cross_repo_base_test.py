"""Tests for the cross-repo resolver base framework.

Covers the contract surface that all concrete resolvers plug into:

* :class:`CrossRepoEdge` shape (typed output).
* :class:`ResolverContext` helpers (config access, loaded child graphs,
  deterministic child hashing).
* :class:`CrossRepoResolver` abstract base and its ``name`` attribute.
* Registry (``register_resolver`` / ``get_resolver`` / ``resolver_names``).
* :class:`MalformedEdgeError` raised on invalid resolver output.
* :func:`run_resolvers` orchestrator: invocation order matches the
  YAML-declared strategy list, failing resolvers are caught and reported,
  child-byte hash re-check skips affected output, empty strategy list
  produces an empty edge set, and unknown strategy names raise a clear
  error.
"""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr
from dataclasses import FrozenInstanceError

from weld.cross_repo.base import (
    CrossRepoEdge,
    CrossRepoResolver,
    MalformedEdgeError,
    ResolverContext,
    UnknownResolverError,
    get_resolver,
    register_resolver,
    resolver_names,
    run_resolvers,
)


class _FakeGraph:
    """Minimal stand-in for :class:`weld.graph.Graph` used in unit tests."""

    def __init__(self, data: dict) -> None:
        self._data = data

    def dump(self) -> dict:
        return dict(self._data)


def _make_context(
    *,
    strategies: list[str] | None = None,
    children: dict[str, tuple[object, bytes]] | None = None,
) -> ResolverContext:
    children = children or {}
    loaded: dict[str, object] = {name: graph for name, (graph, _) in children.items()}
    hashes: dict[str, str] = {
        name: ResolverContext.hash_bytes(raw) for name, (_, raw) in children.items()
    }
    return ResolverContext(
        workspace_root="/tmp/workspace",
        cross_repo_strategies=list(strategies or []),
        children=loaded,
        child_hashes=hashes,
    )


class CrossRepoEdgeTests(unittest.TestCase):
    """Typed output contract for cross-repo edges."""

    def test_edge_is_frozen_dataclass(self) -> None:
        edge = CrossRepoEdge(
            from_id="a", to_id="b", type="invokes", props={"path": "/x"}
        )
        with self.assertRaises(FrozenInstanceError):
            edge.from_id = "c"  # type: ignore[misc]

    def test_edge_defaults_to_empty_props(self) -> None:
        edge = CrossRepoEdge(from_id="a", to_id="b", type="invokes")
        self.assertEqual(edge.props, {})

    def test_edge_to_dict_is_serializable_shape(self) -> None:
        edge = CrossRepoEdge(
            from_id="a\x1ff1", to_id="b\x1ff2", type="invokes", props={"path": "/x"}
        )
        payload = edge.to_dict()
        self.assertEqual(
            payload,
            {"from": "a\x1ff1", "to": "b\x1ff2", "type": "invokes", "props": {"path": "/x"}},
        )


class RegistryTests(unittest.TestCase):
    """Decorator-based registration and lookup."""

    def setUp(self) -> None:
        # Snapshot existing names so we can restore between tests.
        self._snapshot = set(resolver_names())

    def tearDown(self) -> None:
        current = set(resolver_names())
        for name in current - self._snapshot:
            # Teardown helper: pop anonymously registered test resolvers.
            from weld.cross_repo import base as base_mod

            base_mod._REGISTRY.pop(name, None)

    def test_register_and_lookup(self) -> None:
        @register_resolver("unit_test_stub")
        class _StubResolver(CrossRepoResolver):
            name = "unit_test_stub"

            def resolve(self, context: ResolverContext) -> list[CrossRepoEdge]:
                return []

        cls = get_resolver("unit_test_stub")
        self.assertIs(cls, _StubResolver)
        self.assertIn("unit_test_stub", resolver_names())

    def test_duplicate_name_rejected(self) -> None:
        @register_resolver("unit_test_dup")
        class _First(CrossRepoResolver):
            name = "unit_test_dup"

            def resolve(self, context: ResolverContext) -> list[CrossRepoEdge]:
                return []

        with self.assertRaises(ValueError):
            @register_resolver("unit_test_dup")
            class _Second(CrossRepoResolver):  # pragma: no cover - never constructed
                name = "unit_test_dup"

                def resolve(
                    self, context: ResolverContext
                ) -> list[CrossRepoEdge]:
                    return []

    def test_register_rejects_name_mismatch(self) -> None:
        with self.assertRaises(ValueError):
            @register_resolver("unit_test_mismatch")
            class _Bad(CrossRepoResolver):  # pragma: no cover - never constructed
                name = "something_else"

                def resolve(
                    self, context: ResolverContext
                ) -> list[CrossRepoEdge]:
                    return []

    def test_unknown_name_raises_typed_error(self) -> None:
        with self.assertRaises(UnknownResolverError) as exc:
            get_resolver("does_not_exist")
        self.assertIn("does_not_exist", str(exc.exception))


class ResolverContextTests(unittest.TestCase):
    """Context surface exposed to resolvers."""

    def test_children_exposes_loaded_graphs_readonly(self) -> None:
        graph = _FakeGraph({"nodes": [], "edges": []})
        ctx = _make_context(children={"svc-a": (graph, b'{"nodes":[]}')})
        self.assertEqual(list(ctx.children.keys()), ["svc-a"])
        # The mapping is a MappingProxyType so resolvers cannot mutate it.
        with self.assertRaises(TypeError):
            ctx.children["svc-b"] = graph  # type: ignore[index]

    def test_hash_bytes_is_deterministic(self) -> None:
        raw = b'{"nodes":[], "edges":[]}'
        self.assertEqual(
            ResolverContext.hash_bytes(raw), ResolverContext.hash_bytes(raw)
        )
        self.assertNotEqual(
            ResolverContext.hash_bytes(raw), ResolverContext.hash_bytes(raw + b" ")
        )

    def test_child_hash_lookup(self) -> None:
        graph = _FakeGraph({"nodes": [], "edges": []})
        raw = b'{"nodes":[]}'
        ctx = _make_context(children={"svc-a": (graph, raw)})
        self.assertEqual(ctx.child_hashes["svc-a"], ResolverContext.hash_bytes(raw))


class RunResolversOrchestrationTests(unittest.TestCase):
    """End-to-end behavior of :func:`run_resolvers`."""

    def setUp(self) -> None:
        self._snapshot = set(resolver_names())

    def tearDown(self) -> None:
        current = set(resolver_names())
        for name in current - self._snapshot:
            from weld.cross_repo import base as base_mod

            base_mod._REGISTRY.pop(name, None)

    def test_empty_strategy_list_returns_empty_edges(self) -> None:
        ctx = _make_context(strategies=[])
        edges = run_resolvers(ctx)
        self.assertEqual(edges, [])

    def test_runs_in_yaml_declared_order(self) -> None:
        call_order: list[str] = []

        @register_resolver("order_first")
        class _First(CrossRepoResolver):
            name = "order_first"

            def resolve(self, context: ResolverContext) -> list[CrossRepoEdge]:
                call_order.append(self.name)
                return [CrossRepoEdge(from_id="a", to_id="b", type="t1")]

        @register_resolver("order_second")
        class _Second(CrossRepoResolver):
            name = "order_second"

            def resolve(self, context: ResolverContext) -> list[CrossRepoEdge]:
                call_order.append(self.name)
                return [CrossRepoEdge(from_id="b", to_id="c", type="t2")]

        # Declare in reverse alphabetical order to prove filesystem/lexicographic
        # ordering is not what drives execution.
        ctx = _make_context(strategies=["order_second", "order_first"])
        edges = run_resolvers(ctx)
        self.assertEqual(call_order, ["order_second", "order_first"])
        self.assertEqual([e.type for e in edges], ["t2", "t1"])

    def test_failing_resolver_is_isolated(self) -> None:
        @register_resolver("stub_healthy")
        class _Healthy(CrossRepoResolver):
            name = "stub_healthy"

            def resolve(self, context: ResolverContext) -> list[CrossRepoEdge]:
                return [CrossRepoEdge(from_id="a", to_id="b", type="invokes")]

        @register_resolver("stub_exploding")
        class _Exploding(CrossRepoResolver):
            name = "stub_exploding"

            def resolve(self, context: ResolverContext) -> list[CrossRepoEdge]:
                raise RuntimeError("boom")

        ctx = _make_context(strategies=["stub_exploding", "stub_healthy"])
        buffer = io.StringIO()
        with redirect_stderr(buffer):
            edges = run_resolvers(ctx)
        # Healthy resolver output is preserved.
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0].type, "invokes")
        # Warning names the failing resolver.
        warning = buffer.getvalue()
        self.assertIn("stub_exploding", warning)

    def test_malformed_edge_raises_named_error(self) -> None:
        with self.assertRaises(MalformedEdgeError):
            CrossRepoEdge.from_mapping({"from": "a", "to": "b"})  # missing type

        with self.assertRaises(MalformedEdgeError):
            CrossRepoEdge.from_mapping(
                {"from": "a", "to": "b", "type": "invokes", "props": "not-a-dict"}
            )

    def test_unknown_strategy_raises(self) -> None:
        ctx = _make_context(strategies=["no_such_resolver"])
        with self.assertRaises(UnknownResolverError):
            run_resolvers(ctx)

    def test_hash_drift_skips_affected_child(self) -> None:
        graph = _FakeGraph({"nodes": [], "edges": []})
        raw = b'{"nodes":[]}'

        @register_resolver("stub_drift")
        class _Drifter(CrossRepoResolver):
            name = "stub_drift"

            def resolve(self, context: ResolverContext) -> list[CrossRepoEdge]:
                return [
                    CrossRepoEdge(
                        from_id="svc-a\x1fa", to_id="svc-b\x1fb", type="invokes"
                    )
                ]

        ctx = _make_context(
            strategies=["stub_drift"],
            children={"svc-a": (graph, raw), "svc-b": (graph, raw)},
        )
        # Replace the post-resolve hash lookup so we simulate a drift on svc-a.
        ctx_drifted = ResolverContext(
            workspace_root=ctx.workspace_root,
            cross_repo_strategies=list(ctx.cross_repo_strategies),
            children=dict(ctx.children),
            child_hashes=dict(ctx.child_hashes),
        )
        # Point to a different post-run hash for svc-a.
        post_run_hashes = {
            "svc-a": ResolverContext.hash_bytes(b"different"),
            "svc-b": ctx_drifted.child_hashes["svc-b"],
        }
        buffer = io.StringIO()
        with redirect_stderr(buffer):
            edges = run_resolvers(ctx_drifted, post_run_child_hashes=post_run_hashes)
        # Edge references svc-a which drifted, so output is dropped.
        self.assertEqual(edges, [])
        self.assertIn("svc-a", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
