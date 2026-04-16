"""Strategy: Worker stage exports from __init__.py files."""

from __future__ import annotations

import ast
from pathlib import Path

from weld.strategies._helpers import StrategyResult, extract_all, filter_glob_results

def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract worker stage exports from __init__.py files."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source["glob"]
    worker_dir = (root / pattern).parent
    if not worker_dir.is_dir():
        return StrategyResult(nodes, edges, discovered_from)
    discovered_from.append(str(worker_dir.relative_to(root)) + "/")

    for stage_dir in filter_glob_results(root, sorted(worker_dir.iterdir())):
        if not stage_dir.is_dir():
            continue
        init_py = stage_dir / "__init__.py"
        if not init_py.exists():
            continue
        try:
            tree = ast.parse(
                init_py.read_text(encoding="utf-8"), filename=str(init_py)
            )
        except SyntaxError:
            continue
        rel_path = str(init_py.relative_to(root))
        exports = extract_all(tree)
        stage_name = stage_dir.name
        nid = f"stage:{stage_name}"
        nodes[nid] = {
            "type": "stage",
            "label": stage_name.title(),
            "props": {
                "file": rel_path,
                "exports": exports,
                "source_strategy": "worker_stage",
                "authority": "canonical",
                "confidence": "definite",
                "roles": ["implementation"],
            },
        }

    return StrategyResult(nodes, edges, discovered_from)
