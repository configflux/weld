"""Shared fixtures for the sqlite-backed query tests (ADR 0058 Option B).

Split out so the per-test modules stay under the line-count cap. Holds the
sidecar/JSON graph builders and the synthetic node graphs the
``weld_sqlite_query_test`` (envelope + security) and
``weld_sqlite_query_parity_test`` (coverage admission + OR-fallback) modules
both consume. Keeping one copy of the builders means the two surfaces cannot
drift on how a sidecar is materialized.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from weld import _sqlite_reader as reader
from weld import _sqlite_writer as writer
from weld.graph import Graph
from weld.serializer import dumps_graph

# Coverage-trap node ids (boundary_entrypoint family) shared by the parity
# tests and the union-binding security probe.
QUERY = "boundary entrypoint strategy test"
STRATEGY_NODE = "file:weld/strategies/boundary_entrypoint"
TEST_NODE = "file:weld/tests/weld_boundary_entrypoint_test"
DOC_NODE = "doc:docs/determinism-audit-T1a"


def fixture_nodes() -> dict[str, dict]:
    """Mixed-topic fixture with distinguishable token surfaces."""
    return {
        "service:billing": {
            "type": "service",
            "label": "billing",
            "props": {
                "file": "services/billing.py",
                "description": "Billing rollup",
                "exports": ["bill", "charge"],
            },
        },
        "service:auth": {
            "type": "service",
            "label": "auth",
            "props": {
                "file": "services/auth.py",
                "description": "Auth surface",
                "exports": ["login", "logout"],
            },
        },
        "symbol:helper": {
            "type": "symbol",
            "label": "helper",
            "props": {
                "description": "Generic helper used by billing",
            },
        },
        "file:readme": {
            "type": "file",
            "label": "README.md",
            "props": {"file": "README.md"},
        },
    }


def open_sqlite_view(nodes: dict[str, dict]) -> tuple[
    reader.SqliteBackedGraph, "tempfile.TemporaryDirectory[str]",
]:
    """Build a fresh sidecar over *nodes* and open it as a SqliteBackedGraph."""
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    graph = {"meta": {"schema_version": 1}, "nodes": nodes, "edges": []}
    body = dumps_graph(graph).encode("utf-8")
    (root / ".weld" / "graph.json").write_bytes(body)
    writer.build_sidecar_for_bytes(graph, body, root / ".weld" / "graph.db")
    view = reader.open_sidecar_if_fresh(root / ".weld" / "graph.json")
    assert view is not None
    return view, tmp


def open_json_graph(nodes: dict[str, dict]) -> tuple[
    Graph, "tempfile.TemporaryDirectory[str]",
]:
    """Load *nodes* into a JSON-backed in-memory Graph (parity baseline)."""
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {"version": "0", "updated_at": "t", "schema_version": 1},
        "nodes": nodes,
        "edges": [],
    }
    (root / ".weld" / "graph.json").write_text(
        dumps_graph(payload), encoding="utf-8",
    )
    g = Graph(root)
    g.load()
    return g, tmp


def boundary_trap_nodes() -> dict[str, dict]:
    """Group coverage of ``[boundary][entrypoint][strategy][test]`` (N=4):

    * strategy module -> boundary, entrypoint, strategy (via stem) = 3/4;
    * test target     -> boundary, entrypoint, test               = 3/4;
    * determinism doc -> all four via scattered headings           = 4/4 diffuse.

    Strict-AND admits only the 4/4 doc; the 3/4 code nodes are dropped by the
    intersection -- exactly the impl #1 trap, reproduced on the sqlite path.
    """
    return {
        STRATEGY_NODE: {
            "type": "file", "label": "boundary_entrypoint",
            "props": {"file": "weld/strategies/boundary_entrypoint.py",
                      "authority": "canonical", "confidence": "definite"},
        },
        TEST_NODE: {
            "type": "file", "label": "weld_boundary_entrypoint_test",
            "props": {"file": "weld/tests/weld_boundary_entrypoint_test.py",
                      "authority": "canonical", "confidence": "definite"},
        },
        DOC_NODE: {
            "type": "doc", "label": "Determinism Audit T1A",
            "props": {"file": "docs/determinism-audit-T1a.md",
                      "authority": "canonical", "confidence": "definite",
                      "headings": ["Boundary handling", "Entrypoint ordering",
                                   "Strategy emission order", "Test peer wiring"]},
        },
        "file:weld/unrelated": {
            "type": "file", "label": "unrelated",
            "props": {"file": "weld/unrelated.py"},
        },
    }


def disjoint_token_nodes() -> dict[str, dict]:
    """Graph where each query token hits a *different* node.

    No single node covers both ``discovery`` and ``strategy``, so strict-AND
    yields zero and the query must relax to the OR (per-group union) path.
    The distractor matches neither token and must never surface.
    """
    return {
        "module:discovery": {
            "type": "module",
            "label": "discovery",
            "props": {
                "file": "weld/discovery.py",
                "authority": "canonical",
                "confidence": "definite",
            },
        },
        "module:strategy": {
            "type": "module",
            "label": "strategy",
            "props": {
                "file": "weld/strategy.py",
                "authority": "canonical",
                "confidence": "definite",
            },
        },
        "module:unrelated": {
            "type": "module",
            "label": "unrelated",
            "props": {"file": "weld/unrelated.py"},
        },
    }
