"""Strategy: Agent definitions from markdown with YAML frontmatter."""

from __future__ import annotations

from pathlib import Path

from weld._rel_path import rel_to_root
from weld.strategies._glob_resolve import resolve_glob
from weld.strategies._helpers import StrategyResult

def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract agent definitions from markdown with YAML frontmatter."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source["glob"]
    excludes = source.get("exclude", [])
    for md in resolve_glob(root, pattern, excludes):
        rel_path = rel_to_root(md, root)
        # Per-file provenance, recorded before the read -- see StrategyResult
        # (bd 8ia5). The old ``parent``-derived entry was ``"./"`` for any
        # root-anchored glob, which marks the whole tree as tracked source.
        discovered_from.append(rel_path)
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
        name = md.stem
        description = ""
        model = ""
        if text.startswith("---"):
            end = text.find("---", 3)
            if end != -1:
                frontmatter = text[3:end]
                for line in frontmatter.splitlines():
                    line = line.strip()
                    if line.startswith("name:"):
                        name = line.split(":", 1)[1].strip()
                    elif line.startswith("description:"):
                        description = line.split(":", 1)[1].strip()
                    elif line.startswith("model:"):
                        model = line.split(":", 1)[1].strip()

        nid = f"agent:{name}"
        nodes[nid] = {
            "type": "agent",
            "label": name,
            "props": {
                "file": rel_path,
                "description": description,
                "model": model,
                "source_strategy": "frontmatter_md",
                "authority": "canonical",
                "confidence": "definite",
                "roles": ["config"],
            },
        }

    return StrategyResult(nodes, edges, discovered_from)
