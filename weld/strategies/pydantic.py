"""Strategy: Pydantic BaseModel contracts and contract-level enums."""

from __future__ import annotations

import ast
from pathlib import Path

from weld.strategies._helpers import (
    StrategyResult,
    enum_members,
    extract_contracts,
    filter_glob_results,
    inherits,
)

def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract Pydantic BaseModel contracts and contract-level enums."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source["glob"]
    contracts_dir = (root / pattern).parent
    if not contracts_dir.is_dir():
        return StrategyResult(nodes, edges, discovered_from)
    discovered_from.append(str(contracts_dir.relative_to(root)) + "/")

    for py in filter_glob_results(root, sorted(contracts_dir.glob(Path(pattern).name))):
        if py.name.startswith("_"):
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except SyntaxError:
            continue
        rel_path = str(py.relative_to(root))
        for contract in extract_contracts(tree):
            nid = f"contract:{contract['name']}"
            nodes[nid] = {
                "type": "contract",
                "label": contract["name"],
                "props": {
                    "file": rel_path,
                    "fields": contract["fields"],
                    "description": contract["docstring"],
                    "source_strategy": "pydantic",
                    "authority": "canonical",
                    "confidence": "definite",
                    "roles": ["implementation"],
                },
            }
        # Also extract contract-level StrEnum definitions
        for cls_node in (n for n in tree.body if isinstance(n, ast.ClassDef)):
            if inherits(cls_node, "StrEnum"):
                members = enum_members(cls_node)
                nid = f"enum:{cls_node.name}"
                if nid not in nodes:
                    nodes[nid] = {
                        "type": "enum",
                        "label": cls_node.name,
                        "props": {
                            "file": rel_path,
                            "members": members,
                            "source_strategy": "pydantic",
                            "authority": "canonical",
                            "confidence": "definite",
                            "roles": ["implementation"],
                        },
                    }

    return StrategyResult(nodes, edges, discovered_from)
