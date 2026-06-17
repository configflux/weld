"""MCP structured-error contract: corrupt/unsupported graph never crashes.

Split out of ``weld_mcp_server_test`` (which pins the happy-path tool-adapter
shapes) so each file stays under the line-count cap. The MCP boundary
historically let a ``JSONDecodeError`` from ``Graph.load`` escape as an
uncaught exception -- on the stdio path that is a transport crash, and on the
dispatch path an unhandled raise. These tests lock the structured-error
contract (``error_code`` + ``hint``, shared with the CLI via ``weld._errors``)
on both the ``dispatch`` and the SDK-free ``dispatch_to_text_payload`` seams.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path


from weld import _errors  # noqa: E402
from weld import mcp_server  # noqa: E402
from weld.contract import SCHEMA_VERSION  # noqa: E402


def _valid_graph_root() -> Path:
    """Write a minimal valid graph + file index and return the root."""
    tmp = Path(tempfile.mkdtemp())
    (tmp / ".weld").mkdir(parents=True, exist_ok=True)
    (tmp / ".weld" / "graph.json").write_text(
        json.dumps(
            {
                "meta": {"version": SCHEMA_VERSION},
                "nodes": {
                    "entity:Store": {
                        "type": "entity",
                        "label": "Store",
                        "props": {"exports": ["Store"]},
                    }
                },
                "edges": [],
            }
        ),
        encoding="utf-8",
    )
    (tmp / ".weld" / "file-index.json").write_text(
        json.dumps({"meta": {"version": 1}, "files": {}}), encoding="utf-8"
    )
    return tmp


class WeldMcpServerCorruptGraphTest(unittest.TestCase):
    """A corrupt/unsupported graph yields a structured error, not a crash."""

    def _root_with(self, graph_text: str) -> Path:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        (tmp / ".weld").mkdir(parents=True, exist_ok=True)
        (tmp / ".weld" / "graph.json").write_text(graph_text, encoding="utf-8")
        return tmp

    def test_dispatch_corrupt_graph_returns_structured_error(self) -> None:
        # Truncated JSON carrying a secret value.
        root = self._root_with('{"meta": {"token": "MCP-SECRET-XYZ"')
        result = mcp_server.dispatch("weld_query", {"term": "foo"}, root=root)
        self.assertEqual(result.get("error_code"), _errors.GRAPH_CORRUPT)
        self.assertIn("hint", result)
        # SAFETY: raw bytes (and the secret) never appear in the payload.
        self.assertNotIn("MCP-SECRET-XYZ", json.dumps(result))

    def test_dispatch_corrupt_graph_does_not_raise(self) -> None:
        root = self._root_with('{"nodes": ')
        # Must not raise -- the whole point of the contract.
        result = mcp_server.dispatch("weld_context", {"node_id": "x"}, root=root)
        self.assertIn("error_code", result)

    def test_dispatch_schema_mismatch_returns_structured_error(self) -> None:
        root = self._root_with(
            json.dumps({"meta": {"schema_version": 999}, "nodes": {}, "edges": []})
        )
        result = mcp_server.dispatch("weld_query", {"term": "foo"}, root=root)
        self.assertEqual(result.get("error_code"), _errors.SCHEMA_MISMATCH)
        # SAFETY: the absolute graph path is not leaked to the MCP client.
        self.assertNotIn(str(root), json.dumps(result))

    def test_safe_dispatch_converts_load_error_to_payload(self) -> None:
        """The stdio guard helper never lets load errors escape as a crash.

        ``run_stdio`` is SDK-gated and not exercised here, but the helper it
        relies on (:func:`mcp_server.dispatch_to_text_payload`) is SDK-free
        and is what guarantees a corrupt graph becomes a JSON text payload
        instead of a transport crash.
        """
        root = self._root_with('{"meta": ')
        payload = mcp_server.dispatch_to_text_payload(
            "weld_query", {"term": "foo"}, root=root
        )
        decoded = json.loads(payload)
        self.assertEqual(decoded.get("error_code"), _errors.GRAPH_CORRUPT)

    def test_safe_dispatch_unknown_tool_becomes_payload_not_raise(self) -> None:
        root = self._root_with(json.dumps({"meta": {}, "nodes": {}, "edges": []}))
        payload = mcp_server.dispatch_to_text_payload("weld_nope", {}, root=root)
        decoded = json.loads(payload)
        self.assertIn("error", decoded)

    def test_safe_dispatch_happy_path_unchanged(self) -> None:
        root = _valid_graph_root()
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        payload = mcp_server.dispatch_to_text_payload(
            "weld_query", {"term": "Store"}, root=root
        )
        decoded = json.loads(payload)
        self.assertIn("matches", decoded)


class WeldMcpServerNodeNotFoundTest(unittest.TestCase):
    """A node-not-found result carries the shared ``node_not_found`` code.

    9gla.2 stamped ``error_code=node_not_found`` on the CLI ``context`` /
    ``callers`` commands (via :func:`weld._graph_cli_errors.emit_node_lookup`)
    but the MCP surface still returned Graph's bare
    ``{"error": "node not found: X"}`` with no machine-readable code. These
    tests lock CLI/MCP parity: both surfaces must emit the identical
    ``error_code`` + ``hint`` from :mod:`weld._errors` so an agent can branch
    on the code regardless of surface.
    """

    def setUp(self) -> None:
        self.root = _valid_graph_root()
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))

    def test_dispatch_context_missing_node_carries_code(self) -> None:
        result = mcp_server.dispatch(
            "weld_context", {"node_id": "entity:Nope"}, root=self.root
        )
        self.assertEqual(result.get("error_code"), _errors.NODE_NOT_FOUND)
        self.assertEqual(
            result.get("hint"), _errors.ERROR_HINTS[_errors.NODE_NOT_FOUND]
        )
        # The original human-readable message is preserved (additive).
        self.assertIn("not found", result.get("error", ""))
        # SAFETY: only the caller-supplied id is echoed; nothing else leaks.
        self.assertIn("entity:Nope", result["error"])

    def test_dispatch_callers_missing_node_carries_code(self) -> None:
        result = mcp_server.dispatch(
            "weld_callers", {"symbol_id": "symbol:py:nope:ghost"}, root=self.root
        )
        self.assertEqual(result.get("error_code"), _errors.NODE_NOT_FOUND)
        self.assertEqual(
            result.get("hint"), _errors.ERROR_HINTS[_errors.NODE_NOT_FOUND]
        )
        # The callers payload shape (symbol/depth/callers/edges) is preserved.
        self.assertEqual(result.get("callers"), [])
        self.assertIn("not found", result.get("error", ""))

    def test_text_payload_context_missing_node_carries_code(self) -> None:
        payload = mcp_server.dispatch_to_text_payload(
            "weld_context", {"node_id": "entity:Nope"}, root=self.root
        )
        decoded = json.loads(payload)
        self.assertEqual(decoded.get("error_code"), _errors.NODE_NOT_FOUND)
        self.assertIn("hint", decoded)

    def test_text_payload_callers_missing_node_carries_code(self) -> None:
        payload = mcp_server.dispatch_to_text_payload(
            "weld_callers", {"symbol_id": "ghost"}, root=self.root
        )
        decoded = json.loads(payload)
        self.assertEqual(decoded.get("error_code"), _errors.NODE_NOT_FOUND)

    def test_dispatch_context_resolved_has_no_error_code(self) -> None:
        # A real node resolves; no spurious error_code is stamped.
        result = mcp_server.dispatch(
            "weld_context", {"node_id": "entity:Store"}, root=self.root
        )
        self.assertNotIn("error_code", result)
        self.assertNotIn("error", result)

    def test_dispatch_path_miss_is_unchanged(self) -> None:
        # weld_path uses {"path": None, "reason": ...}; the CLI 'path' command
        # does NOT stamp node_not_found either, so MCP must stay in parity and
        # leave the path-miss payload untouched (no error / no error_code).
        result = mcp_server.dispatch(
            "weld_path",
            {"from_id": "entity:Store", "to_id": "entity:Ghost"},
            root=self.root,
        )
        self.assertIsNone(result.get("path"))
        self.assertNotIn("error_code", result)
        self.assertNotIn("error", result)


if __name__ == "__main__":
    unittest.main()
