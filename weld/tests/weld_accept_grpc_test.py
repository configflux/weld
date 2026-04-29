"""Acceptance tests for gRPC interaction graph extraction (tracked project).

Exercises the grpc_proto (declarations) and grpc_bindings (server +
client linking) strategies against the ``grpc_accept`` fixture and
verifies the resulting interaction graph: services, rpc methods,
message contracts, enums, and server/client binding edges.

Per ADR 0018, proto declarations are canonical (confidence=definite)
while bindings are inferred (confidence=inferred).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.contract import validate_fragment  # noqa: E402
from weld.strategies.grpc_bindings import extract as bindings_extract  # noqa: E402
from weld.strategies.grpc_proto import extract as proto_extract  # noqa: E402

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "grpc_accept"

class GrpcProtoAcceptanceTest(unittest.TestCase):
    """Proto declarations produce rpc, contract, and enum nodes."""

    def setUp(self) -> None:
        result = proto_extract(
            _FIXTURE, {"glob": "proto/**/*.proto"}, {}
        )
        self.nodes = result.nodes
        self.edges = result.edges

    # -- rpc nodes -----------------------------------------------------------

    def test_all_three_rpcs_extracted(self) -> None:
        rpcs = {
            nid for nid, n in self.nodes.items() if n["type"] == "rpc"
        }
        self.assertIn("rpc:grpc:orders.v1.OrderService.PlaceOrder", rpcs)
        self.assertIn("rpc:grpc:orders.v1.OrderService.GetOrder", rpcs)
        self.assertIn("rpc:grpc:orders.v1.OrderService.WatchOrders", rpcs)

    def test_unary_rpcs_tagged_request_response(self) -> None:
        for method in ("PlaceOrder", "GetOrder"):
            nid = f"rpc:grpc:orders.v1.OrderService.{method}"
            self.assertEqual(
                self.nodes[nid]["props"]["surface_kind"],
                "request_response",
                f"{method} should be request_response",
            )

    def test_streaming_rpc_tagged_stream(self) -> None:
        nid = "rpc:grpc:orders.v1.OrderService.WatchOrders"
        self.assertEqual(
            self.nodes[nid]["props"]["surface_kind"], "stream"
        )

    def test_rpc_protocol_metadata(self) -> None:
        for nid, node in self.nodes.items():
            if node["type"] != "rpc":
                continue
            props = node["props"]
            self.assertEqual(props["protocol"], "grpc", f"{nid}")
            self.assertEqual(props["transport"], "http2", f"{nid}")
            self.assertEqual(props["boundary_kind"], "inbound", f"{nid}")
            self.assertEqual(props["confidence"], "definite", f"{nid}")

    # -- contract and enum nodes ---------------------------------------------

    def test_message_contracts_extracted(self) -> None:
        contracts = {
            nid
            for nid, n in self.nodes.items()
            if n["type"] == "contract"
        }
        for name in (
            "PlaceOrderRequest",
            "PlaceOrderResponse",
            "GetOrderRequest",
            "WatchOrdersRequest",
            "Order",
            "OrderItem",
            "OrderEvent",
        ):
            fqn = f"contract:grpc:orders.v1.{name}"
            self.assertIn(fqn, contracts, f"missing contract: {name}")

    def test_enum_extracted_with_members(self) -> None:
        nid = "enum:grpc:orders.v1.OrderStatus"
        self.assertIn(nid, self.nodes)
        members = self.nodes[nid]["props"]["members"]
        self.assertIn("ORDER_STATUS_PENDING", members)
        self.assertIn("ORDER_STATUS_SHIPPED", members)

    # -- edges ---------------------------------------------------------------

    def test_rpc_accepts_and_responds_with_edges(self) -> None:
        edge_keys = {
            (e["from"], e["to"], e["type"]) for e in self.edges
        }
        place = "rpc:grpc:orders.v1.OrderService.PlaceOrder"
        self.assertIn(
            (place, "contract:grpc:orders.v1.PlaceOrderRequest", "accepts"),
            edge_keys,
        )
        self.assertIn(
            (
                place,
                "contract:grpc:orders.v1.PlaceOrderResponse",
                "responds_with",
            ),
            edge_keys,
        )

    def test_file_contains_edges(self) -> None:
        file_id = "file:proto/orders/v1/orders.proto"
        contains = [
            e["to"]
            for e in self.edges
            if e["from"] == file_id and e["type"] == "contains"
        ]
        self.assertGreaterEqual(len(contains), 7)

    # -- fragment validation -------------------------------------------------

    def test_proto_fragment_validates(self) -> None:
        errs = validate_fragment(
            {"nodes": self.nodes, "edges": self.edges},
            source_label="strategy:grpc_proto",
            allow_dangling_edges=True,
        )
        self.assertEqual(errs, [], f"validation errors: {errs}")

class GrpcBindingsAcceptanceTest(unittest.TestCase):
    """Server servicer and client stub produce binding edges."""

    def setUp(self) -> None:
        result = bindings_extract(
            _FIXTURE,
            {"glob": "src/**/*.py", "proto_glob": "proto/**/*.proto"},
            {},
        )
        self.nodes = result.nodes
        self.edges = result.edges

    def test_server_implements_edges(self) -> None:
        impl_edges = [
            e for e in self.edges if e["type"] == "implements"
        ]
        targets = {e["to"] for e in impl_edges}
        self.assertIn(
            "rpc:grpc:orders.v1.OrderService.PlaceOrder", targets
        )
        self.assertIn(
            "rpc:grpc:orders.v1.OrderService.GetOrder", targets
        )
        self.assertIn(
            "rpc:grpc:orders.v1.OrderService.WatchOrders", targets
        )

    def test_client_invokes_edges(self) -> None:
        client_invokes = [
            e
            for e in self.edges
            if e["type"] == "invokes"
            and e["from"] == "file:src/client/order_caller.py"
        ]
        targets = {e["to"] for e in client_invokes}
        self.assertIn(
            "rpc:grpc:orders.v1.OrderService.PlaceOrder", targets
        )
        self.assertIn(
            "rpc:grpc:orders.v1.OrderService.WatchOrders", targets
        )

    def test_binding_edges_are_inferred_confidence(self) -> None:
        for e in self.edges:
            self.assertEqual(
                e["props"]["confidence"],
                "inferred",
                f"binding edge confidence: {e}",
            )

    def test_bindings_fragment_validates(self) -> None:
        errs = validate_fragment(
            {"nodes": self.nodes, "edges": self.edges},
            source_label="strategy:grpc_bindings",
            allow_dangling_edges=True,
        )
        self.assertEqual(errs, [], f"validation errors: {errs}")

if __name__ == "__main__":
    unittest.main()
