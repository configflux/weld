"""Test fixtures and helpers for ``weld_wiki_export_test.py``.

Extracted into a peer module to keep the test file under the 400-line
cap (CLAUDE.md line-count policy).
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.contract import SCHEMA_VERSION  # noqa: E402
from weld.graph import Graph  # noqa: E402

FIXTURE_NODES: dict[str, dict] = {
    "file:weld/cli.py": {
        "type": "file",
        "label": "weld/cli.py",
        "props": {
            "file": "weld/cli.py",
            "origin": "project",
            "description": "CLI dispatcher for wd.",
        },
    },
    "symbol:weld.cli.main": {
        "type": "symbol",
        "label": "main",
        "props": {
            "file": "weld/cli.py",
            "origin": "project",
            "span": "1-407",
        },
    },
    "entity:Store": {
        "type": "entity",
        "label": "Store",
        "props": {"file": "domain/store.py", "origin": "project"},
    },
    "entity:Offer": {
        "type": "entity",
        "label": "Offer",
        "props": {"file": "domain/offer.py", "origin": "project"},
    },
    "package:requests": {
        "type": "package",
        "label": "requests",
        "props": {"origin": "external"},
    },
}

FIXTURE_EDGES: list[dict] = [
    {
        "from": "file:weld/cli.py",
        "to": "symbol:weld.cli.main",
        "type": "contains",
        "props": {"confidence": "definite", "source_strategy": "python_ast"},
    },
    {
        "from": "entity:Offer",
        "to": "entity:Store",
        "type": "depends_on",
        "props": {"confidence": "definite", "source_strategy": "python_ast"},
    },
    {
        "from": "symbol:weld.cli.main",
        "to": "package:requests",
        "type": "calls",
        "props": {
            "confidence": "speculative",
            "source_strategy": "anthropic_enrichment",
        },
    },
]


def make_graph_root(
    nodes: dict[str, dict] | None = None,
    edges: list[dict] | None = None,
) -> Path:
    """Write a graph.json fixture to a fresh temp dir and return the root."""
    tmp = Path(tempfile.mkdtemp())
    (tmp / ".weld").mkdir(parents=True, exist_ok=True)
    (tmp / ".weld" / "graph.json").write_text(
        json.dumps(
            {
                "meta": {
                    "version": SCHEMA_VERSION,
                    "git_sha": "deadbeef",
                    "updated_at": "2026-05-10T00:00:00+00:00",
                },
                "nodes": nodes if nodes is not None else FIXTURE_NODES,
                "edges": edges if edges is not None else FIXTURE_EDGES,
            }
        ),
        encoding="utf-8",
    )
    return tmp


def load_fixture_graph() -> Graph:
    g = Graph(make_graph_root())
    g.load()
    return g


def load_id_map(output_dir: Path) -> dict[str, str]:
    """Return the ``ids`` projection from ``.id-map.json``."""
    payload = json.loads(
        (output_dir / ".id-map.json").read_text(encoding="utf-8")
    )
    return payload["ids"]


__all__ = [
    "FIXTURE_EDGES",
    "FIXTURE_NODES",
    "load_fixture_graph",
    "load_id_map",
    "make_graph_root",
]
