"""Strategy: build-system root-file detector (ADR 0057, Wave 1).

A lightweight probe that scans for the *root* file of each known C++
build system and records what it finds as a single node per match.
Unlike :mod:`cpp_cmake` / :mod:`cpp_conan` / :mod:`cpp_vcpkg` this
strategy does not try to *parse* the file -- it only attests that a
build system is present so :func:`wd capabilities` and downstream
tooling can report which projects are reachable.

Files recognised:

- ``CMakeLists.txt``    -> ``build_system: cmake`` (parsed elsewhere).
- ``Makefile`` / ``GNUmakefile`` -> ``build_system: make``,
  ``unsupported_build_system: make``.
- ``meson.build``       -> ``build_system: meson``,
  ``unsupported_build_system: meson``.
- ``BUILD`` / ``BUILD.bazel`` -> ``build_system: bazel`` (defers
  emission of build-target nodes to :mod:`bazel`).

The strategy emits one ``build-target`` node per matched root file and
a ``contains`` edge from the per-directory ``package:cpp:<name>``
project node so the call surface is uniform regardless of which build
system is in play. ``confidence`` is ``definite`` (root file exists).
"""

from __future__ import annotations

from pathlib import Path

from weld._node_ids import package_id
from weld.strategies._glob_resolve import resolve_glob
from weld.strategies._helpers import StrategyResult

_STRATEGY = "cpp_buildsystem_detector"

# Map basename -> (build_system label, unsupported).
_KNOWN_FILES: dict[str, tuple[str, bool]] = {
    "CMakeLists.txt": ("cmake", False),
    "Makefile": ("make", True),
    "GNUmakefile": ("make", True),
    "meson.build": ("meson", True),
    "BUILD": ("bazel", False),
    "BUILD.bazel": ("bazel", False),
}


def _detect_one(path: Path) -> tuple[str, bool] | None:
    """Return ``(build_system, unsupported)`` for *path* or ``None``."""
    return _KNOWN_FILES.get(path.name)


def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Probe for build-system root files and record their presence."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source.get("glob", "")
    excludes = source.get("exclude", [])
    if not pattern:
        return StrategyResult(nodes, edges, discovered_from)

    matched = resolve_glob(root, pattern, excludes)

    for candidate in matched:
        if not candidate.is_file():
            continue
        detected = _detect_one(candidate)
        if detected is None:
            continue

        build_system, unsupported = detected
        rel = candidate.relative_to(root).as_posix()
        discovered_from.append(rel)

        project = candidate.parent.name or build_system
        project_nid = package_id("cpp", project)
        # ``package:cpp:<project>`` is shared with the cmake/conan/vcpkg
        # strategies; setdefault preserves whatever those wrote first.
        nodes.setdefault(
            project_nid,
            {
                "type": "package",
                "label": project,
                "props": {
                    "name": project,
                    "file": rel,
                    "source_strategy": _STRATEGY,
                    "authority": "canonical",
                    "confidence": "definite",
                    "roles": ["config"],
                    "build_system": build_system,
                },
            },
        )
        # Always stamp build_system on the project node so consumers
        # see the detection even if cmake/conan/vcpkg already populated
        # the node with a different ordering. We only overwrite when
        # the existing prop is missing: cmake-strategy authority wins.
        props = nodes[project_nid]["props"]
        props.setdefault("build_system", build_system)

        target_nid = (
            f"build-target:{build_system}:{project}:{candidate.stem or 'root'}"
        )
        node_props: dict = {
            "file": rel,
            "target_name": candidate.stem or candidate.name,
            "project_name": project,
            "source_strategy": _STRATEGY,
            "authority": "canonical",
            "confidence": "definite",
            "roles": ["build"],
            "build_system": build_system,
        }
        if unsupported:
            node_props["unsupported_build_system"] = build_system
        nodes.setdefault(
            target_nid,
            {
                "type": "build-target",
                "label": f"{build_system} {project}",
                "props": node_props,
            },
        )
        edges.append(
            {
                "from": project_nid,
                "to": target_nid,
                "type": "contains",
                "props": {
                    "source_strategy": _STRATEGY,
                    "confidence": "definite",
                    "kind": "build_system_root",
                    "file": rel,
                },
            }
        )

    return StrategyResult(nodes, edges, discovered_from)
