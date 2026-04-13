"""Custom strategy: extract TODO/FIXME comments from Python files.

This is a project-local strategy that demonstrates the cortex plugin
pattern.  Place this file in ``.cortex/strategies/`` and reference it
by name in ``discover.yaml``::

    sources:
      - strategy: todo_comment
        glob: "*.py"

The strategy scans each matching file for lines containing TODO or
FIXME markers and emits a graph node for each one.
"""

from __future__ import annotations

import re
from pathlib import Path

from cortex.strategies._helpers import StrategyResult

# Pattern matches "# TODO: ..." or "# FIXME: ..." (with optional colon).
_TODO_RE = re.compile(
    r"#\s*(TODO|FIXME):?\s*(.*)",
    re.IGNORECASE,
)


def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract TODO/FIXME comment nodes from Python files.

    Parameters
    ----------
    root
        Absolute path to the repository root.
    source
        The source entry from ``discover.yaml``.
    context
        Shared context dict (unused by this strategy).

    Returns
    -------
    StrategyResult
        Nodes for each TODO/FIXME comment found.
    """
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source.get("glob", "")
    if not pattern:
        return StrategyResult(nodes, edges, discovered_from)

    for filepath in sorted(root.glob(pattern)):
        if not filepath.is_file():
            continue
        rel = str(filepath.relative_to(root))
        discovered_from.append(rel)

        try:
            lines = filepath.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue

        for line_no, line in enumerate(lines, start=1):
            match = _TODO_RE.search(line)
            if not match:
                continue

            kind = match.group(1).upper()
            text = match.group(2).strip()
            safe_name = rel.replace("/", "_").replace(".", "_")
            node_id = f"todo:{safe_name}:{line_no}"

            nodes[node_id] = {
                "type": "concept",
                "label": f"{kind}: {text}" if text else kind,
                "props": {
                    "file": rel,
                    "line": line_no,
                    "kind": kind,
                    "source_strategy": "todo_comment",
                    "authority": "canonical",
                    "confidence": "definite",
                },
            }

    return StrategyResult(nodes, edges, discovered_from)
