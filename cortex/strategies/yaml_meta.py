"""Strategy: CI workflow metadata from YAML files."""

from __future__ import annotations

import re
from pathlib import Path

from cortex.strategies._helpers import StrategyResult, filter_glob_results, should_skip

def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract CI workflow metadata from YAML files."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source["glob"]
    excludes = source.get("exclude", [])
    parent = (root / pattern).parent
    if not parent.is_dir():
        return StrategyResult(nodes, edges, discovered_from)
    discovered_from.append(str(parent.relative_to(root)) + "/")

    for yml in filter_glob_results(root, sorted(parent.glob(Path(pattern).name))):
        if should_skip(yml, excludes):
            continue
        rel_path = str(yml.relative_to(root))
        try:
            text = yml.read_text(encoding="utf-8")
        except OSError:
            continue
        name = yml.stem
        triggers: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("name:"):
                name = stripped.split(":", 1)[1].strip().strip('"').strip("'")
            elif stripped.startswith("on:"):
                val = stripped.split(":", 1)[1].strip()
                if val:
                    triggers.append(val)
            elif re.match(
                r"^\s+(push|pull_request|workflow_dispatch|schedule):", stripped
            ):
                triggers.append(stripped.strip().rstrip(":"))

        nid = f"workflow:{yml.stem}"
        nodes[nid] = {
            "type": "workflow",
            "label": name,
            "props": {
                "file": rel_path,
                "triggers": triggers,
                "source_strategy": "yaml_meta",
                "authority": "canonical",
                "confidence": "definite",
                "roles": ["config"],
            },
        }

    return StrategyResult(nodes, edges, discovered_from)
