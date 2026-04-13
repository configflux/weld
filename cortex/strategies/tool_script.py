"""Strategy: Tool scripts with language detection from shebang."""

from __future__ import annotations

from pathlib import Path

from cortex.strategies._helpers import StrategyResult, filter_glob_results, should_skip

def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract tool scripts with language detection from shebang."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source["glob"]
    excludes = source.get("exclude", [])
    parent = (root / pattern).parent
    if not parent.is_dir():
        return StrategyResult(nodes, edges, discovered_from)
    discovered_from.append(str(parent.relative_to(root)) + "/")

    for path in filter_glob_results(root, sorted(parent.glob(Path(pattern).name))):
        if not path.is_file():
            continue
        if should_skip(path, excludes):
            continue
        rel_path = str(path.relative_to(root))
        lang = "unknown"
        if path.suffix == ".py":
            lang = "python"
        elif path.suffix == ".sh":
            lang = "bash"
        else:
            try:
                first_line = path.read_text(encoding="utf-8").split("\n", 1)[0]
                if "python" in first_line:
                    lang = "python"
                elif "bash" in first_line or "sh" in first_line:
                    lang = "bash"
            except (OSError, UnicodeDecodeError):
                continue

        name = path.stem.replace(".", "_")
        nid = f"tool:{name}"
        nodes[nid] = {
            "type": "tool",
            "label": path.name,
            "props": {
                "file": rel_path,
                "lang": lang,
                "source_strategy": "tool_script",
                "authority": "canonical",
                "confidence": "definite",
                "roles": ["script"],
            },
        }

    return StrategyResult(nodes, edges, discovered_from)
