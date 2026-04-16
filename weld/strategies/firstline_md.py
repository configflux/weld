"""Strategy: Command definitions from markdown (first content line).

Note: The cross-strategy dependency (detecting agent invocations) is
handled by the orchestrator in post-processing, not by this strategy.
This strategy only extracts command nodes.
"""

from __future__ import annotations

from pathlib import Path

from weld.strategies._helpers import StrategyResult, filter_glob_results, should_skip

def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract command definitions from markdown (first content line = description)."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source["glob"]
    excludes = source.get("exclude", [])
    parent = (root / pattern).parent
    if not parent.is_dir():
        return StrategyResult(nodes, edges, discovered_from)
    discovered_from.append(str(parent.relative_to(root)) + "/")

    for md in filter_glob_results(root, sorted(parent.glob(Path(pattern).name))):
        if should_skip(md, excludes):
            continue
        rel_path = str(md.relative_to(root))
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
        # Skip frontmatter if present
        content = text
        if text.startswith("---"):
            end = text.find("---", 3)
            if end != -1:
                content = text[end + 3:].strip()
        # First non-empty line is description
        description = ""
        for line in content.splitlines():
            line = line.strip()
            if line:
                description = line
                break

        name = md.stem
        nid = f"command:{name}"
        nodes[nid] = {
            "type": "command",
            "label": name,
            "props": {
                "file": rel_path,
                "description": description,
                "source_strategy": "firstline_md",
                "authority": "canonical",
                "confidence": "definite",
                "roles": ["config"],
            },
        }
        # Store full text in context for orchestrator invokes detection
        context.setdefault("command_texts", {})[nid] = text

    return StrategyResult(nodes, edges, discovered_from)
