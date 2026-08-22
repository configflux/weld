"""Shared fixtures for the ``wd enrich --agent-direct`` tests (ADR 0098).

Split out the way ``_impact_test_helpers`` is, so the plan-builder tests
and the CLI-mode tests describe the same graph without either file
owning the other's setup. Every ``py_test`` target that reads the
fixture lists this module in ``srcs``.

The fixture deliberately mixes node types and includes one node with no
``props.file`` (``concept:Checkout``): "this node has no source file" is
a case the plan must render as an instruction, not as a blank column.

``file:app/main`` carries a ``props.aliases`` entry so the fixture can
exercise ADR 0041 lookup compatibility: a node id pasted from an older
transcript must still name the node it named then. The alias sits on the
shared fixture rather than a private one because both enrichment paths
have to agree about it, and they read this graph.
"""

from __future__ import annotations

import json
from pathlib import Path

from weld.graph import Graph
from weld.providers import EnrichmentResult

#: A structurally complete manual record -- what an agent following the
#: emitted contract writes.
VALID_RECORD = {
    "provider": "manual",
    "model": "agent-reviewed",
    "timestamp": "2026-08-13T00:00:00+00:00",
    "description": "Already reviewed.",
}


def nodes() -> dict[str, dict]:
    """Return a fresh (mutable) fixture node set."""
    return {
        "entity:Store": {
            "type": "entity",
            "label": "Store",
            "props": {"file": "store.py"},
        },
        "entity:Cart": {
            "type": "entity",
            "label": "Cart",
            "props": {"file": "cart.py"},
        },
        "file:app/main": {
            "type": "file",
            "label": "main",
            "props": {"file": "app/main.py", "aliases": ["file:main"]},
        },
        "concept:Checkout": {
            "type": "concept",
            "label": "Checkout",
            "props": {},
        },
    }


def write_graph(root: Path, node_map: dict[str, dict]) -> Graph:
    """Write *node_map* to ``<root>/.weld/graph.json`` and return it loaded."""
    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    (weld_dir / "graph.json").write_text(
        json.dumps(
            {
                "meta": {"version": 4, "updated_at": "2026-08-13T00:00:00+00:00"},
                "nodes": node_map,
                "edges": [],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    graph = Graph(root)
    graph.load()
    return graph


class StubProvider:
    """Enriches every node handed to it, so ``enriched`` == the selection."""

    DEFAULT_MODEL = "stub-model"

    def enrich(self, node: dict, neighbors: list[dict], *, model: str):
        return EnrichmentResult(
            description=f"desc for {node['id']}",
            purpose=None,
            complexity_hint=None,
            suggested_tags=[],
            tokens_used=0,
            cost_usd=0.0,
        )
