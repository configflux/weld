"""Tests for ``wd trace`` -- protocol-aware capability path surface.

``wd trace`` returns a coherent cross-boundary slice for a task area or
node, including service, contract, interface, boundary, and verification
context. Per tracked project the surface must:

  - model output around service / contract / interface / boundary /
    verification links
  - reuse existing graph semantics (classification from ``weld.brief``,
    edge types from the contract) rather than inventing a second
    interaction model
  - be optimized for agent consumption (stable JSON envelope, no
    terminal-only formatting tricks)

"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.contract import SCHEMA_VERSION  # noqa: E402
from weld.graph import Graph  # noqa: E402
from weld.trace import TRACE_VERSION, main as trace_main, trace  # noqa: E402
from weld.trace_contract import trace_contract_warnings  # noqa: E402

_TS = "2026-04-09T12:00:00+00:00"

def _make_graph(nodes: dict, edges: list | None = None) -> Graph:
    tmpdir = Path(tempfile.mkdtemp())
    g = Graph(tmpdir)
    g.load()
    g._data = {
        "meta": {"version": SCHEMA_VERSION, "updated_at": _TS, "git_sha": "tr8c"},
        "nodes": nodes,
        "edges": edges or [],
    }
    return g

def _slice_graph() -> Graph:
    """Build a small graph that exercises the full slice."""
    nodes = {
        "service:orders": {
            "type": "service", "label": "orders service",
            "props": {"authority": "canonical", "confidence": "definite",
                      "file": "services/orders/main.py"},
        },
        "rpc:create-order": {
            "type": "rpc", "label": "CreateOrder",
            "props": {"authority": "canonical", "confidence": "definite",
                      "protocol": "grpc", "surface_kind": "request_response",
                      "boundary_kind": "inbound"},
        },
        "channel:order-events": {
            "type": "channel", "label": "order-events",
            "props": {"authority": "canonical", "confidence": "definite",
                      "protocol": "event", "surface_kind": "pub_sub",
                      "boundary_kind": "outbound"},
        },
        "boundary:public-api": {
            "type": "boundary", "label": "public api boundary",
            "props": {"authority": "canonical", "confidence": "definite",
                      "boundary_kind": "inbound"},
        },
        "contract:orders-v1": {
            "type": "contract", "label": "orders v1 contract",
            "props": {"authority": "canonical", "confidence": "definite"},
        },
        "test-target:orders-it": {
            "type": "test-target", "label": "//services/orders:it",
            "props": {"roles": ["test"], "confidence": "definite"},
        },
        "doc:orders-design": {
            "type": "doc", "label": "orders design doc",
            "props": {"doc_kind": "guide", "authority": "canonical"},
        },
        "service:unrelated": {
            "type": "service", "label": "unrelated service",
            "props": {"authority": "canonical", "confidence": "definite"},
        },
    }
    edges = [
        {"from": "service:orders", "to": "rpc:create-order",
         "type": "exposes", "props": {}},
        {"from": "service:orders", "to": "channel:order-events",
         "type": "produces", "props": {}},
        {"from": "service:orders", "to": "boundary:public-api",
         "type": "contains", "props": {}},
        {"from": "rpc:create-order", "to": "contract:orders-v1",
         "type": "implements", "props": {}},
        {"from": "test-target:orders-it", "to": "service:orders",
         "type": "verifies", "props": {}},
        {"from": "doc:orders-design", "to": "service:orders",
         "type": "documents", "props": {}},
    ]
    return _make_graph(nodes, edges)

class TraceEnvelopeTest(unittest.TestCase):
    """Stable envelope contract: required fields are always present."""

    def test_envelope_has_required_keys(self) -> None:
        g = _slice_graph()
        result = trace(g, term="orders")
        for key in (
            "trace_version", "anchor", "services", "interfaces",
            "contracts", "boundaries", "verifications", "edges",
            "provenance", "warnings",
        ):
            self.assertIn(key, result, f"missing envelope key: {key}")

    def test_trace_version_is_int(self) -> None:
        g = _slice_graph()
        result = trace(g, term="orders")
        self.assertIsInstance(result["trace_version"], int)
        self.assertEqual(result["trace_version"], TRACE_VERSION)

    def test_empty_graph_returns_empty_buckets(self) -> None:
        g = _make_graph({})
        result = trace(g, term="anything")
        self.assertEqual(result["services"], [])
        self.assertEqual(result["interfaces"], [])
        self.assertEqual(result["contracts"], [])
        self.assertEqual(result["boundaries"], [])
        self.assertEqual(result["verifications"], [])
        self.assertEqual(result["edges"], [])
        self.assertTrue(result["warnings"])  # warns about no anchor

    def test_provenance_present(self) -> None:
        g = _slice_graph()
        result = trace(g, term="orders")
        prov = result["provenance"]
        self.assertEqual(prov["graph_sha"], "tr8c")
        self.assertEqual(prov["updated_at"], _TS)

class TraceSliceContentTest(unittest.TestCase):
    """The slice surfaces service / interface / contract / boundary /
    verification context for the anchor."""

    def test_includes_service_for_term(self) -> None:
        g = _slice_graph()
        result = trace(g, term="orders")
        ids = {n["id"] for n in result["services"]}
        self.assertIn("service:orders", ids)

    def test_includes_rpc_interface(self) -> None:
        g = _slice_graph()
        result = trace(g, term="orders")
        ids = {n["id"] for n in result["interfaces"]}
        self.assertIn("rpc:create-order", ids)

    def test_includes_channel_interface(self) -> None:
        g = _slice_graph()
        result = trace(g, term="orders")
        ids = {n["id"] for n in result["interfaces"]}
        self.assertIn("channel:order-events", ids)

    def test_includes_contract(self) -> None:
        g = _slice_graph()
        result = trace(g, term="orders")
        ids = {n["id"] for n in result["contracts"]}
        self.assertIn("contract:orders-v1", ids)

    def test_includes_boundary(self) -> None:
        g = _slice_graph()
        result = trace(g, term="orders")
        ids = {n["id"] for n in result["boundaries"]}
        self.assertIn("boundary:public-api", ids)

    def test_includes_verification(self) -> None:
        g = _slice_graph()
        result = trace(g, term="orders")
        ids = {n["id"] for n in result["verifications"]}
        self.assertIn("test-target:orders-it", ids)

    def test_excludes_unrelated_service(self) -> None:
        g = _slice_graph()
        result = trace(g, term="orders")
        ids = {n["id"] for n in result["services"]}
        self.assertNotIn("service:unrelated", ids)

    def test_edges_connect_included_nodes(self) -> None:
        g = _slice_graph()
        result = trace(g, term="orders")
        node_ids: set[str] = set()
        for bucket in (
            "services", "interfaces", "contracts",
            "boundaries", "verifications",
        ):
            node_ids.update(n["id"] for n in result[bucket])
        # Every emitted edge must reference nodes that are in the slice.
        for edge in result["edges"]:
            self.assertIn(edge["from"], node_ids,
                          f"edge from {edge['from']} not in slice")
            self.assertIn(edge["to"], node_ids,
                          f"edge to {edge['to']} not in slice")

class TraceAnchorByNodeTest(unittest.TestCase):
    """Tracing from a known node id seeds the slice with that node."""

    def test_anchor_by_node_id(self) -> None:
        g = _slice_graph()
        result = trace(g, node_id="rpc:create-order")
        # The anchor is the rpc node; the slice should still surface the
        # owning service, the contract it implements, and verifications.
        self.assertEqual(result["anchor"]["kind"], "node")
        self.assertEqual(result["anchor"]["id"], "rpc:create-order")
        ids = {n["id"] for n in result["services"]}
        self.assertIn("service:orders", ids)
        ifaces = {n["id"] for n in result["interfaces"]}
        self.assertIn("rpc:create-order", ifaces)
        contracts = {n["id"] for n in result["contracts"]}
        self.assertIn("contract:orders-v1", contracts)

    def test_unknown_node_id_warns(self) -> None:
        g = _slice_graph()
        result = trace(g, node_id="rpc:does-not-exist")
        self.assertTrue(result["warnings"])
        self.assertEqual(result["services"], [])

    def test_term_anchor_kind(self) -> None:
        g = _slice_graph()
        result = trace(g, term="orders")
        self.assertEqual(result["anchor"]["kind"], "term")
        self.assertEqual(result["anchor"]["term"], "orders")

class TraceReuseSemanticsTest(unittest.TestCase):
    """Trace reuses ``weld.brief`` classification rather than inventing
    a second interaction model."""

    def test_route_with_protocol_promoted_to_interface(self) -> None:
        # A ``route`` node carrying protocol metadata classifies as an
        # interface in ``weld.brief`` and must do the same here.
        nodes = {
            "service:web": {
                "type": "service", "label": "web service",
                "props": {"authority": "canonical", "confidence": "definite"},
            },
            "route:GET-/users": {
                "type": "route", "label": "GET /users",
                "props": {"protocol": "http", "boundary_kind": "inbound",
                          "authority": "canonical", "confidence": "definite"},
            },
        }
        edges = [
            {"from": "service:web", "to": "route:GET-/users",
             "type": "exposes", "props": {}},
        ]
        g = _make_graph(nodes, edges)
        result = trace(g, term="web")
        ifaces = {n["id"] for n in result["interfaces"]}
        self.assertIn("route:GET-/users", ifaces)

class TraceStartupFlowTest(unittest.TestCase):
    """Startup-oriented queries should find runtime entrypoints and wiring."""

    def _startup_graph(self) -> Graph:
        nodes = {
            "service:api": {
                "type": "service", "label": "api service",
                "props": {"description": "Service runtime startup host."},
            },
            "entrypoint:services/api/main": {
                "type": "entrypoint", "label": "main",
                "props": {
                    "description": "Runtime startup entrypoint for execution flow.",
                },
            },
            "boundary:services/api/app": {
                "type": "boundary", "label": "FastAPI app",
                "props": {"description": "Application runtime boundary."},
            },
            "deploy:docker_compose": {
                "type": "deploy", "label": "docker-compose.yml",
                "props": {"deploy_kind": "compose"},
            },
        }
        edges = [
            {"from": "service:api", "to": "entrypoint:services/api/main",
             "type": "contains", "props": {}},
            {"from": "service:api", "to": "boundary:services/api/app",
             "type": "contains", "props": {}},
            {"from": "boundary:services/api/app",
             "to": "entrypoint:services/api/main",
             "type": "exposes", "props": {}},
            {"from": "deploy:docker_compose", "to": "service:api",
             "type": "configures", "props": {}},
        ]
        return _make_graph(nodes, edges)

    def test_natural_language_startup_query_uses_or_fallback(self) -> None:
        result = trace(self._startup_graph(), term="how does this service start")
        boundary_ids = {n["id"] for n in result["boundaries"]}
        self.assertIn("entrypoint:services/api/main", boundary_ids)
        self.assertIn("boundary:services/api/app", boundary_ids)
        self.assertIn("deploy:docker_compose", boundary_ids)
        self.assertTrue(any("or_fallback" in w for w in result["warnings"]))

    def test_trace_inert_anchor_warns(self) -> None:
        g = _make_graph({
            "event_contract:order-created": {
                "type": "event_contract",
                "label": "OrderCreated",
                "props": {},
            },
        })
        result = trace(g, node_id="event_contract:order-created")
        self.assertEqual(result["services"], [])
        self.assertTrue(any("trace buckets" in w for w in result["warnings"]))

    def test_fragment_contract_warns_for_custom_trace_inert_vocab(self) -> None:
        warnings = trace_contract_warnings({
            "nodes": {
                "event_contract:order-created": {
                    "type": "event_contract",
                    "label": "OrderCreated",
                    "props": {},
                },
                "capability:order-handler": {
                    "type": "capability",
                    "label": "Order handler",
                    "props": {},
                },
            },
            "edges": [
                {"from": "event_contract:order-created",
                 "to": "capability:order-handler",
                 "type": "handled_by", "props": {}},
            ],
        })
        joined = " ".join(warnings)
        self.assertIn("trace bucket", joined)
        self.assertIn("trace-followed edges", joined)

class TraceCLITest(unittest.TestCase):
    """End-to-end smoke test for the CLI entry point."""

    def test_cli_emits_json_envelope(self) -> None:
        # Build a real on-disk graph so the CLI can load it.
        tmpdir = Path(tempfile.mkdtemp())
        g = Graph(tmpdir)
        g.load()
        g._data = _slice_graph()._data
        g.save()
        buf = io.StringIO()
        with redirect_stdout(buf):
            trace_main(["orders", "--root", str(tmpdir)])
        result = json.loads(buf.getvalue())
        self.assertEqual(result["trace_version"], TRACE_VERSION)
        ids = {n["id"] for n in result["services"]}
        self.assertIn("service:orders", ids)

    def test_cli_node_flag(self) -> None:
        tmpdir = Path(tempfile.mkdtemp())
        g = Graph(tmpdir)
        g.load()
        g._data = _slice_graph()._data
        g.save()
        buf = io.StringIO()
        with redirect_stdout(buf):
            trace_main(["--node", "rpc:create-order", "--root", str(tmpdir)])
        result = json.loads(buf.getvalue())
        self.assertEqual(result["anchor"]["kind"], "node")
        self.assertEqual(result["anchor"]["id"], "rpc:create-order")

if __name__ == "__main__":
    unittest.main()
