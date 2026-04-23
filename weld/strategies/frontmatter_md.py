"""Strategy: Agent definitions from markdown with YAML frontmatter."""

from __future__ import annotations

from pathlib import Path

from weld.strategies._helpers import StrategyResult, filter_glob_results, should_skip

def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract agent definitions from markdown with YAML frontmatter."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source["glob"]
    excludes = source.get("exclude", [])
    parent = (root / pattern).parent
    if not parent.is_dir():
        return StrategyResult(nodes, edges, discovered_from)
    discovered_from.append(str(parent.relative_to(root)) + "/")

    for md in filter_glob_results(root, sorted(parent.glob(Path(pattern).name)), excludes=excludes):
        if should_skip(md, excludes, root=root):
            continue
        rel_path = str(md.relative_to(root))
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
