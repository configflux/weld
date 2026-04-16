"""Tests for partial-coverage and freshness warnings."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.contract import SCHEMA_VERSION  # noqa: E402
from weld.graph import Graph  # noqa: E402
from weld.warnings import (  # noqa: E402
    check_confidence_gaps,
    check_freshness,
    check_partial_coverage,
)

_TS = "2026-04-09T12:00:00+00:00"

def _make_graph(
    nodes: dict,
    edges: list | None = None,
    git_sha: str | None = "abc123",
) -> Graph:
    tmpdir = Path(tempfile.mkdtemp())
    g = Graph(tmpdir)
    g.load()
    meta: dict = {
        "version": SCHEMA_VERSION,
        "updated_at": _TS,
    }
    if git_sha is not None:
        meta["git_sha"] = git_sha
    g._data = {"meta": meta, "nodes": nodes, "edges": edges or []}
    return g

# -- Freshness tests ---------------------------------------------------------

class FreshnessTest(unittest.TestCase):
    """check_freshness emits [stale] warnings when the graph is outdated."""

    def test_fresh_graph_no_warnings(self) -> None:
        g = _make_graph({})
        with patch.object(
            g, "stale", return_value={"stale": False, "commits_behind": 0}
        ):
            self.assertEqual(check_freshness(g), [])

    def test_stale_graph_with_commits_behind(self) -> None:
        g = _make_graph({})
        with patch.object(
            g, "stale",
            return_value={
                "stale": True,
                "commits_behind": 5,
                "graph_sha": "old",
            },
        ):
            warnings = check_freshness(g)
            self.assertEqual(len(warnings), 1)
            self.assertTrue(warnings[0].startswith("[stale]"))
            self.assertIn("5 commit(s)", warnings[0])

    def test_stale_no_sha_recorded(self) -> None:
        g = _make_graph({})
        with patch.object(
            g, "stale",
            return_value={
                "stale": True,
                "commits_behind": -1,
                "graph_sha": None,
            },
        ):
            warnings = check_freshness(g)
            self.assertEqual(len(warnings), 1)
            self.assertIn("no recorded git_sha", warnings[0])

    def test_stale_force_push(self) -> None:
        g = _make_graph({})
        with patch.object(
            g, "stale",
            return_value={
                "stale": True,
                "commits_behind": -1,
                "graph_sha": "old",
            },
        ):
            warnings = check_freshness(g)
            self.assertEqual(len(warnings), 1)
            self.assertIn("force-push", warnings[0])

    def test_stale_exception_returns_empty(self) -> None:
        g = _make_graph({})
        with patch.object(g, "stale", side_effect=RuntimeError("boom")):
            self.assertEqual(check_freshness(g), [])

# -- Partial coverage tests --------------------------------------------------

class PartialCoverageTest(unittest.TestCase):
    """check_partial_coverage emits [partial] warnings for coverage gaps."""

    def test_no_interfaces_no_warnings(self) -> None:
        self.assertEqual(check_partial_coverage([], []), [])

    def test_balanced_http_no_warning(self) -> None:
        interfaces = [
            {"id": "rpc:get", "props": {
                "protocol": "http", "boundary_kind": "inbound"}},
            {"id": "rpc:call", "props": {
                "protocol": "http", "boundary_kind": "outbound"}},
        ]
        self.assertEqual(check_partial_coverage(interfaces, []), [])

    def test_http_server_only_warns(self) -> None:
        interfaces = [
            {"id": "rpc:get", "props": {
                "protocol": "http", "boundary_kind": "inbound"}},
        ]
        warnings = check_partial_coverage(interfaces, [])
        self.assertEqual(len(warnings), 1)
        self.assertTrue(warnings[0].startswith("[partial]"))
        self.assertIn("http", warnings[0])
        self.assertIn("server-side", warnings[0])

    def test_http_client_only_warns(self) -> None:
        interfaces = [
            {"id": "rpc:call", "props": {
                "protocol": "http", "boundary_kind": "outbound"}},
        ]
        warnings = check_partial_coverage(interfaces, [])
        self.assertEqual(len(warnings), 1)
        self.assertIn("client-side", warnings[0])

    def test_grpc_proto_without_bindings_warns(self) -> None:
        interfaces = [
            {"id": "rpc:method", "props": {
                "protocol": "grpc", "boundary_kind": "inbound"}},
        ]
        warnings = check_partial_coverage(interfaces, [])
        has_bindings_warning = any(
            "bindings" in w for w in warnings
        )
        self.assertTrue(has_bindings_warning)

    def test_grpc_with_bindings_no_bindings_warning(self) -> None:
        interfaces = [
            {"id": "rpc:method", "props": {
                "protocol": "grpc", "boundary_kind": "inbound"}},
            {"id": "rpc:stub", "props": {
                "protocol": "grpc", "boundary_kind": "internal"}},
        ]
        warnings = check_partial_coverage(interfaces, [])
        bindings_warnings = [w for w in warnings if "bindings" in w]
        self.assertEqual(len(bindings_warnings), 0)

    def test_nodes_without_protocol_ignored(self) -> None:
        interfaces = [
            {"id": "rpc:generic", "props": {}},
        ]
        self.assertEqual(check_partial_coverage(interfaces, []), [])

    def test_boundaries_included_in_check(self) -> None:
        boundaries = [
            {"id": "boundary:api", "props": {
                "protocol": "http", "boundary_kind": "inbound"}},
        ]
        warnings = check_partial_coverage([], boundaries)
        self.assertTrue(len(warnings) > 0)
        self.assertIn("http", warnings[0])

# -- Confidence gap tests ----------------------------------------------------

class ConfidenceGapTest(unittest.TestCase):
    """check_confidence_gaps warns when most nodes are speculative."""

    def test_empty_list_no_warnings(self) -> None:
        self.assertEqual(check_confidence_gaps([]), [])

    def test_all_definite_no_warning(self) -> None:
        nodes = [
            {"id": "a", "props": {"confidence": "definite"}},
            {"id": "b", "props": {"confidence": "definite"}},
        ]
        self.assertEqual(check_confidence_gaps(nodes), [])

    def test_majority_speculative_warns(self) -> None:
        nodes = [
            {"id": "a", "props": {"confidence": "speculative"}},
            {"id": "b", "props": {"confidence": "speculative"}},
            {"id": "c", "props": {"confidence": "definite"}},
        ]
        warnings = check_confidence_gaps(nodes)
        self.assertEqual(len(warnings), 1)
        self.assertTrue(warnings[0].startswith("[partial]"))
        self.assertIn("2/3", warnings[0])

    def test_exactly_half_no_warning(self) -> None:
        nodes = [
            {"id": "a", "props": {"confidence": "speculative"}},
            {"id": "b", "props": {"confidence": "definite"}},
        ]
        self.assertEqual(check_confidence_gaps(nodes), [])

    def test_missing_confidence_not_counted(self) -> None:
        nodes = [
            {"id": "a", "props": {}},
            {"id": "b", "props": {}},
        ]
        self.assertEqual(check_confidence_gaps(nodes), [])

# -- Integration: brief + trace with warnings --------------------------------

class BriefWarningsIntegrationTest(unittest.TestCase):
    """brief() emits partial-coverage and freshness warnings."""

    def test_brief_emits_partial_coverage_warning(self) -> None:
        from weld.brief import brief  # noqa: E402

        nodes = {
            "rpc:get-user": {
                "type": "rpc", "label": "get user endpoint",
                "props": {
                    "protocol": "http",
                    "boundary_kind": "inbound",
                    "authority": "canonical",
                    "confidence": "definite",
                },
            },
        }
        g = _make_graph(nodes)
        result = brief(g, "endpoint")
        partial_warnings = [
            w for w in result["warnings"] if w.startswith("[partial]")
        ]
        self.assertTrue(
            len(partial_warnings) > 0,
            f"Expected [partial] warning, got: {result['warnings']}",
        )

    def test_brief_no_partial_warning_when_balanced(self) -> None:
        from weld.brief import brief  # noqa: E402

        nodes = {
            "rpc:get-user": {
                "type": "rpc", "label": "get user http endpoint",
                "props": {
                    "protocol": "http",
                    "boundary_kind": "inbound",
                    "authority": "canonical",
                    "confidence": "definite",
                },
            },
            "rpc:call-user": {
                "type": "rpc", "label": "call user http endpoint",
                "props": {
                    "protocol": "http",
                    "boundary_kind": "outbound",
                    "authority": "canonical",
                    "confidence": "definite",
                },
            },
        }
        g = _make_graph(nodes)
        result = brief(g, "endpoint")
        partial_warnings = [
            w for w in result["warnings"] if w.startswith("[partial]")
        ]
        self.assertEqual(partial_warnings, [])

class TraceWarningsIntegrationTest(unittest.TestCase):
    """trace() emits partial-coverage and freshness warnings."""

    def test_trace_emits_partial_coverage_warning(self) -> None:
        from weld.trace import trace  # noqa: E402

        nodes = {
            "service:orders": {
                "type": "service", "label": "orders service",
                "props": {
                    "authority": "canonical",
                    "confidence": "definite",
                },
            },
            "rpc:create": {
                "type": "rpc", "label": "create order",
                "props": {
                    "protocol": "grpc",
                    "boundary_kind": "inbound",
                    "authority": "canonical",
                    "confidence": "definite",
                },
            },
        }
        edges = [
            {"from": "service:orders", "to": "rpc:create",
             "type": "exposes", "props": {}},
        ]
        g = _make_graph(nodes, edges)
        result = trace(g, term="orders")
        partial_warnings = [
            w for w in result["warnings"] if w.startswith("[partial]")
        ]
        self.assertTrue(
            len(partial_warnings) > 0,
            f"Expected [partial] warning, got: {result['warnings']}",
        )

if __name__ == "__main__":
    unittest.main()
