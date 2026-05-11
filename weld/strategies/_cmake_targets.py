"""Per-call target handlers for ``cpp_cmake`` (ADR 0057).

This module owns the ``add_executable`` / ``add_library`` /
``target_sources`` / ``target_link_libraries`` /
``target_include_directories`` / ``target_compile_definitions`` call
shapes. Package handling (``find_package``) and shared utility
helpers live in :mod:`_cmake_packages` so neither file exceeds the
400-line cap.

Every handler signature mirrors the ROS2 strategy's pattern: a single
``CMakeCall`` worth of args, the per-file dictionaries to mutate, and
the file-relative path for provenance.

Confidence convention (ADR 0050):

- ``definite``    -- name explicitly given in the source call.
- ``inferred``    -- never emitted from this module (find_package
  components are in :mod:`_cmake_packages`).
- ``speculative`` -- never emitted by the static parser.

Out of scope:

- ``glob``, ``select``, ``configure_file``, ``add_subdirectory``,
  ``include(...)``, custom commands. They become ``unresolved_labels``
  via :func:`_cmake_packages.bump_unresolved`.
"""

from __future__ import annotations

from weld._node_ids import package_id
from weld.strategies._cmake_packages import (
    STRATEGY,
    build_target_id,
    bump_unresolved,
    ensure_package_sentinel,
    ensure_target,
    file_node_id,
    handle_find_package,
    is_unresolvable_token,
    join_path,
)

__all__ = [
    "STRATEGY",
    "build_target_id",
    "handle_add_executable",
    "handle_add_library",
    "handle_find_package",
    "handle_target_compile_definitions",
    "handle_target_include_directories",
    "handle_target_link_libraries",
    "handle_target_sources",
]


def _emit_contains(
    nid: str, file_nid: str, file_rel: str, edges: list,
) -> None:
    edges.append(
        {
            "from": nid,
            "to": file_nid,
            "type": "contains",
            "props": {
                "source_strategy": STRATEGY,
                "confidence": "definite",
                "file": file_rel,
            },
        }
    )


def handle_add_executable(
    args: list[str],
    project: str,
    cmake_dir: str,
    nodes: dict,
    edges: list,
    file_rel: str,
) -> None:
    """Emit a ``build-target`` plus ``contains`` edges for sources."""
    if not args:
        return
    target = args[0]
    sources = [a for a in args[1:] if a not in ("IMPORTED", "ALIAS")]
    nid = ensure_target(nodes, project, target, file_rel)
    unresolved: list[str] = []
    for src in sources:
        if is_unresolvable_token(src):
            unresolved.append(src)
            continue
        rel = join_path(cmake_dir, src)
        _emit_contains(nid, file_node_id(rel), file_rel, edges)
    bump_unresolved(nodes[nid], unresolved)


def handle_add_library(
    args: list[str],
    project: str,
    cmake_dir: str,
    nodes: dict,
    edges: list,
    file_rel: str,
) -> None:
    """Same shape as ``add_executable`` but for libraries."""
    if not args:
        return
    target = args[0]
    skip = {"STATIC", "SHARED", "MODULE", "OBJECT", "INTERFACE", "IMPORTED",
            "ALIAS", "EXCLUDE_FROM_ALL"}
    sources = [a for a in args[1:] if a not in skip]
    nid = ensure_target(nodes, project, target, file_rel)
    unresolved: list[str] = []
    for src in sources:
        if is_unresolvable_token(src):
            unresolved.append(src)
            continue
        rel = join_path(cmake_dir, src)
        _emit_contains(nid, file_node_id(rel), file_rel, edges)
    bump_unresolved(nodes[nid], unresolved)


def handle_target_sources(
    args: list[str],
    project: str,
    cmake_dir: str,
    nodes: dict,
    edges: list,
    file_rel: str,
) -> None:
    """``target_sources(<target> [PRIVATE|PUBLIC|INTERFACE] <files...>)``."""
    if not args:
        return
    target = args[0]
    rest = [
        a for a in args[1:]
        if a not in ("PRIVATE", "PUBLIC", "INTERFACE")
    ]
    nid = ensure_target(nodes, project, target, file_rel)
    unresolved: list[str] = []
    for src in rest:
        if is_unresolvable_token(src):
            unresolved.append(src)
            continue
        rel = join_path(cmake_dir, src)
        _emit_contains(nid, file_node_id(rel), file_rel, edges)
    bump_unresolved(nodes[nid], unresolved)


