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

    def _root_with_directory_graph(self) -> Path:
        """A directory sitting where ``graph.json`` should be a file (bd 9yc8)."""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        (tmp / ".weld" / "graph.json").mkdir(parents=True, exist_ok=True)
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

    def test_dispatch_weld_stale_directory_graph_returns_structured_error(
        self,
    ) -> None:
        """bd 9yc8: a directory at graph.json must not leak IsADirectoryError.

        ``weld_stale`` is the tool the original repro used -- unlike the
        other graph-backed reads it does not pass through the missing-graph
        guard (``_graph_present``) first, so this pins the fix at the exact
        call site that used to raise a raw exception with a filesystem path
        in it out of ``mcp_server.dispatch``.
        """
        root = self._root_with_directory_graph()
        result = mcp_server.dispatch("weld_stale", {}, root=root)
        self.assertEqual(result.get("error_code"), _errors.GRAPH_CORRUPT)
        self.assertIn("hint", result)
        # SAFETY: no absolute filesystem path leaks into the payload.
        self.assertNotIn(str(root), json.dumps(result))

    def test_dispatch_directory_graph_does_not_raise(self) -> None:
        root = self._root_with_directory_graph()
        # Must not raise -- same contract as the corrupt-JSON case.
        result = mcp_server.dispatch("weld_query", {"term": "foo"}, root=root)
        self.assertEqual(result.get("error_code"), _errors.GRAPH_CORRUPT)

    def test_safe_dispatch_directory_graph_converts_to_payload(self) -> None:
        """The stdio guard helper never lets a directory-shaped graph escape.

        Mirrors :meth:`test_safe_dispatch_converts_load_error_to_payload`
        for the corrupt-JSON case -- before the fix this payload's ``error``
        was the raw ``IsADirectoryError: ... '<abs path>'`` string.
        """
        root = self._root_with_directory_graph()
        payload = mcp_server.dispatch_to_text_payload(
            "weld_stale", {}, root=root
        )
        decoded = json.loads(payload)
        self.assertEqual(decoded.get("error_code"), _errors.GRAPH_CORRUPT)
        self.assertNotIn(str(root), payload)
        self.assertNotIn("IsADirectoryError", payload)

    def test_dispatch_malformed_shape_graph_returns_structured_error(self) -> None:
        # bd 5038-1c7o: {"meta": {...}} alone parses as valid JSON but is
        # missing "nodes"/"edges" -- used to raise an uncaught KeyError out
        # of Graph._build_inverted_index instead of the structured contract
        # every other malformed-graph case on this surface already gets.
        root = self._root_with(json.dumps({"meta": {"version": 1}}))
        result = mcp_server.dispatch("weld_query", {"term": "foo"}, root=root)
        self.assertEqual(result.get("error_code"), _errors.GRAPH_CORRUPT)
        self.assertIn("hint", result)

    def test_dispatch_bare_list_graph_returns_structured_error(self) -> None:
        # bd 5038-w0r4: a bare-list top-level graph.json parses fine as
        # JSON but used to raise an uncaught AttributeError out of
        # Graph.load() (data.get("meta") on a list) instead of the
        # structured contract every other malformed-graph case here gets.
        root = self._root_with(json.dumps([1, 2, 3]))
        result = mcp_server.dispatch("weld_query", {"term": "foo"}, root=root)
        self.assertEqual(result.get("error_code"), _errors.GRAPH_CORRUPT)
        self.assertIn("hint", result)

    def test_dispatch_scalar_graph_returns_structured_error(self) -> None:
        # Same crash class as the bare-list case above, for a bare scalar
        # top-level payload ('"oops"').
        root = self._root_with(json.dumps("oops"))
        result = mcp_server.dispatch("weld_query", {"term": "foo"}, root=root)
        self.assertEqual(result.get("error_code"), _errors.GRAPH_CORRUPT)
        self.assertIn("hint", result)

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

    # ----- weld_export (bd tl32) -----------------------------------------
    #
    # weld_export wraps its call to weld.export.export() in a local
    # `except ValueError`. json.JSONDecodeError IS a ValueError subclass, so
    # a corrupt graph.json used to be caught right here -- before it ever
    # reached this shared classifier -- and returned an unstructured
    # {"error": str(exc)} with no error_code at all. The directory-shape
    # case (below) was never affected: IsADirectoryError is an OSError, not
    # a ValueError, so it already reached the classifier pre-fix.

    def test_dispatch_export_corrupt_graph_returns_structured_error(self) -> None:
        root = self._root_with('{"meta": {"token": "MCP-EXPORT-SECRET"')
        result = mcp_server.dispatch(
            "weld_export", {"format": "mermaid"}, root=root
        )
        self.assertEqual(result.get("error_code"), _errors.GRAPH_CORRUPT)
        self.assertIn("hint", result)
        # SAFETY: raw bytes (and the secret) never appear in the payload.
        self.assertNotIn("MCP-EXPORT-SECRET", json.dumps(result))

    def test_dispatch_export_directory_graph_returns_structured_error(self) -> None:
        # Already correct before this fix (IsADirectoryError is not a
        # ValueError); pinned here so a future change to the local except
        # clause cannot regress it silently.
        root = self._root_with_directory_graph()
        result = mcp_server.dispatch(
            "weld_export", {"format": "mermaid"}, root=root
        )
        self.assertEqual(result.get("error_code"), _errors.GRAPH_CORRUPT)
        self.assertIn("hint", result)
        self.assertNotIn(str(root), json.dumps(result))

    def test_dispatch_export_unknown_format_stays_plain_error(self) -> None:
        """The local except ValueError's legitimate purpose survives.

        export()'s own argument-validation errors (an unrecognized
        --format) are not graph-load failures -- they must keep returning
        the plain {"error": ...} shape they always have, with no
        error_code, rather than being routed to the graph-load classifier.
        """
        root = _valid_graph_root()
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        result = mcp_server.dispatch(
            "weld_export", {"format": "bogus"}, root=root
        )
        self.assertNotIn("error_code", result)
        self.assertIn("error", result)
        self.assertIn("bogus", result["error"])

    def test_dispatch_export_valid_graph_still_succeeds(self) -> None:
        root = _valid_graph_root()
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        result = mcp_server.dispatch(
            "weld_export", {"format": "mermaid"}, root=root
        )
        self.assertNotIn("error", result)
        self.assertIn("output", result)


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
