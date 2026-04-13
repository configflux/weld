"""Strategy: Bazel BUILD file extraction for build and test targets.

Parses BUILD.bazel and BUILD files to extract build-target and test-target
nodes.  Recognizes common Bazel rule types (py_library, py_binary, py_test,
sh_test, sh_binary, etc.) and models them as first-class graph objects.

"""

from __future__ import annotations

import re
from pathlib import Path

from cortex.strategies._helpers import (
    StrategyResult,
    filter_glob_results,
    should_skip,
)

# Bazel rule patterns: maps rule name -> (node_type, role)
_BUILD_RULES: dict[str, tuple[str, str]] = {
    "py_library": ("build-target", "build"),
    "py_binary": ("build-target", "build"),
    "py_test": ("test-target", "test"),
    "sh_test": ("test-target", "test"),
    "sh_binary": ("build-target", "build"),
    "sh_library": ("build-target", "build"),
    "genrule": ("build-target", "build"),
    "filegroup": ("build-target", "build"),
    "exports_files": ("build-target", "build"),
}

# Regex to match a Bazel rule invocation with a name kwarg.
# Captures: rule_type, name value.
_RULE_RE = re.compile(
    r"^(\w+)\(\s*$"
)
_NAME_RE = re.compile(
    r'^\s*name\s*=\s*"([^"]+)"'
)
_SRCS_RE = re.compile(
    r'^\s*srcs\s*=\s*\['
)
_DEPS_RE = re.compile(
    r'^\s*deps\s*=\s*\['
)
_DEP_ENTRY_RE = re.compile(
    r'"(//[^"]+|:[^"]+)"'
)

def _parse_build_file(text: str) -> list[dict]:
    """Parse a BUILD file and return a list of target dicts.

    Each dict has: rule, name, srcs (list), deps (list).
    """
    targets: list[dict] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        # Check for rule start: e.g. "py_library("
        rule_match = re.match(r"^(\w+)\(", line)
        if rule_match:
            rule_name = rule_match.group(1)
            if rule_name not in _BUILD_RULES:
                i += 1
                continue
            # Collect the full rule block until closing paren
            target: dict = {"rule": rule_name, "name": "", "srcs": [], "deps": []}
            # Parse until we find the closing paren at col 0
            in_srcs = False
            in_deps = False
            while i < len(lines):
                cur = lines[i]
                stripped = cur.strip()

                # Check for name
                name_m = _NAME_RE.match(cur)
                if name_m:
                    target["name"] = name_m.group(1)

                # Track srcs/deps list contexts
                if _SRCS_RE.match(cur):
                    in_srcs = True
                    in_deps = False
                elif _DEPS_RE.match(cur):
                    in_deps = True
                    in_srcs = False

                if in_srcs or in_deps:
                    for dep_m in _DEP_ENTRY_RE.finditer(cur):
                        if in_deps:
                            target["deps"].append(dep_m.group(1))
                        elif in_srcs:
                            target["srcs"].append(dep_m.group(1))

                if stripped.endswith("],"):
                    in_srcs = False
                    in_deps = False

                # Check for rule end
                if stripped == ")" or stripped.endswith(")"):
                    break
                i += 1
            if target["name"]:
                targets.append(target)
        i += 1
    return targets

def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract Bazel build and test target nodes from BUILD files."""
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

    for build_file in matched:
        if not build_file.is_file():
            continue
        if should_skip(build_file, excludes):
            continue

        rel_path = str(build_file.relative_to(root))
        discovered_from.append(rel_path)

        try:
            text = build_file.read_text(encoding="utf-8")
        except OSError:
            continue

        # Derive the Bazel package path (directory relative to root)
        pkg_dir = build_file.parent
        if pkg_dir == root:
            pkg_label = "//"
        else:
            pkg_label = "//" + str(pkg_dir.relative_to(root))

        targets = _parse_build_file(text)
        for target in targets:
            rule = target["rule"]
            name = target["name"]
            node_type, role = _BUILD_RULES[rule]

            nid = f"{node_type}:{pkg_label}:{name}"
            bazel_label = f"{pkg_label}:{name}"

            nodes[nid] = {
                "type": node_type,
                "label": bazel_label,
                "props": {
                    "file": rel_path,
                    "bazel_label": bazel_label,
                    "rule": rule,
                    "source_strategy": "bazel",
                    "authority": "canonical",
                    "confidence": "definite",
                    "roles": [role],
                },
            }

            # Create edges for deps that reference other packages
            for dep in target["deps"]:
                # Normalize dep label
                if dep.startswith(":"):
                    dep_full = f"{pkg_label}{dep}"
                else:
                    dep_full = dep

                # Determine target node type for the dep (assume build-target)
                dep_nid = f"build-target:{dep_full}"
                edge_type = "depends_on"
                edges.append({
                    "from": nid,
                    "to": dep_nid,
                    "type": edge_type,
                    "props": {
                        "source_strategy": "bazel",
                        "confidence": "definite",
                    },
                })

            # For test targets, add a "tests" edge to the package
            if node_type == "test-target":
                for dep in target["deps"]:
                    if dep.startswith(":"):
                        dep_full = f"{pkg_label}{dep}"
                    else:
                        dep_full = dep
                    dep_nid = f"build-target:{dep_full}"
                    edges.append({
                        "from": nid,
                        "to": dep_nid,
                        "type": "tests",
                        "props": {
                            "source_strategy": "bazel",
                            "confidence": "inferred",
                        },
                    })

    return StrategyResult(nodes, edges, discovered_from)
