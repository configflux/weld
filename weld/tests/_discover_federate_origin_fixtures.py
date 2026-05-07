"""Shared fixture builders for federated origin re-tagging tests.

Used by ``discover_federate_origin_test`` (Python branch) and
``discover_federate_cpp_origin_test`` (C++ branch + cross-language
isolation + multi-language disk pass). The builders mirror the shape
the discover pipeline actually emits so a test failure points at a
real per-language regression, not a stub mismatch.
"""

from __future__ import annotations

import json
from pathlib import Path

from weld.contract import SCHEMA_VERSION
from weld.workspace_state import WorkspaceChildState, WorkspaceState


def state_present(names: list[str]) -> WorkspaceState:
    """Build a ``WorkspaceState`` with every *names* entry marked present."""
    return WorkspaceState(
        children={
            name: WorkspaceChildState(
                status="present",
                head_sha=None,
                head_ref=None,
                is_dirty=False,
                graph_path=f"{name}/.weld/graph.json",
                graph_sha256=None,
                last_seen_utc="2026-05-04T00:00:00+00:00",
            )
            for name in names
        },
    )


def write_child_graph(root: Path, rel_path: str, payload: dict) -> Path:
    """Write *payload* to ``<root>/<rel_path>/.weld/graph.json``."""
    weld_dir = root / rel_path / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    graph_path = weld_dir / "graph.json"
    graph_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return graph_path


def empty_graph(nodes: dict[str, dict] | None = None) -> dict:
    """Return a minimal valid graph dict with optional *nodes*."""
    return {
        "meta": {"version": SCHEMA_VERSION, "schema_version": 1},
        "nodes": nodes or {},
        "edges": [],
    }


def python_project_symbol(module: str, qualname: str) -> tuple[str, dict]:
    """Build a child-shaped Python project symbol node entry."""
    sid = f"symbol:py:{module}:{qualname}"
    body = {
        "type": "symbol",
        "label": qualname,
        "props": {
            "module": module,
            "qualname": qualname,
            "language": "python",
            "source_strategy": "python_callgraph",
            "authority": "derived",
            "confidence": "definite",
            "roles": ["implementation"],
            "origin": "project",
        },
    }
    return sid, body


def python_external_target(module: str, qualname: str) -> tuple[str, dict]:
    """Build a speculative ``external``-tagged Python target node."""
    sid = f"symbol:py:{module}:{qualname}"
    body = {
        "type": "symbol",
        "label": qualname,
        "props": {
            "module": module,
            "qualname": qualname,
            "language": "python",
            "source_strategy": "python_callgraph",
            "authority": "derived",
            "confidence": "speculative",
            "roles": ["implementation"],
            "origin": "external",
        },
    }
    return sid, body


def cpp_project_symbol(module: str, qualname: str) -> tuple[str, dict]:
    """Build a child-shaped C++ project symbol node entry.

    Mirrors what ``_ts_call_graph.extract_call_edges`` emits at layer 1
    for a project file: ``language="cpp"``, ``origin="project"``, and a
    dotted ``module`` derived from the relative path.
    """
    sid = f"symbol:cpp:{module}:{qualname}"
    body = {
        "type": "symbol",
        "label": qualname,
        "props": {
            "module": module,
            "qualname": qualname,
            "language": "cpp",
            "source_strategy": "tree_sitter",
            "authority": "derived",
            "confidence": "definite",
            "roles": ["implementation"],
            "origin": "project",
        },
    }
    return sid, body


def cpp_external_target(module: str, qualname: str) -> tuple[str, dict]:
    """Build a C++ external-tagged target symbol node entry.

    Mirrors what ``cpp_resolver.resolve_includes_pass`` produces when
    a layer-2 rewrite resolves a callee to a header outside the local
    project tree (``classify_resolved_include`` returns ``external``).
    """
    sid = f"symbol:cpp:{module}:{qualname}"
    body = {
        "type": "symbol",
        "label": qualname,
        "props": {
            "module": module,
            "qualname": qualname,
            "language": "cpp",
            "source_strategy": "tree_sitter",
            "authority": "derived",
            "confidence": "definite",
            "roles": ["implementation"],
            "origin": "external",
        },
    }
    return sid, body
