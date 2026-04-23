"""Strategy: GitHub Actions workflow extraction with rich metadata.

Parses .github/workflows/*.yml files to extract workflow nodes with
triggers, jobs, permissions, and concurrency metadata.  Provides richer
coverage than the generic yaml_meta strategy for CI/CD workflows.

"""

from __future__ import annotations

import re
from pathlib import Path

from weld.strategies._helpers import StrategyResult, filter_glob_results, should_skip

# -- Line-based YAML parsers ------------------------------------------------
# We deliberately avoid a full YAML parser dependency.  GH Actions workflow
# files have a predictable structure that line-based parsing handles
# reliably.

def _parse_workflow(text: str) -> dict:
    """Parse a GitHub Actions workflow YAML into structured metadata.

    Returns a dict with keys: name, triggers, jobs, permissions, concurrency.
    """
    name: str = ""
    triggers: list[str] = []
    jobs: list[str] = []
    permissions: list[str] = []
    concurrency: str = ""

    in_on = False
    in_jobs = False
    in_permissions = False
    in_concurrency = False

    for line in text.splitlines():
        stripped = line.strip()

        # Top-level name
        if line.startswith("name:"):
            name = stripped.split(":", 1)[1].strip().strip('"').strip("'")
            continue

        # Detect top-level sections (no leading whitespace)
        if not line.startswith(" ") and not line.startswith("\t"):
            # Reset section trackers
            if stripped.startswith("on:"):
                in_on = True
                in_jobs = False
                in_permissions = False
                in_concurrency = False
                # Inline on: value (e.g., "on: push")
                val = stripped.split(":", 1)[1].strip()
                if val:
                    triggers.append(val)
                continue
            elif stripped.startswith("jobs:"):
                in_on = False
                in_jobs = True
                in_permissions = False
                in_concurrency = False
                continue
            elif stripped.startswith("permissions:"):
                in_on = False
                in_jobs = False
                in_permissions = True
                in_concurrency = False
                # Inline permissions: read-all / write-all
                val = stripped.split(":", 1)[1].strip()
                if val:
                    permissions.append(val)
                continue
            elif stripped.startswith("concurrency:"):
                in_on = False
                in_jobs = False
                in_permissions = False
                in_concurrency = True
                val = stripped.split(":", 1)[1].strip()
                if val:
                    concurrency = val
                continue
            elif stripped and not stripped.startswith("#"):
                # Any other top-level key resets tracking
                in_on = False
                in_jobs = False
                in_permissions = False
                in_concurrency = False
                continue

        # Parse within sections
        if in_on and stripped and not stripped.startswith("#"):
            # Trigger names are indented keys like "  push:" or "  pull_request:"
            m = re.match(r"^  (\w[\w-]*):", line)
            if m:
                triggers.append(m.group(1))

        if in_jobs and stripped and not stripped.startswith("#"):
            # Job names are 2-space indented keys like "  build:"
            m = re.match(r"^  (\w[\w-]*):", line)
            if m:
                jobs.append(m.group(1))

        if in_permissions and stripped and not stripped.startswith("#"):
            # Permission lines like "  contents: read"
            m = re.match(r"^  (\w[\w-]*):\s*(.+)", line)
            if m:
                permissions.append(f"{m.group(1)}: {m.group(2).strip()}")

        if in_concurrency and stripped and not stripped.startswith("#"):
            m = re.match(r"^  group:\s*(.+)", line)
            if m:
                concurrency = m.group(1).strip()

    return {
        "name": name,
        "triggers": triggers,
        "jobs": jobs,
        "permissions": permissions,
        "concurrency": concurrency,
    }

def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract GitHub Actions workflow nodes with rich metadata."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source.get("glob", "")
    excludes = source.get("exclude", [])

    if not pattern:
        return StrategyResult(nodes, edges, discovered_from)

    parent = (root / pattern).parent
    if not parent.is_dir():
        return StrategyResult(nodes, edges, discovered_from)

    for yml in filter_glob_results(root, sorted(parent.glob(Path(pattern).name)), excludes=excludes):
        if should_skip(yml, excludes, root=root):
            continue

        rel_path = str(yml.relative_to(root))
        discovered_from.append(rel_path)

        try:
            text = yml.read_text(encoding="utf-8")
        except OSError:
            continue

        parsed = _parse_workflow(text)

        # Use parsed name or fall back to stem
        wf_name = parsed["name"] or yml.stem

        # Skip files that do not look like valid workflow files
        if not parsed["name"] and not parsed["triggers"] and not parsed["jobs"]:
            continue

        nid = f"workflow:{yml.stem}"
        nodes[nid] = {
            "type": "workflow",
            "label": wf_name,
            "props": {
                "file": rel_path,
                "triggers": parsed["triggers"],
                "jobs": parsed["jobs"],
                "permissions": parsed["permissions"],
                "concurrency": parsed["concurrency"] or None,
                "source_strategy": "gh_workflow",
                "authority": "canonical",
                "confidence": "definite",
                "roles": ["config"],
            },
        }

    return StrategyResult(nodes, edges, discovered_from)