def handle_target_link_libraries(
    args: list[str],
    project: str,
    nodes: dict,
    edges: list,
    file_rel: str,
    known_targets: set[str],
) -> None:
    """``target_link_libraries(<target> [keyword] <libs...>)``.

    Internal targets (those that appear in ``known_targets`` from
    earlier ``add_executable``/``add_library`` calls) become
    ``depends_on -> build-target`` edges. Everything else becomes a
    ``depends_on -> package:cpp:<name>`` edge with ``definite``
    confidence -- the call form is an explicit dependency statement, so
    the producing call is definitive even when the package node itself
    is a sentinel.
    """
    if not args:
        return
    target = args[0]
    keyword = {"PRIVATE", "PUBLIC", "INTERFACE", "LINK_PRIVATE",
               "LINK_PUBLIC", "LINK_INTERFACE_LIBRARIES"}
    libs = [a for a in args[1:] if a not in keyword]
    nid = ensure_target(nodes, project, target, file_rel)
    unresolved: list[str] = []
    for lib in libs:
        if is_unresolvable_token(lib):
            unresolved.append(lib)
            continue
        if lib in known_targets:
            dep_nid = build_target_id(project, lib)
            edges.append(
                {
                    "from": nid,
                    "to": dep_nid,
                    "type": "depends_on",
                    "props": {
                        "source_strategy": STRATEGY,
                        "confidence": "definite",
                        "kind": "target_link_libraries",
                        "file": file_rel,
                    },
                }
            )
        else:
            pkg_nid = package_id("cpp", lib)
            ensure_package_sentinel(nodes, pkg_nid, lib)
            edges.append(
                {
                    "from": nid,
                    "to": pkg_nid,
                    "type": "depends_on",
                    "props": {
                        "source_strategy": STRATEGY,
                        "confidence": "definite",
                        "kind": "target_link_libraries",
                        "file": file_rel,
                    },
                }
            )
    bump_unresolved(nodes[nid], unresolved)


def handle_target_include_directories(
    args: list[str],
    project: str,
    cmake_dir: str,
    nodes: dict,
    file_rel: str,
) -> None:
    """Record include directories as a prop on the target node.

    No edges -- the include path itself is not a graph entity in v1.
    """
    if not args:
        return
    target = args[0]
    keyword = {"PRIVATE", "PUBLIC", "INTERFACE", "BEFORE", "SYSTEM"}
    dirs = [a for a in args[1:] if a not in keyword]
    nid = ensure_target(nodes, project, target, file_rel)
    resolved: list[str] = []
    unresolved: list[str] = []
    for d in dirs:
        if is_unresolvable_token(d):
            unresolved.append(d)
            continue
        resolved.append(join_path(cmake_dir, d))
    props = nodes[nid]["props"]
    if resolved:
        existing = list(props.get("include_directories", []))
        existing.extend(resolved)
        seen: set[str] = set()
        ordered: list[str] = []
        for item in existing:
            if item not in seen:
                ordered.append(item)
                seen.add(item)
        props["include_directories"] = ordered
    bump_unresolved(nodes[nid], unresolved)


def handle_target_compile_definitions(
    args: list[str],
    project: str,
    nodes: dict,
    file_rel: str,
) -> None:
    """Record compile definitions as a prop on the target node."""
    if not args:
        return
    target = args[0]
    keyword = {"PRIVATE", "PUBLIC", "INTERFACE"}
    defs = [a for a in args[1:] if a not in keyword]
    if not defs:
        return
    nid = ensure_target(nodes, project, target, file_rel)
    unresolved: list[str] = []
    cleaned: list[str] = []
    for d in defs:
        if is_unresolvable_token(d):
            unresolved.append(d)
            continue
        cleaned.append(d)
    if cleaned:
        props = nodes[nid]["props"]
        existing = list(props.get("compile_definitions", []))
        existing.extend(cleaned)
        seen: set[str] = set()
        ordered: list[str] = []
        for item in existing:
            if item not in seen:
                ordered.append(item)
                seen.add(item)
        props["compile_definitions"] = ordered
    bump_unresolved(nodes[nid], unresolved)
