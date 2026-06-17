"""MCP ``weld_review`` tool surface (ADR 0055, ADR 0023).

The MCP tool exposes review/show/accept/reject as sub-operations on a single
tool. This test pins:

* ``weld_review`` is in the registered tool list (added to EXPECTED_TOOL_NAMES).
* The tool's ``input_schema`` declares ``op`` as required and lists the
  supported operations.
* Each op routes to the matching Python helper.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_tests_dir = str(Path(__file__).resolve().parent)
if _tests_dir not in sys.path:
    sys.path.insert(0, _tests_dir)

from mcp_expected_tools import EXPECTED_TOOL_NAMES  # noqa: E402
from weld import mcp_server  # noqa: E402
from weld._review import mint_edge_id  # noqa: E402
from weld.graph import Graph  # noqa: E402


def _seed_graph(root: Path) -> dict:
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    g = Graph(root)
    g.load()
    g.add_node("symbol:caller", "symbol", "caller", {})
    g.add_node("symbol:callee", "symbol", "callee", {})
    edge = {
        "from": "symbol:caller",
        "to": "symbol:callee",
        "type": "calls",
        "props": {
            "source_strategy": "anthropic_enrichment",
            "confidence": "speculative",
        },
    }
    g.add_edge(edge["from"], edge["to"], edge["type"], edge["props"])
    g.save()
    return edge


class WeldReviewRegistryTest(unittest.TestCase):
    """``weld_review`` is registered with the right schema."""

    def test_weld_review_is_in_expected_tool_names(self) -> None:
        self.assertIn("weld_review", EXPECTED_TOOL_NAMES)

    def test_weld_review_is_in_build_tools(self) -> None:
        names = {t.name for t in mcp_server.build_tools()}
        self.assertIn("weld_review", names)

    def test_weld_review_schema_has_op_required(self) -> None:
        by_name = {t.name: t for t in mcp_server.build_tools()}
        schema = by_name["weld_review"].input_schema
        self.assertEqual(schema["required"], ["op"])
        op_prop = schema["properties"]["op"]
        self.assertIn("list", op_prop["enum"])
        self.assertIn("show", op_prop["enum"])
        self.assertIn("accept", op_prop["enum"])
        self.assertIn("reject", op_prop["enum"])


class WeldReviewDispatchTest(unittest.TestCase):
    """Each op routes to the matching Python helper."""

    def test_dispatch_list_returns_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_graph(root)
            result = mcp_server.dispatch(
                "weld_review", {"op": "list"}, root=root,
            )
            self.assertIn("edges", result)
            self.assertEqual(len(result["edges"]), 1)

    def test_dispatch_show_returns_edge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            edge = _seed_graph(root)
            eid = mint_edge_id(edge)
            result = mcp_server.dispatch(
                "weld_review",
                {"op": "show", "edge_id": eid},
                root=root,
            )
            self.assertEqual(result["review_id"], eid)

    def test_dispatch_accept_promotes_confidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            edge = _seed_graph(root)
            eid = mint_edge_id(edge)
            result = mcp_server.dispatch(
                "weld_review",
                {"op": "accept", "edge_id": eid, "reason": "LGTM"},
                root=root,
            )
            self.assertEqual(result["decision"], "accepted")
            self.assertEqual(result["confidence"], "definite")

    def test_dispatch_reject_records_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            edge = _seed_graph(root)
            eid = mint_edge_id(edge)
            result = mcp_server.dispatch(
                "weld_review",
                {"op": "reject", "edge_id": eid},
                root=root,
            )
            self.assertEqual(result["decision"], "rejected")

    def test_dispatch_unknown_op_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_graph(root)
            result = mcp_server.dispatch(
                "weld_review", {"op": "nope"}, root=root,
            )
            self.assertIn("error", result)


if __name__ == "__main__":
    unittest.main()
