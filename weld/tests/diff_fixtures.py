"""Shared fixtures for graph diff tests."""

from __future__ import annotations

import json
from pathlib import Path

from weld.contract import SCHEMA_VERSION


def base_graph() -> dict:
    """Return a minimal baseline graph."""
    return {
        "meta": {
            "version": SCHEMA_VERSION,
            "git_sha": "aaa111",
            "updated_at": "2026-04-13T00:00:00+00:00",
        },
        "nodes": {
            "entity:Store": {
                "type": "entity",
                "label": "Store",
                "props": {
                    "file": "models/store.py",
                    "exports": ["Store"],
                },
            },
            "entity:Offer": {
                "type": "entity",
                "label": "Offer",
                "props": {
                    "file": "models/offer.py",
                    "exports": ["Offer"],
                },
            },
            "route:GET:/stores": {
                "type": "route",
                "label": "list_stores",
                "props": {
                    "file": "routes/stores.py",
                    "exports": ["list_stores"],
                },
            },
        },
        "edges": [
            {
                "from": "entity:Offer",
                "to": "entity:Store",
                "type": "depends_on",
                "props": {},
            },
            {
                "from": "route:GET:/stores",
                "to": "entity:Store",
                "type": "responds_with",
                "props": {},
            },
        ],
    }


def write_graphs(root: Path, previous: dict | None, current: dict) -> None:
    """Write previous and current graph files into a temp .weld dir."""
    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    weld_dir.joinpath("graph.json").write_text(
        json.dumps(current, indent=2), encoding="utf-8"
    )
    if previous is not None:
        weld_dir.joinpath("graph-previous.json").write_text(
            json.dumps(previous, indent=2), encoding="utf-8"
        )
