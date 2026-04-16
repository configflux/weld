"""Strategy: Generic build manifest extraction (package.json, Makefile, etc.).

Discovers build and verification surfaces from common manifest files:
- package.json: scripts as build/test targets
- Makefile / Justfile: targets as build/test surfaces

"""

from __future__ import annotations

import json
import re
from pathlib import Path

from weld.strategies._helpers import (
    StrategyResult,
    filter_glob_results,
    should_skip,
)

# Script name patterns that indicate test targets
_TEST_SCRIPT_RE = re.compile(r"^(test|check|lint|e2e|spec|coverage|verify)", re.IGNORECASE)
# Script name patterns that indicate build targets
_BUILD_SCRIPT_RE = re.compile(r"^(build|compile|bundle|start|dev|serve|watch)", re.IGNORECASE)

def _extract_package_json(
    root: Path,
    pj_path: Path,
    nodes: dict,
    edges: list,
    discovered_from: list,
) -> None:
    """Extract build/test script targets from a package.json file."""
    rel_path = str(pj_path.relative_to(root))
    discovered_from.append(rel_path)

    try:
        data = json.loads(pj_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return

    if not isinstance(data, dict):
        return

    pkg_name = data.get("name", pj_path.parent.name)
    scripts = data.get("scripts", {})
    if not isinstance(scripts, dict):
        return

    # Create a node for the manifest itself
    safe_name = rel_path.replace("/", "_").replace(".", "_")
    manifest_nid = f"config:{safe_name}"
    nodes[manifest_nid] = {
        "type": "config",
        "label": f"package.json ({pkg_name})",
        "props": {
            "file": rel_path,
            "package_name": pkg_name,
            "source_strategy": "manifest",
            "authority": "canonical",
            "confidence": "definite",
            "roles": ["config"],
        },
    }

    for script_name, script_cmd in scripts.items():
        if not isinstance(script_cmd, str):
            continue

        # Classify the script
        if _TEST_SCRIPT_RE.match(script_name):
            node_type = "test-target"
            role = "test"
        elif _BUILD_SCRIPT_RE.match(script_name):
            node_type = "build-target"
            role = "build"
        else:
            # Skip scripts that are neither build nor test
            continue

        nid = f"{node_type}:npm:{pkg_name}:{script_name}"
        nodes[nid] = {
            "type": node_type,
            "label": f"npm run {script_name}",
            "props": {
                "file": rel_path,
                "script_name": script_name,
                "command": script_cmd,
                "source_strategy": "manifest",
                "authority": "canonical",
                "confidence": "definite",
                "roles": [role],
            },
        }

        # Edge: manifest configures the target
        edges.append({
            "from": manifest_nid,
            "to": nid,
            "type": "configures",
            "props": {
                "source_strategy": "manifest",
                "confidence": "definite",
            },
        })

def _extract_makefile(
    root: Path,
    mk_path: Path,
    nodes: dict,
    edges: list,
    discovered_from: list,
) -> None:
    """Extract make/just targets from a Makefile or Justfile."""
    rel_path = str(mk_path.relative_to(root))
    discovered_from.append(rel_path)

    try:
        text = mk_path.read_text(encoding="utf-8")
    except OSError:
        return

    # Parse make targets: lines matching "target_name:" at column 0
    # Excludes lines starting with tab/space (recipe lines) and comments
    target_re = re.compile(r"^([a-zA-Z_][\w.-]*)\s*:", re.MULTILINE)

    targets = target_re.findall(text)
    if not targets:
        return

    safe_name = rel_path.replace("/", "_").replace(".", "_")
    manifest_nid = f"config:{safe_name}"
    nodes[manifest_nid] = {
        "type": "config",
        "label": mk_path.name,
        "props": {
            "file": rel_path,
            "source_strategy": "manifest",
            "authority": "canonical",
            "confidence": "definite",
            "roles": ["config"],
        },
    }

    for target_name in targets:
        # Skip .PHONY and similar
        if target_name.startswith("."):
            continue

        # Classify
        if _TEST_SCRIPT_RE.match(target_name):
            node_type = "test-target"
            role = "test"
        elif _BUILD_SCRIPT_RE.match(target_name):
            node_type = "build-target"
            role = "build"
        else:
            # Skip targets that don't look like build or test
            continue

        nid = f"{node_type}:make:{mk_path.stem}:{target_name}"
        nodes[nid] = {
            "type": node_type,
            "label": f"make {target_name}",
            "props": {
                "file": rel_path,
                "target_name": target_name,
                "source_strategy": "manifest",
                "authority": "canonical",
                "confidence": "definite",
                "roles": [role],
            },
        }

        edges.append({
            "from": manifest_nid,
            "to": nid,
            "type": "configures",
            "props": {
                "source_strategy": "manifest",
                "confidence": "definite",
            },
        })

def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract build and verification targets from manifest files."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source.get("glob", "")
    excludes = source.get("exclude", [])

    if not pattern:
        return StrategyResult(nodes, edges, discovered_from)

    # Handle recursive globs
    if "**" in pattern:
        matched = sorted(root.glob(pattern))
        matched = filter_glob_results(root, matched)
    else:
        parent = (root / pattern).parent
        if not parent.is_dir():
            return StrategyResult(nodes, edges, discovered_from)
        matched = sorted(parent.glob(Path(pattern).name))

    for filepath in matched:
        if not filepath.is_file():
            continue
        if should_skip(filepath, excludes):
            continue

        name_lower = filepath.name.lower()
        if name_lower == "package.json":
            _extract_package_json(root, filepath, nodes, edges, discovered_from)
        elif name_lower in ("makefile", "justfile"):
            _extract_makefile(root, filepath, nodes, edges, discovered_from)

    return StrategyResult(nodes, edges, discovered_from)
