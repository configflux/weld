"""Strategy: Static config file nodes."""

from __future__ import annotations

from pathlib import Path

from cortex.strategies._helpers import StrategyResult
from cortex.repo_boundary import path_within_repo_boundary

def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Create config nodes for explicit file list."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    files = source.get("files", [])

    for filepath in files:
        full = root / filepath
        if not full.exists():
            continue
        if not path_within_repo_boundary(root, full):
            continue
        rel_path = str(full.relative_to(root))
        discovered_from.append(rel_path)
        safe_name = filepath.lstrip(".").replace("/", "_").replace(".", "_")
        nid = f"config:{safe_name}"
        nodes[nid] = {
            "type": "config",
            "label": Path(filepath).name,
            "props": {
                "file": rel_path,
                "source_strategy": "config_file",
                "authority": "canonical",
                "confidence": "definite",
                "roles": ["config"],
            },
        }

    return StrategyResult(nodes, edges, discovered_from)
