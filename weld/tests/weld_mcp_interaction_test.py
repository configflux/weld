"""Tests for interaction-aware MCP surface: weld_trace tool and weld_brief v2 parity.

Verifies that the MCP surface exposes weld_trace and returns the same
interaction-aware retrieval packets as the CLI. Also confirms the weld_brief
tool description and output reflect BRIEF_VERSION=2 with interfaces bucket.

"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld import mcp_server  # noqa: E402
from weld.brief import BRIEF_VERSION, brief as brief_helper  # noqa: E402
from weld.contract import SCHEMA_VERSION  # noqa: E402
from weld.graph import Graph  # noqa: E402
from weld.trace import TRACE_VERSION, trace as trace_helper  # noqa: E402

# ---------------------------------------------------------------------------
# Fixture graph with interaction surfaces
# ---------------------------------------------------------------------------

_FIXTURE_NODES: dict[str, dict] = {
    "service:orders": {
        "type": "service",
        "label": "orders service",
        "props": {
            "authority": "canonical",
            "confidence": "definite",
            "file": "services/orders/main.py",
        },
    },
    "rpc:create-order": {
        "type": "rpc",
        "label": "CreateOrder",
        "props": {
            "authority": "canonical",
            "confidence": "definite",
            "protocol": "grpc",
            "surface_kind": "request_response",
            "boundary_kind": "inbound",
        },
    },
    "channel:order-events": {
        "type": "channel",
        "label": "order-events",
        "props": {
            "authority": "canonical",
            "confidence": "definite",
            "protocol": "event",
            "surface_kind": "pub_sub",
            "boundary_kind": "outbound",
        },
    },
    "boundary:public-api": {
        "type": "boundary",
        "label": "public api boundary",
        "props": {
            "authority": "canonical",
            "confidence": "definite",
            "boundary_kind": "inbound",
        },
    },
    "contract:orders-v1": {
        "type": "contract",
        "label": "orders v1 contract",
        "props": {"authority": "canonical", "confidence": "definite"},
    },
    "test-target:orders-it": {
        "type": "test-target",
        "label": "//services/orders:it",
        "props": {"roles": ["test"], "confidence": "definite"},
    },
}

_FIXTURE_EDGES: list[dict] = [
    {
        "from": "service:orders",
        "to": "rpc:create-order",
        "type": "exposes",
        "props": {},
    },
    {
        "from": "service:orders",
        "to": "channel:order-events",
        "type": "produces",
        "props": {},
    },
    {
        "from": "service:orders",
        "to": "boundary:public-api",
        "type": "contains",
        "props": {},
    },
    {
        "from": "rpc:create-order",
        "to": "contract:orders-v1",
        "type": "implements",
        "props": {},
    },
    {
        "from": "test-target:orders-it",
        "to": "service:orders",
        "type": "verifies",
        "props": {},
    },
]

def _make_graph_root() -> Path:
    """Write fixture graph + file-index to a temp dir and return root."""
    tmp = Path(tempfile.mkdtemp())
    (tmp / ".weld").mkdir(parents=True, exist_ok=True)
    (tmp / ".weld" / "graph.json").write_text(
        json.dumps(
            {
                "meta": {
                    "version": SCHEMA_VERSION,
                    "git_sha": "deadbeef",
                    "updated_at": "2026-04-09T00:00:00+00:00",
                },
                "nodes": _FIXTURE_NODES,
                "edges": _FIXTURE_EDGES,
            }
        ),
        encoding="utf-8",
    )
    (tmp / ".weld" / "file-index.json").write_text(
        json.dumps({"meta": {"version": 1}, "files": {}}),
        encoding="utf-8",
    )
    return tmp

class WeldMcpTraceToolTest(unittest.TestCase):
    """weld_trace MCP tool: adapter + dispatch + registry parity."""

    def setUp(self) -> None:
        self.root = _make_graph_root()

    def test_trace_tool_in_registry(self) -> None:
        tools = mcp_server.build_tools()
        names = {t.name for t in tools}
        self.assertIn("weld_trace", names)

    def test_trace_dispatch_by_term(self) -> None:
        result = mcp_server.dispatch(
            "weld_trace", {"term": "orders"}, root=self.root
        )
        self.assertEqual(result["trace_version"], TRACE_VERSION)
        self.assertIn("services", result)
        self.assertIn("interfaces", result)
        self.assertIn("contracts", result)
        self.assertIn("boundaries", result)
        self.assertIn("verifications", result)
        self.assertIn("edges", result)

    def test_trace_dispatch_by_node_id(self) -> None:
        result = mcp_server.dispatch(
            "weld_trace", {"node_id": "rpc:create-order"}, root=self.root
        )
        self.assertEqual(result["anchor"]["kind"], "node")
        self.assertEqual(result["anchor"]["id"], "rpc:create-order")

    def test_trace_matches_cli_helper(self) -> None:
        g = Graph(self.root)
        g.load()
        expected = trace_helper(g, term="orders")
        result = mcp_server.dispatch(
            "weld_trace", {"term": "orders"}, root=self.root
        )
        self.assertEqual(result, expected)

    def test_trace_tool_schema(self) -> None:
        by_name = {t.name: t for t in mcp_server.build_tools()}
        schema = by_name["weld_trace"].input_schema
        self.assertIn("term", schema["properties"])
        self.assertIn("node_id", schema["properties"])
        # Exactly one of term/node_id is required at call time but
        # the JSON schema does not enforce XOR -- the handler does.
        self.assertEqual(schema.get("required", []), [])

    def test_trace_depth_param_forwarded(self) -> None:
        result = mcp_server.dispatch(
            "weld_trace", {"term": "orders", "depth": 1}, root=self.root
        )
        # With depth=1 the slice may be smaller but the envelope is valid.
        self.assertEqual(result["trace_version"], TRACE_VERSION)

    def test_trace_seed_limit_param_forwarded(self) -> None:
        result = mcp_server.dispatch(
            "weld_trace", {"term": "orders", "seed_limit": 1}, root=self.root
        )
        self.assertEqual(result["trace_version"], TRACE_VERSION)

class WeldMcpBriefV2ParityTest(unittest.TestCase):
    """weld_brief MCP tool reflects v2 with interfaces bucket."""

    def setUp(self) -> None:
        self.root = _make_graph_root()

    def test_brief_returns_interfaces_bucket(self) -> None:
        result = mcp_server.dispatch(
            "weld_brief", {"area": "orders"}, root=self.root
        )
        self.assertIn("interfaces", result)
        self.assertEqual(result["brief_version"], BRIEF_VERSION)
        self.assertEqual(result["brief_version"], 2)

    def test_brief_description_mentions_interfaces(self) -> None:
        by_name = {t.name: t for t in mcp_server.build_tools()}
        desc = by_name["weld_brief"].description
        self.assertIn("interfaces", desc.lower())

    def test_brief_matches_cli_helper_with_interfaces(self) -> None:
        g = Graph(self.root)
        g.load()
        expected = brief_helper(g, "orders", limit=20)
        result = mcp_server.dispatch(
            "weld_brief", {"area": "orders"}, root=self.root
        )
        self.assertEqual(result, expected)
        self.assertIn("interfaces", result)

class WeldMcpRegistryCountTest(unittest.TestCase):
    """After adding weld_enrich, the registry should have 13 tools."""

    def test_tool_registry_lists_thirteen_tools(self) -> None:
        tools = mcp_server.build_tools()
        self.assertEqual(len(tools), 13)
        names = {t.name for t in tools}
        self.assertEqual(
            names,
            {
                "weld_query",
                "weld_find",
                "weld_context",
                "weld_path",
                "weld_brief",
                "weld_stale",
                "weld_callers",
                "weld_references",
                "weld_trace",
                "weld_diff",
                "weld_export",
                "weld_impact",
                "weld_enrich",
            },
        )

if __name__ == "__main__":
    unittest.main()
