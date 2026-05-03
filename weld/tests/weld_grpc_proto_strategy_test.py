"""Tests for the grpc_proto strategy (tracked project).

Extracts services, rpc methods, message contracts, and enums from
declared ``.proto`` files. Per ADR 0018's static-truth policy,
extraction is text-only: the strategy never invokes protoc and never
inspects runtime bindings. Emitted rpc nodes are stamped with
``protocol="grpc"``, ``transport="http2"``, ``boundary_kind="inbound"``,
``surface_kind`` of ``"request_response"`` or ``"stream"``, and are
linked back to their declaring file via an ``invokes`` edge plus
``accepts``/``responds_with`` edges to the proto-declared request and
response contracts. Message and enum nodes use ``contains`` edges.
"""

from __future__ import annotations

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.contract import validate_fragment  # noqa: E402
from weld.strategies.grpc_proto import extract  # noqa: E402

def _write(pkg: Path, name: str, body: str) -> None:
    (pkg / name).write_text(textwrap.dedent(body))

def _run(root: Path, glob: str = "proto/**/*.proto") -> tuple[dict, list, list]:
    result = extract(root, {"glob": glob}, {})
    return result.nodes, result.edges, list(result.discovered_from)

class GrpcProtoFixtureTest(unittest.TestCase):
    """End-to-end check against the repo's grpc_project fixture."""

    FIXTURE = Path(__file__).resolve().parent / "fixtures" / "grpc_project"

    def test_fixture_extracts_service_rpc_messages_and_enum(self) -> None:
        nodes, _edges, discovered = _run(self.FIXTURE)
        rpc_ids = {nid for nid, n in nodes.items() if n["type"] == "rpc"}
        contract_ids = {nid for nid, n in nodes.items() if n["type"] == "contract"}
        enum_ids = {nid for nid, n in nodes.items() if n["type"] == "enum"}
        # ADR 0041 § Layer 1: ids route through ``canonical_slug`` so
        # mixed-case service / contract / enum names lowercase.
        self.assertIn("rpc:grpc:catalog.v1.catalogservice.getproduct", rpc_ids)
        self.assertIn("rpc:grpc:catalog.v1.catalogservice.listproducts", rpc_ids)
        self.assertIn("contract:grpc:catalog.v1.getproductrequest", contract_ids)
        self.assertIn("contract:grpc:catalog.v1.getproductresponse", contract_ids)
        self.assertIn("contract:grpc:catalog.v1.product", contract_ids)
        self.assertIn("enum:grpc:catalog.v1.productstatus", enum_ids)
        self.assertIn("proto/catalog/v1/catalog.proto", discovered)

    def test_fixture_fragment_validates(self) -> None:
        nodes, edges, _ = _run(self.FIXTURE)
        errors = validate_fragment(
            {"nodes": nodes, "edges": edges},
            source_label="strategy:grpc_proto",
            allow_dangling_edges=True,
        )
        self.assertEqual(errors, [], f"unexpected validation errors: {errors}")

