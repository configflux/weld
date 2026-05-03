"""Tests for the grpc_service_binding cross-repo resolver.

Covers the acceptance criteria from the task description:

* A fixture where repo-a contains gRPC client stubs and repo-b contains
  gRPC proto service definitions emits a ``cross_repo:grpc_calls`` edge.
* The emitted edge carries the matched service name in props.
* Running resolve twice on the same fixture produces identical edges
  (deterministic edge IDs and props).
* A stub that matches no sibling's service produces no edge and no error.
* A child with ``missing`` or ``uninitialized`` status is skipped
  (framework-level guarantee -- only ``present`` children appear in
  ``context.children``).
* Renaming the service in the definition child causes the previously
  matched edge to disappear.
* Multiple services across multiple children.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

from weld.cross_repo.base import (
    CrossRepoEdge,
    resolver_names,
    run_resolvers,
)

# Import for registration side effect.
import weld.cross_repo.grpc_service_binding as _grpc_mod  # noqa: F401

_tests_dir = str(Path(__file__).resolve().parent)
if _tests_dir not in sys.path:
    sys.path.insert(0, _tests_dir)

from cross_repo_grpc_fixtures import FakeGraph as _FakeGraph  # noqa: E402
from cross_repo_grpc_fixtures import UNIT_SEP  # noqa: E402
from cross_repo_grpc_fixtures import client_stub_graph as _client_stub_graph  # noqa: E402
from cross_repo_grpc_fixtures import make_context as _make_context  # noqa: E402
from cross_repo_grpc_fixtures import proto_service_graph as _proto_service_graph  # noqa: E402


class GrpcServiceBindingRegistrationTests(unittest.TestCase):
    """Verify the resolver registers under the expected name."""

    def test_registered_name(self) -> None:
        self.assertIn("grpc_service_binding", resolver_names())


class GrpcServiceBindingMatchTests(unittest.TestCase):
    """Core matching: stubs in one child resolve to defs in another."""

    def test_basic_match_emits_cross_repo_edge(self) -> None:
        """AC: repo-a stubs + repo-b defs -> invokes edge."""
        repo_a = _client_stub_graph("UserService", ["GetUser"], package="users.v1")
        repo_b = _proto_service_graph("UserService", ["GetUser"], package="users.v1")
        ctx = _make_context(children={"repo-a": repo_a, "repo-b": repo_b})
        edges = run_resolvers(ctx)

        self.assertEqual(len(edges), 1)
        edge = edges[0]
        self.assertEqual(edge.type, "cross_repo:grpc_calls")
        # ADR 0041 § Layer 1: file ids drop ext; rpc ids lowercase.
        self.assertEqual(
            edge.from_id,
            f"repo-a{UNIT_SEP}file:client",
        )
        self.assertEqual(
            edge.to_id,
            f"repo-b{UNIT_SEP}rpc:grpc:users.v1.userservice.getuser",
        )
        # Service label preserves the lowercased slug parsed from the
        # rpc id (ADR 0041 § Layer 1); method follows the same rule.
        self.assertEqual(edge.props["service"], "users.v1.userservice")
        self.assertEqual(edge.props["method"], "getuser")

    def test_edge_props_contain_service_name(self) -> None:
        """AC: edge props include the matched service name."""
        repo_a = _client_stub_graph("Catalog", ["ListItems"], package="catalog.v1")
        repo_b = _proto_service_graph("Catalog", ["ListItems"], package="catalog.v1")
        ctx = _make_context(children={"repo-a": repo_a, "repo-b": repo_b})
        edges = run_resolvers(ctx)

        self.assertEqual(len(edges), 1)
        self.assertIn("service", edges[0].props)
        # ADR 0041 § Layer 1: service label lowercases through canonical slug.
        self.assertEqual(edges[0].props["service"], "catalog.v1.catalog")

    def test_multiple_methods_emit_multiple_edges(self) -> None:
        """Each matched method gets its own edge."""
        repo_a = _client_stub_graph(
            "OrderService", ["CreateOrder", "GetOrder"], package="orders.v1"
        )
        repo_b = _proto_service_graph(
            "OrderService", ["CreateOrder", "GetOrder", "DeleteOrder"],
            package="orders.v1",
        )
        ctx = _make_context(children={"repo-a": repo_a, "repo-b": repo_b})
        edges = run_resolvers(ctx)

        # Client calls CreateOrder and GetOrder (not DeleteOrder).
        self.assertEqual(len(edges), 2)
        methods = {e.props["method"] for e in edges}
        # Method label lowercases through canonical slug (ADR 0041).
        self.assertEqual(methods, {"createorder", "getorder"})


class DeterminismTests(unittest.TestCase):
    """AC: running resolve twice produces byte-identical edges."""

    def test_deterministic_output(self) -> None:
        repo_a = _client_stub_graph(
            "UserService", ["GetUser", "ListUsers"], package="users.v1"
        )
        repo_b = _proto_service_graph(
            "UserService", ["GetUser", "ListUsers"], package="users.v1"
        )

        def _run() -> list[dict]:
            ctx = _make_context(children={"repo-a": repo_a, "repo-b": repo_b})
            edges = run_resolvers(ctx)
            return [e.to_dict() for e in edges]

        first = _run()
        second = _run()
        # Compare JSON-serialized form for byte-identical check.
        self.assertEqual(
            json.dumps(first, sort_keys=True),
            json.dumps(second, sort_keys=True),
        )

    def test_child_iteration_order_does_not_affect_output(self) -> None:
        """Children iterated in lexicographic order regardless of dict insertion."""
        repo_a = _client_stub_graph("Svc", ["Do"], package="p")
        repo_b = _proto_service_graph("Svc", ["Do"], package="p")

        # Insert children in different orders.
        ctx1 = _make_context(children={"alpha": repo_a, "beta": repo_b})
        ctx2 = _make_context(children={"beta": repo_b, "alpha": repo_a})

        edges1 = [e.to_dict() for e in run_resolvers(ctx1)]
        edges2 = [e.to_dict() for e in run_resolvers(ctx2)]
        self.assertEqual(edges1, edges2)


class UnmatchedStubTests(unittest.TestCase):
    """AC: unmatched stubs produce no edge and no error."""

    def test_stub_with_no_matching_definition(self) -> None:
        repo_a = _client_stub_graph("Nonexistent", ["Call"], package="x")
        repo_b = _proto_service_graph("OtherService", ["DoStuff"], package="y")
        ctx = _make_context(children={"repo-a": repo_a, "repo-b": repo_b})
        edges = run_resolvers(ctx)
        self.assertEqual(edges, [])

    def test_stub_matching_same_child_definition_produces_no_cross_edge(self) -> None:
        """Within-repo matches are not cross-repo; they should not emit edges."""
        # repo-a has both the proto definition and the client stub.
        nodes: dict[str, dict] = {}
        all_edges: list[dict] = []
        # Proto nodes (canonical id per ADR 0041 § Layer 1).
        rpc_id = "rpc:grpc:users.v1.userservice.getuser"
        nodes[rpc_id] = {
            "type": "rpc",
            "label": "UserService.GetUser",
            "props": {
                "service": "users.v1.UserService",
                "method": "GetUser",
                "source_strategy": "grpc_proto",
                "protocol": "grpc",
            },
        }
        # Client edges
        all_edges.append({
            "from": "file:client",
            "to": rpc_id,
            "type": "invokes",
            "props": {"source_strategy": "grpc_bindings", "confidence": "inferred"},
        })
        repo_a = _FakeGraph(nodes=nodes, edges=all_edges)
        ctx = _make_context(children={"repo-a": repo_a})
        edges = run_resolvers(ctx)
        # Same-child matching should produce no cross-repo edges.
        self.assertEqual(edges, [])

    def test_empty_children_produce_no_edges(self) -> None:
        ctx = _make_context(children={})
        edges = run_resolvers(ctx)
        self.assertEqual(edges, [])


class MissingChildTests(unittest.TestCase):
    """AC: missing/uninitialized children are skipped without crash.

    The framework only passes ``present`` children in ``context.children``.
    Simulating a missing child means it simply is absent from the dict.
    """

    def test_missing_child_skipped(self) -> None:
        # Only repo-a is present; repo-b (with the proto defs) is missing.
        repo_a = _client_stub_graph("UserService", ["GetUser"], package="users.v1")
        ctx = _make_context(children={"repo-a": repo_a})
        edges = run_resolvers(ctx)
        # No definition child present, so no edges.
        self.assertEqual(edges, [])


class ServiceRenameTests(unittest.TestCase):
    """AC: renaming the service causes previously emitted edges to vanish."""

    def test_renamed_service_drops_edge(self) -> None:
        repo_a = _client_stub_graph("UserService", ["GetUser"], package="users.v1")

        # Before rename: UserService is declared.
        repo_b_before = _proto_service_graph(
            "UserService", ["GetUser"], package="users.v1"
        )
        ctx1 = _make_context(children={"repo-a": repo_a, "repo-b": repo_b_before})
        edges_before = run_resolvers(ctx1)
        self.assertEqual(len(edges_before), 1)

        # After rename: UserService -> OrderService.
        repo_b_after = _proto_service_graph(
            "OrderService", ["GetUser"], package="users.v1"
        )
        ctx2 = _make_context(children={"repo-a": repo_a, "repo-b": repo_b_after})
        edges_after = run_resolvers(ctx2)
        self.assertEqual(edges_after, [])


class MultiChildTests(unittest.TestCase):
    """Multiple services spread across multiple children."""

    def test_three_children_two_services(self) -> None:
        """client calls services from two different definition repos."""
        client = _client_stub_graph(
            "UserService", ["GetUser"], package="users.v1",
            source_file="user_client.py",
        )
        # Add more client edges for the other service (canonical ids).
        order_rpc_id = "rpc:grpc:orders.v1.orderservice.createorder"
        client._data["edges"].append({
            "from": "file:order_client",
            "to": order_rpc_id,
            "type": "invokes",
            "props": {"source_strategy": "grpc_bindings", "confidence": "inferred"},
        })

        user_defs = _proto_service_graph(
            "UserService", ["GetUser"], package="users.v1"
        )
        order_defs = _proto_service_graph(
            "OrderService", ["CreateOrder"], package="orders.v1"
        )

        ctx = _make_context(
            children={
                "client-repo": client,
                "user-service": user_defs,
                "order-service": order_defs,
            }
        )
        edges = run_resolvers(ctx)
        self.assertEqual(len(edges), 2)

        edge_targets = {e.to_id for e in edges}
        self.assertIn(
            f"user-service{UNIT_SEP}rpc:grpc:users.v1.userservice.getuser",
            edge_targets,
        )
        self.assertIn(
            f"order-service{UNIT_SEP}rpc:grpc:orders.v1.orderservice.createorder",
            edge_targets,
        )

    def test_bidirectional_services(self) -> None:
        """Two repos that are both client and server to each other."""
        # repo-a: defines OrderService, calls UserService (canonical ids
        # per ADR 0041 § Layer 1).
        repo_a_nodes = {
            "rpc:grpc:orders.v1.orderservice.createorder": {
                "type": "rpc",
                "label": "OrderService.CreateOrder",
                "props": {
                    "service": "orders.v1.OrderService",
                    "method": "CreateOrder",
                    "source_strategy": "grpc_proto",
                    "protocol": "grpc",
                },
            }
        }
        repo_a_edges = [
            {
                "from": "file:user_client",
                "to": "rpc:grpc:users.v1.userservice.getuser",
                "type": "invokes",
                "props": {"source_strategy": "grpc_bindings", "confidence": "inferred"},
            }
        ]
        repo_a = _FakeGraph(nodes=repo_a_nodes, edges=repo_a_edges)

        # repo-b: defines UserService, calls OrderService
        repo_b_nodes = {
            "rpc:grpc:users.v1.userservice.getuser": {
                "type": "rpc",
                "label": "UserService.GetUser",
                "props": {
                    "service": "users.v1.UserService",
                    "method": "GetUser",
                    "source_strategy": "grpc_proto",
                    "protocol": "grpc",
                },
            }
        }
        repo_b_edges = [
            {
                "from": "file:order_client",
                "to": "rpc:grpc:orders.v1.orderservice.createorder",
                "type": "invokes",
                "props": {"source_strategy": "grpc_bindings", "confidence": "inferred"},
            }
        ]
        repo_b = _FakeGraph(nodes=repo_b_nodes, edges=repo_b_edges)

        ctx = _make_context(children={"repo-a": repo_a, "repo-b": repo_b})
        edges = run_resolvers(ctx)

        self.assertEqual(len(edges), 2)
        # repo-a calls UserService in repo-b
        ab_edges = [e for e in edges if e.from_id.startswith("repo-a")]
        self.assertEqual(len(ab_edges), 1)
        self.assertEqual(
            ab_edges[0].to_id,
            f"repo-b{UNIT_SEP}rpc:grpc:users.v1.userservice.getuser",
        )
        # repo-b calls OrderService in repo-a
        ba_edges = [e for e in edges if e.from_id.startswith("repo-b")]
        self.assertEqual(len(ba_edges), 1)
        self.assertEqual(
            ba_edges[0].to_id,
            f"repo-a{UNIT_SEP}rpc:grpc:orders.v1.orderservice.createorder",
        )


class EdgeContractTests(unittest.TestCase):
    """Verify the shape and contract of emitted edges."""

    def test_edge_type_is_cross_repo_grpc_calls(self) -> None:
        repo_a = _client_stub_graph("Svc", ["Method"], package="pkg")
        repo_b = _proto_service_graph("Svc", ["Method"], package="pkg")
        ctx = _make_context(children={"repo-a": repo_a, "repo-b": repo_b})
        edges = run_resolvers(ctx)
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0].type, "cross_repo:grpc_calls")

    def test_edge_is_frozen(self) -> None:
        repo_a = _client_stub_graph("Svc", ["Method"], package="pkg")
        repo_b = _proto_service_graph("Svc", ["Method"], package="pkg")
        ctx = _make_context(children={"repo-a": repo_a, "repo-b": repo_b})
        edges = run_resolvers(ctx)
        self.assertEqual(len(edges), 1)
        with self.assertRaises(AttributeError):
            edges[0].from_id = "tampered"  # type: ignore[misc]

    def test_to_dict_round_trips(self) -> None:
        repo_a = _client_stub_graph("Svc", ["Method"], package="pkg")
        repo_b = _proto_service_graph("Svc", ["Method"], package="pkg")
        ctx = _make_context(children={"repo-a": repo_a, "repo-b": repo_b})
        edges = run_resolvers(ctx)
        self.assertEqual(len(edges), 1)
        d = edges[0].to_dict()
        reconstructed = CrossRepoEdge.from_mapping(d)
        self.assertEqual(reconstructed.from_id, edges[0].from_id)
        self.assertEqual(reconstructed.to_id, edges[0].to_id)
        self.assertEqual(reconstructed.type, edges[0].type)
        self.assertEqual(dict(reconstructed.props), dict(edges[0].props))


class NoResolverRegisteredTests(unittest.TestCase):
    """When grpc_service_binding is not in cross_repo_strategies, no gRPC edges."""

    def test_absent_strategy_produces_no_edges(self) -> None:
        repo_a = _client_stub_graph("Svc", ["Method"], package="pkg")
        repo_b = _proto_service_graph("Svc", ["Method"], package="pkg")
        ctx = _make_context(
            strategies=[],  # grpc_service_binding not listed
            children={"repo-a": repo_a, "repo-b": repo_b},
        )
        edges = run_resolvers(ctx)
        self.assertEqual(edges, [])


if __name__ == "__main__":
    unittest.main()
