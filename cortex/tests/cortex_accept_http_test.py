"""Acceptance tests for HTTP interaction graph extraction (project-xoq.7.2).

Exercises the FastAPI (server) and http_client (outbound) strategies
against the ``http_accept`` fixture and verifies the resulting
interaction graph nodes, edges, and protocol metadata are correct.

Both inbound (route) and outbound (rpc) surfaces must carry full
ADR 0018 interaction metadata, and the fragment must validate cleanly.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from cortex.contract import validate_fragment  # noqa: E402
from cortex.strategies.fastapi import extract as fastapi_extract  # noqa: E402
from cortex.strategies.http_client import extract as http_client_extract  # noqa: E402

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "http_accept"

class HttpServerAcceptanceTest(unittest.TestCase):
    """FastAPI server routes are extracted with full interaction metadata."""

    def setUp(self) -> None:
        result = fastapi_extract(
            _FIXTURE, {"glob": "routers/*.py"}, {}
        )
        self.nodes = result.nodes
        self.edges = result.edges

    def test_all_expected_routes_extracted(self) -> None:
        expected = {
            "route:GET:/products/",
            "route:GET:/products/{product_id}",
            "route:POST:/products/",
            "route:GET:/orders/",
            "route:POST:/orders/",
        }
        self.assertEqual(set(self.nodes.keys()), expected)

    def test_route_nodes_carry_http_protocol_metadata(self) -> None:
        for nid, node in self.nodes.items():
            if node["type"] != "route":
                continue
            props = node["props"]
            self.assertEqual(
                props.get("protocol"), "http", f"{nid}: protocol"
            )
            self.assertEqual(
                props.get("surface_kind"),
                "request_response",
                f"{nid}: surface_kind",
            )
            self.assertEqual(
                props.get("transport"), "http", f"{nid}: transport"
            )
            self.assertEqual(
                props.get("boundary_kind"),
                "inbound",
                f"{nid}: boundary_kind",
            )
            self.assertTrue(
                props.get("declared_in", "").startswith("routers/"),
                f"{nid}: declared_in should start with routers/",
            )

    def test_server_fragment_validates(self) -> None:
        errs = validate_fragment(
            {"nodes": self.nodes, "edges": self.edges},
            source_label="strategy:fastapi",
            allow_dangling_edges=True,
        )
        self.assertEqual(errs, [], f"validation errors: {errs}")

class HttpClientAcceptanceTest(unittest.TestCase):
    """Outbound HTTP calls produce rpc nodes with correct metadata."""

    def setUp(self) -> None:
        result = http_client_extract(
            _FIXTURE, {"glob": "src/**/*.py"}, {}
        )
        self.nodes = result.nodes
        self.edges = result.edges

    def test_static_outbound_calls_extracted(self) -> None:
        rpc_ids = set(self.nodes.keys())
        self.assertIn(
            "rpc:http:out:GET:https://api.example.com/products", rpc_ids
        )
        self.assertIn(
            "rpc:http:out:POST:https://api.example.com/products", rpc_ids
        )
        self.assertIn(
            "rpc:http:out:POST:https://api.example.com/orders", rpc_ids
        )
        self.assertIn(
            "rpc:http:out:GET:https://api.example.com/orders", rpc_ids
        )

    def test_dynamic_fstring_url_not_extracted(self) -> None:
        """f-string with substitution should be dropped per ADR 0018."""
        for nid in self.nodes:
            self.assertNotIn("{product_id}", nid)

    def test_outbound_rpc_nodes_carry_http_protocol_metadata(self) -> None:
        for nid, node in self.nodes.items():
            props = node["props"]
            self.assertEqual(
                props.get("protocol"), "http", f"{nid}: protocol"
            )
            self.assertEqual(
                props.get("boundary_kind"),
                "outbound",
                f"{nid}: boundary_kind",
            )
            self.assertEqual(
                props.get("transport"), "http", f"{nid}: transport"
            )

    def test_invokes_edges_link_files_to_rpc_nodes(self) -> None:
        invokes = [e for e in self.edges if e["type"] == "invokes"]
        self.assertGreaterEqual(len(invokes), 4)
        from_files = {e["from"] for e in invokes}
        self.assertIn("file:src/client/product_client.py", from_files)
        self.assertIn("file:src/client/order_client.py", from_files)

    def test_client_fragment_validates(self) -> None:
        errs = validate_fragment(
            {"nodes": self.nodes, "edges": self.edges},
            source_label="strategy:http_client",
            allow_dangling_edges=True,
        )
        self.assertEqual(errs, [], f"validation errors: {errs}")

if __name__ == "__main__":
    unittest.main()