class GrpcProtoServiceTest(unittest.TestCase):
    """Service and rpc-method extraction."""

    def test_unary_rpc_emits_request_response_node(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "proto" / "billing" / "v1"
            pkg.mkdir(parents=True)
            _write(pkg, "billing.proto", """\
                syntax = "proto3";
                package billing.v1;
                service BillingService {
                  rpc Charge(ChargeRequest) returns (ChargeResponse);
                }
                message ChargeRequest { string user_id = 1; }
                message ChargeResponse { bool ok = 1; }
            """)
            nodes, _edges, _ = _run(root)
            rpc_id = "rpc:grpc:billing.v1.billingservice.charge"
            self.assertIn(rpc_id, nodes)
            props = nodes[rpc_id]["props"]
            self.assertEqual(nodes[rpc_id]["type"], "rpc")
            self.assertEqual(props["protocol"], "grpc")
            self.assertEqual(props["surface_kind"], "request_response")
            self.assertEqual(props["transport"], "http2")
            self.assertEqual(props["boundary_kind"], "inbound")
            self.assertEqual(props["declared_in"], "proto/billing/v1/billing.proto")
            self.assertEqual(props["service"], "billing.v1.BillingService")
            self.assertEqual(props["method"], "Charge")
            self.assertEqual(props["source_strategy"], "grpc_proto")
            self.assertEqual(props["authority"], "canonical")
            self.assertEqual(props["confidence"], "definite")
            # ADR 0041 § Layer 3: legacy mixed-case id preserved as alias.
            self.assertIn(
                "rpc:grpc:billing.v1.BillingService.Charge",
                props["aliases"],
            )

    def test_server_client_and_bidi_streams_tagged_as_stream(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "proto"
            pkg.mkdir(parents=True)
            _write(pkg, "s.proto", """\
                syntax = "proto3";
                package s;
                service S {
                  rpc Tail(TailRequest) returns (stream TailEvent);
                  rpc Upload(stream UploadChunk) returns (UploadResult);
                  rpc Chat(stream ChatMsg) returns (stream ChatMsg);
                }
                message TailRequest { string topic = 1; }
                message TailEvent { string payload = 1; }
                message UploadChunk { bytes data = 1; }
                message UploadResult { string id = 1; }
                message ChatMsg { string text = 1; }
            """)
            nodes, _, _ = _run(root)
            for method in ("tail", "upload", "chat"):
                self.assertEqual(
                    nodes[f"rpc:grpc:s.s.{method}"]["props"]["surface_kind"],
                    "stream",
                    f"expected {method} to be tagged as stream",
                )

    def test_multiple_services_are_all_extracted(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "proto"
            pkg.mkdir(parents=True)
            _write(pkg, "multi.proto", """\
                syntax = "proto3";
                package m;
                service A { rpc Ping(P) returns (P); }
                service B { rpc Pong(P) returns (P); }
                message P { int32 n = 1; }
            """)
            nodes, _, _ = _run(root)
            self.assertIn("rpc:grpc:m.a.ping", nodes)
            self.assertIn("rpc:grpc:m.b.pong", nodes)

    def test_file_without_package_uses_empty_namespace(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "proto"
            pkg.mkdir(parents=True)
            _write(pkg, "nopkg.proto", """\
                syntax = "proto3";
                service NoPkg { rpc Do(Req) returns (Resp); }
                message Req {}
                message Resp {}
            """)
            nodes, _, _ = _run(root)
            self.assertIn("rpc:grpc:nopkg.do", nodes)
            self.assertIn("contract:grpc:req", nodes)

class GrpcProtoMessageTest(unittest.TestCase):
    """Message contracts and enums from proto declarations."""

    def test_message_emits_contract_node_with_fields(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "proto"
            pkg.mkdir(parents=True)
            _write(pkg, "m.proto", """\
                syntax = "proto3";
                package m;
                message User {
                  string id = 1;
                  string email = 2;
                  int64 created_at = 3;
                }
            """)
            nodes, _, _ = _run(root)
            nid = "contract:grpc:m.user"
            self.assertIn(nid, nodes)
            props = nodes[nid]["props"]
            self.assertEqual(props["protocol"], "grpc")
            self.assertEqual(props["declared_in"], "proto/m.proto")
            self.assertEqual(props["fields"], ["id", "email", "created_at"])
            self.assertEqual(props["source_strategy"], "grpc_proto")

    def test_enum_emits_enum_node_with_members(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "proto"
            pkg.mkdir(parents=True)
            _write(pkg, "e.proto", """\
                syntax = "proto3";
                package e;
                enum Color {
                  COLOR_UNSPECIFIED = 0;
                  COLOR_RED = 1;
                  COLOR_BLUE = 2;
                }
            """)
            nodes, _, _ = _run(root)
            nid = "enum:grpc:e.color"
            self.assertIn(nid, nodes)
            self.assertEqual(
                nodes[nid]["props"]["members"],
                ["COLOR_UNSPECIFIED", "COLOR_RED", "COLOR_BLUE"],
            )
            self.assertEqual(nodes[nid]["props"]["declared_in"], "proto/e.proto")

    def test_nested_message_is_extracted_with_parent_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "proto"
            pkg.mkdir(parents=True)
            _write(pkg, "n.proto", """\
                syntax = "proto3";
                package n;
                message Outer {
                  string id = 1;
                  message Inner {
                    string value = 1;
                  }
                }
            """)
            nodes, _, _ = _run(root)
            self.assertIn("contract:grpc:n.outer", nodes)
            self.assertIn("contract:grpc:n.outer.inner", nodes)

class GrpcProtoEdgeTest(unittest.TestCase):
    """Structural edges between file, rpc, contract, and enum."""

    def _extract_common(self) -> tuple[dict, list]:
        d = tempfile.mkdtemp()
        root = Path(d)
        pkg = root / "proto"
        pkg.mkdir(parents=True)
        _write(pkg, "svc.proto", """\
            syntax = "proto3";
            package svc;
            service SvcService {
              rpc DoThing(DoThingRequest) returns (DoThingResponse);
            }
            message DoThingRequest { string id = 1; }
            message DoThingResponse { string result = 1; }
            enum State { STATE_UNSPECIFIED = 0; STATE_OK = 1; }
        """)
        res = extract(root, {"glob": "proto/**/*.proto"}, {})
        return res.nodes, res.edges

    def test_file_contains_rpc_contract_and_enum(self) -> None:
        _nodes, edges = self._extract_common()
        # ADR 0041 § Layer 1: file ids drop the extension and route
        # through ``file_id``.
        file_node_id = "file:proto/svc"
        keys = {(e["from"], e["to"], e["type"]) for e in edges}
        self.assertIn((file_node_id, "rpc:grpc:svc.svcservice.dothing", "invokes"), keys)
        self.assertIn((file_node_id, "contract:grpc:svc.dothingrequest", "contains"), keys)
        self.assertIn((file_node_id, "contract:grpc:svc.dothingresponse", "contains"), keys)
        self.assertIn((file_node_id, "enum:grpc:svc.state", "contains"), keys)

    def test_rpc_accepts_and_responds_with_contracts(self) -> None:
        _, edges = self._extract_common()
        rpc_id = "rpc:grpc:svc.svcservice.dothing"
        req_id = "contract:grpc:svc.dothingrequest"
        resp_id = "contract:grpc:svc.dothingresponse"
        self.assertTrue(any(
            e["from"] == rpc_id and e["to"] == req_id and e["type"] == "accepts"
            for e in edges
        ))
        self.assertTrue(any(
            e["from"] == rpc_id and e["to"] == resp_id
            and e["type"] == "responds_with"
            for e in edges
        ))

    def test_edges_carry_source_strategy_and_confidence(self) -> None:
        _, edges = self._extract_common()
        grpc_edges = [
            e for e in edges
            if e.get("props", {}).get("source_strategy") == "grpc_proto"
        ]
        self.assertGreater(len(grpc_edges), 0)
        for e in grpc_edges:
            self.assertIn(e["props"]["confidence"], ("definite", "inferred"))

class GrpcProtoRobustnessTest(unittest.TestCase):
    """Malformed input and non-proto files are dropped quietly."""

    def test_non_proto_and_missing_glob_yield_empty_result(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "proto"
            pkg.mkdir(parents=True)
            _write(pkg, "notes.md", "# not a proto")
            nodes, edges, discovered = _run(root)
            self.assertEqual((nodes, edges, discovered), ({}, [], []))
            empty = extract(root, {}, {})
            self.assertEqual(empty.nodes, {})
            self.assertEqual(empty.edges, [])
            self.assertEqual(list(empty.discovered_from), [])

    def test_line_and_block_comments_hide_declarations(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "proto"
            pkg.mkdir(parents=True)
            _write(pkg, "c.proto", """\
                syntax = "proto3";
                package c;
                /* service Hidden { rpc Nope(R) returns (R); } */
                service Real {
                  // rpc Ghost(R) returns (R);
                  rpc Do(R) returns (R);
                }
                message R {}
            """)
            nodes, _, _ = _run(root)
            self.assertIn("rpc:grpc:c.real.do", nodes)
            self.assertNotIn("rpc:grpc:c.real.ghost", nodes)
            self.assertNotIn("rpc:grpc:c.hidden.nope", nodes)

if __name__ == "__main__":
    unittest.main()
