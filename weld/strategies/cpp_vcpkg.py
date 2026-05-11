"""Strategy: vcpkg manifest extractor (ADR 0057, Wave 1).

Parses ``vcpkg.json`` -- vcpkg's package-manager manifest -- and emits a
``package://vcpkg/<name>`` node plus a ``depends_on`` edge from the
owning C++ project for each entry in the manifest's ``dependencies``
array.

The manifest schema is small but flexible:

- ``dependencies`` is a list whose entries can be plain strings
  (``"fmt"``) or objects with a required ``name`` field
  (``{"name": "fmt", "version>=": "9.0.0"}``).
- A top-level ``name`` field gives the owning project name; we fall
  back to the directory name when absent or invalid.

Confidence (ADR 0050): every emitted edge is ``definite`` -- the input
is a literal JSON config file. There is no Python-or-dynamic shape
this strategy needs to flag as ``speculative``.
"""

from __future__ import annotations

import json
from pathlib import Path

from weld._node_ids import package_id
from weld.strategies._helpers import (
    StrategyResult,
    filter_glob_results,
    should_skip,
)

_STRATEGY = "cpp_vcpkg"


def _vcpkg_pkg_id(name: str) -> str:
    """Return ``package://vcpkg/<name>``."""
    return f"package://vcpkg/{name}"


def _iter_dependencies(manifest: dict) -> list[tuple[str, dict]]:
    """Yield ``(name, raw_entry)`` for each dependency in the manifest."""
    out: list[tuple[str, dict]] = []
    deps = manifest.get("dependencies")
    if not isinstance(deps, list):
        return out
    for entry in deps:
        if isinstance(entry, str):
            if entry:
                out.append((entry, {"name": entry}))
            continue
        if isinstance(entry, dict):
            name = entry.get("name")
            if isinstance(name, str) and name:
                out.append((name, entry))
    return out


def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Walk vcpkg.json files and emit dependency edges."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source.get("glob", "")
    excludes = source.get("exclude", [])
    if not pattern:
        return StrategyResult(nodes, edges, discovered_from)

    if "**" in pattern:
        matched = filter_glob_results(root, sorted(root.glob(pattern)))
    else:
        parent = (root / pattern).parent
        if not parent.is_dir():
            return StrategyResult(nodes, edges, discovered_from)
        matched = filter_glob_results(
            root, sorted(parent.glob(Path(pattern).name)),
        )

    for vcpkg_path in matched:
        if not vcpkg_path.is_file():
            continue
        if should_skip(vcpkg_path, excludes, root=root):
            continue

        rel = vcpkg_path.relative_to(root).as_posix()
        try:
            text = vcpkg_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        try:
            manifest = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(manifest, dict):
            continue
        discovered_from.append(rel)

        project_name = manifest.get("name")
        if not isinstance(project_name, str) or not project_name:
            project_name = vcpkg_path.parent.name or "vcpkg"
        project_nid = package_id("cpp", project_name)
        nodes.setdefault(
            project_nid,
            {
                "type": "package",
                "label": project_name,
                "props": {
                    "name": project_name,
                    "file": rel,
                    "source_strategy": _STRATEGY,
                    "authority": "canonical",
                    "confidence": "definite",
                    "roles": ["config"],
                },
            },
        )

        for name, entry in _iter_dependencies(manifest):
            pkg_nid = _vcpkg_pkg_id(name)
            nodes.setdefault(
                pkg_nid,
                {
                    "type": "package",
                    "label": f"vcpkg {name}",
                    "props": {
                        "name": name,
                        "source_strategy": _STRATEGY,
                        "authority": "external",
                        "confidence": "inferred",
                        "roles": ["config"],
                        "ecosystem": "vcpkg",
                    },
                },
            )
            edge_props: dict = {
                "source_strategy": _STRATEGY,
                "confidence": "definite",
                "kind": "vcpkg_dependency",
                "file": rel,
            }
            # Preserve any version constraint so consumers can render
            # the dependency without re-parsing the manifest.
            version_ge = entry.get("version>=") if isinstance(entry, dict) else None
            if isinstance(version_ge, str) and version_ge:
                edge_props["version_constraint"] = f">={version_ge}"
            edges.append(
                {
                    "from": project_nid,
                    "to": pkg_nid,
                    "type": "depends_on",
                    "props": edge_props,
                }
            )

    return StrategyResult(nodes, edges, discovered_from)
