"""Tests for ``cortex.mcp_server`` -- the stdio MCP adapter over cortex query helpers.

These tests pin the JSON-shape contract of the six initial MCP tools and
assert that each adapter returns exactly what the equivalent CLI surface
would return, using the shared underlying helpers. They do NOT require the
``mcp`` Python SDK to be installed -- the pure-Python tool dispatch surface
is exercised directly. A separate "round-trip" test exercises the tool
registry builder (also SDK-free) to confirm all six tools are advertised
with the expected names and input-schema keys.
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

from cortex import mcp_server  # noqa: E402
from cortex.brief import BRIEF_VERSION, brief as brief_helper  # noqa: E402
from cortex.contract import SCHEMA_VERSION  # noqa: E402
from cortex.file_index import find_files  # noqa: E402
from cortex.graph import Graph  # noqa: E402

# ---------------------------------------------------------------------------
# Fixture graph
# ---------------------------------------------------------------------------

_FIXTURE_NODES: dict[str, dict] = {
    "entity:Store": {
        "type": "entity",
        "label": "Store",
        "props": {
            "file": "services/api/libs/domain/store.py",
            "exports": ["Store"],
            "description": "SQLAlchemy model for a retail store.",
        },
    },
    "entity:Offer": {
        "type": "entity",
        "label": "Offer",
        "props": {
            "file": "services/api/libs/domain/offer.py",
            "exports": ["Offer"],
            "description": "SQLAlchemy model for a promotional offer tied to a store.",
        },
    },
    "route:GET:/api/v1/stores": {
        "type": "route",
        "label": "list_stores",
        "props": {
            "file": "services/api/app/routes/stores.py",
            "exports": ["list_stores"],
            "description": "FastAPI route that lists stores.",
        },
    },
    "doc:adr/0015-kg-mcp": {
        "type": "doc",
        "label": "ADR 0015 KG MCP",
        "props": {
            "file": "docs/adrs/0015-kg-mcp-server-exposure.md",
            "doc_kind": "adr",
            "description": "Architecture decision for the KG MCP server.",
        },
    },
}

_FIXTURE_EDGES: list[dict] = [
    {
        "from": "entity:Offer",
        "to": "entity:Store",
        "type": "depends_on",
        "props": {},
    },
    {
        "from": "route:GET:/api/v1/stores",
        "to": "entity:Store",
        "type": "responds_with",
        "props": {},
    },
    # Call graph fixture: caller -> helper, plus an unresolved sentinel.
    {
        "from": "symbol:py:m:caller",
        "to": "symbol:py:m:helper",
        "type": "calls",
        "props": {"resolved": True},
    },
    {
        "from": "symbol:py:m:caller",
        "to": "symbol:unresolved:helper",
        "type": "calls",
        "props": {"resolved": False},
    },
]

_FIXTURE_NODES.update(
    {
        "symbol:py:m:helper": {
            "type": "symbol",
            "label": "helper",
            "props": {
                "module": "m",
                "qualname": "helper",
                "language": "python",
            },
        },
        "symbol:py:m:caller": {
            "type": "symbol",
            "label": "caller",
            "props": {
                "module": "m",
                "qualname": "caller",
                "language": "python",
            },
        },
        "symbol:unresolved:helper": {
            "type": "symbol",
            "label": "helper",
            "props": {
                "qualname": "helper",
                "language": "python",
                "resolved": False,
            },
        },
    }
)

def _make_graph_root() -> Path:
    """Write the fixture graph + file-index to a temp dir and return root."""
    tmp = Path(tempfile.mkdtemp())
    (tmp / ".cortex").mkdir(parents=True, exist_ok=True)
    (tmp / ".cortex" / "graph.json").write_text(
        json.dumps(
            {
                "meta": {
                    "version": SCHEMA_VERSION,
                    "git_sha": "deadbeef",
                    "updated_at": "2026-04-06T00:00:00+00:00",
                },
                "nodes": _FIXTURE_NODES,
                "edges": _FIXTURE_EDGES,
            }
        ),
        encoding="utf-8",
    )
    # Minimal file-index
    (tmp / ".cortex" / "file-index.json").write_text(
        json.dumps(
            {
                "meta": {"version": 1},
                "files": {
                    "services/api/libs/domain/store.py": ["store", "Store"],
                    "services/api/libs/domain/offer.py": ["offer", "Offer"],
                    "services/api/app/routes/stores.py": ["stores", "list_stores"],
                },
            }
        ),
        encoding="utf-8",
    )
    return tmp

class CortexMcpServerToolsTest(unittest.TestCase):
    """Unit tests for each of the six adapter functions."""

    def setUp(self) -> None:
        self.root = _make_graph_root()

    # ---- cortex_query -----------------------------------------------------

    def test_cortex_query_returns_query_envelope(self) -> None:
        result = mcp_server.cortex_query("Store", root=self.root)
        self.assertIn("matches", result)
        self.assertIn("neighbors", result)
        self.assertIn("edges", result)
        self.assertEqual(result["query"], "Store")
        match_ids = {m["id"] for m in result["matches"]}
        self.assertIn("entity:Store", match_ids)

    def test_cortex_query_matches_cli_helper(self) -> None:
        g = Graph(self.root)
        g.load()
        expected = g.query("Store", limit=5)
        result = mcp_server.cortex_query("Store", limit=5, root=self.root)
        self.assertEqual(result, expected)

    def test_cortex_query_respects_limit(self) -> None:
        result = mcp_server.cortex_query("store", limit=1, root=self.root)
        self.assertLessEqual(len(result["matches"]), 1)

    # ---- cortex_find ------------------------------------------------------

    def test_cortex_find_returns_file_envelope(self) -> None:
        result = mcp_server.cortex_find("store", root=self.root)
        self.assertEqual(result["query"], "store")
        self.assertIn("files", result)
        self.assertTrue(
            any("store" in f["path"].lower() for f in result["files"]),
            f"expected a store file in {result['files']}",
        )

    def test_cortex_find_matches_cli_helper(self) -> None:
        from cortex.file_index import load_file_index

        expected = find_files(load_file_index(self.root), "store")
        result = mcp_server.cortex_find("store", root=self.root)
        self.assertEqual(result, expected)

    # ---- cortex_context ---------------------------------------------------

    def test_cortex_context_returns_node_and_neighborhood(self) -> None:
        result = mcp_server.cortex_context("entity:Store", root=self.root)
        self.assertIn("node", result)
        self.assertEqual(result["node"]["id"], "entity:Store")
        self.assertIn("neighbors", result)
        self.assertIn("edges", result)
        neighbor_ids = {n["id"] for n in result["neighbors"]}
        # Offer depends_on Store, so Offer should appear as a neighbor
        self.assertIn("entity:Offer", neighbor_ids)

    def test_cortex_context_missing_node_returns_error(self) -> None:
        result = mcp_server.cortex_context("entity:DoesNotExist", root=self.root)
        self.assertIn("error", result)

    # ---- cortex_path ------------------------------------------------------

    def test_cortex_path_finds_shortest_path(self) -> None:
        result = mcp_server.cortex_path(
            "route:GET:/api/v1/stores",
            "entity:Offer",
            root=self.root,
        )
        self.assertIn("path", result)
        self.assertIsNotNone(result["path"])
        path_ids = [n["id"] for n in result["path"]]
        self.assertEqual(path_ids[0], "route:GET:/api/v1/stores")
        self.assertEqual(path_ids[-1], "entity:Offer")

    def test_cortex_path_unknown_node_returns_none(self) -> None:
        result = mcp_server.cortex_path(
            "entity:Store",
            "entity:Nope",
            root=self.root,
        )
        self.assertIsNone(result["path"])

    # ---- cortex_brief -----------------------------------------------------

    def test_cortex_brief_returns_versioned_envelope(self) -> None:
        result = mcp_server.cortex_brief("Store", root=self.root)
        self.assertEqual(result["brief_version"], BRIEF_VERSION)
        self.assertEqual(result["query"], "Store")
        for key in ("primary", "docs", "build", "boundaries", "edges"):
            self.assertIn(key, result)
            self.assertIsInstance(result[key], list)
        self.assertIn("provenance", result)
        self.assertIn("warnings", result)

    def test_cortex_brief_matches_cli_helper(self) -> None:
        g = Graph(self.root)
        g.load()
        expected = brief_helper(g, "Store", limit=20)
        result = mcp_server.cortex_brief("Store", root=self.root)
        self.assertEqual(result, expected)

    # ---- cortex_callers ---------------------------------------------------

    def test_cortex_callers_returns_direct_callers(self) -> None:
        result = mcp_server.cortex_callers("symbol:py:m:helper", root=self.root)
        ids = {c["id"] for c in result["callers"]}
        self.assertIn("symbol:py:m:caller", ids)
        self.assertEqual(result["symbol"], "symbol:py:m:helper")
        self.assertEqual(result["depth"], 1)

    def test_cortex_callers_matches_graph_helper(self) -> None:
        g = Graph(self.root)
        g.load()
        expected = g.callers("symbol:py:m:helper", depth=1)
        result = mcp_server.cortex_callers("symbol:py:m:helper", root=self.root)
        self.assertEqual(result, expected)

    # ---- cortex_references ------------------------------------------------

    def test_cortex_references_combines_callers_and_files(self) -> None:
        result = mcp_server.cortex_references("helper", root=self.root)
        match_ids = {m["id"] for m in result["matches"]}
        self.assertIn("symbol:py:m:helper", match_ids)
        self.assertIn("symbol:unresolved:helper", match_ids)
        self.assertIn("files", result)
        self.assertIsInstance(result["files"], list)

    # ---- cortex_stale -----------------------------------------------------

    def test_cortex_stale_returns_freshness_shape(self) -> None:
        result = mcp_server.cortex_stale(root=self.root)
        # Not a git repo (temp dir), so stale must report that cleanly.
        self.assertIn("stale", result)
        self.assertIsInstance(result["stale"], bool)

class CortexMcpServerRegistryTest(unittest.TestCase):
    """Boot the server's tool registry and verify discovery + happy-path dispatch."""

    def setUp(self) -> None:
        self.root = _make_graph_root()

    def test_tool_registry_lists_eleven_tools(self) -> None:
        tools = mcp_server.build_tools()
        names = {t.name for t in tools}
        self.assertEqual(
            names,
            {
                "cortex_query",
                "cortex_find",
                "cortex_context",
                "cortex_path",
                "cortex_brief",
                "cortex_stale",
                "cortex_callers",
                "cortex_references",
                "cortex_trace",
                "cortex_diff",
                "cortex_export",
            },
        )
        for tool in tools:
            self.assertTrue(tool.description.strip(), f"tool {tool.name} missing description")
            self.assertIsInstance(tool.input_schema, dict)
            self.assertEqual(tool.input_schema.get("type"), "object")
            self.assertIn("properties", tool.input_schema)

    def test_tool_input_schemas_declare_required_args(self) -> None:
        by_name = {t.name: t for t in mcp_server.build_tools()}
        self.assertEqual(by_name["cortex_query"].input_schema["required"], ["term"])
        self.assertEqual(by_name["cortex_find"].input_schema["required"], ["term"])
        self.assertEqual(by_name["cortex_context"].input_schema["required"], ["node_id"])
        self.assertEqual(
            by_name["cortex_path"].input_schema["required"],
            ["from_id", "to_id"],
        )
        self.assertEqual(by_name["cortex_brief"].input_schema["required"], ["area"])
        self.assertEqual(by_name["cortex_stale"].input_schema.get("required", []), [])
        self.assertEqual(
            by_name["cortex_callers"].input_schema["required"], ["symbol_id"]
        )
        self.assertEqual(
            by_name["cortex_references"].input_schema["required"], ["symbol_name"]
        )

    def test_dispatch_cortex_query_happy_path(self) -> None:
        result = mcp_server.dispatch("cortex_query", {"term": "Store"}, root=self.root)
        self.assertIn("matches", result)
        match_ids = {m["id"] for m in result["matches"]}
        self.assertIn("entity:Store", match_ids)

    def test_dispatch_cortex_brief_happy_path(self) -> None:
        result = mcp_server.dispatch("cortex_brief", {"area": "Store"}, root=self.root)
        self.assertEqual(result["brief_version"], BRIEF_VERSION)

    def test_dispatch_unknown_tool_raises(self) -> None:
        with self.assertRaises(KeyError):
            mcp_server.dispatch("cortex_nope", {}, root=self.root)

    def test_import_does_not_require_mcp_sdk(self) -> None:
        # Importing cortex.mcp_server must succeed even when the optional ``mcp``
        # SDK is not installed; only ``run_stdio()`` may require it.
        self.assertTrue(hasattr(mcp_server, "build_tools"))
        self.assertTrue(hasattr(mcp_server, "dispatch"))

if __name__ == "__main__":
    unittest.main()
