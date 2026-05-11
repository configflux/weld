"""Strategy: general-purpose CMake build-graph extractor (ADR 0057, Wave 1).

``cpp_cmake`` parses non-ROS ``CMakeLists.txt`` (and ``*.cmake``) files
and emits a minimal but useful build graph:

- ``build-target:cmake:<project>:<target>`` nodes (one per
  ``add_executable`` / ``add_library``).
- ``build-target -> contains -> file:<src>`` edges for every source
  listed by ``add_executable`` / ``add_library`` / ``target_sources``.
- ``build-target -> depends_on -> build-target`` for internal
  ``target_link_libraries`` (where the linked name resolves to a
  same-project target).
- ``build-target -> depends_on -> package:cpp:<lib>`` for external
  ``target_link_libraries`` and for every ``find_package(<name>)``.

The strategy *coexists* with :mod:`weld.strategies.ros2_cmake`. A file
that contains ``find_package(ament_cmake ...)`` or
``rosidl_generate_interfaces`` is left to the ROS2 strategy. ROS2
target IDs use ``build-target:ros2:...`` so the two strategies cannot
emit colliding node IDs.

Line-count discipline (ADR 0057 § Line-count discipline): the lexer
(:mod:`_cmake_lexer`), call handlers (:mod:`_cmake_targets`), and
scope-local variable expander (:mod:`_cmake_vars`) live in private
sibling modules so each file stays comfortably under the 400-line cap.
Every emitted edge sets ``confidence`` per ADR 0050:

- ``definite`` for explicit ``add_executable`` / ``add_library`` /
  ``target_link_libraries`` / ``find_package(name)`` calls.
- ``inferred`` for individual ``find_package COMPONENTS`` entries.
- ``unresolved_labels`` for glob / select / generator-expression
  arguments (kept as raw labels on the owning target node).
"""

from __future__ import annotations

import re
from pathlib import Path

from weld._node_ids import package_id
from weld.strategies._cmake_lexer import iter_calls, strip_comments
from weld.strategies._cmake_targets import (
    STRATEGY,
    build_target_id,
    handle_add_executable,
    handle_add_library,
    handle_find_package,
    handle_target_compile_definitions,
    handle_target_include_directories,
    handle_target_link_libraries,
    handle_target_sources,
)
from weld.strategies._cmake_vars import apply_set, expand
from weld.strategies._helpers import (
    StrategyResult,
    filter_glob_results,
    should_skip,
)

# Detect a ROS2 CMakeLists. Either of these markers means the file is
# owned by the ``ros2_cmake`` strategy and ``cpp_cmake`` must defer.
_ROS2_MARKER_RE = re.compile(
    r"\b(?:find_package\s*\(\s*ament_cmake\b|rosidl_generate_interfaces\b)",
)
# Extract ``project(name ...)`` so build-target IDs key on the
# CMakeLists' declared project rather than the directory name.
_PROJECT_RE = re.compile(
    r"project\s*\(\s*([A-Za-z_][A-Za-z0-9_-]*)",
)


def _is_ros2_cmake(text: str) -> bool:
    """Return True when *text* contains a ROS2 dispatch marker."""
    return _ROS2_MARKER_RE.search(text) is not None


def _project_name(text: str, fallback: str) -> str:
    match = _PROJECT_RE.search(text)
    if match:
        return match.group(1)
    return fallback


def _expand_args(raw_args: list[str], scope: dict[str, str]) -> list[str]:
    """Return *raw_args* with ``${VAR}`` substituted and CMake lists
    re-flattened.

    CMake's ``set(VAR a b c)`` stores its value as ``"a;b;c"`` (the
    internal list representation). When the value is later expanded
    into another call (``add_executable(app ${VAR})``), CMake re-splits
    the value on ``;`` and treats each element as its own argument.

    The post-expansion split below mirrors that behaviour so the call
    handlers see one argument per source rather than a single
    semicolon-joined string.
    """
    expanded: list[str] = []
    for arg in raw_args:
        substituted = expand(arg, scope)
        if ";" in substituted:
            for piece in substituted.split(";"):
                piece = piece.strip()
                if piece:
                    expanded.append(piece)
        elif substituted:
            expanded.append(substituted)
    return expanded


def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Walk CMakeLists.txt files and emit the C++ build graph."""
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

    for cmake in matched:
        if not cmake.is_file():
            continue
        if should_skip(cmake, excludes, root=root):
            continue

        rel = cmake.relative_to(root).as_posix()
        try:
            raw_text = cmake.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        # Strip comments BEFORE the ROS2 dispatch check so a commented
        # ``# find_package(ament_cmake REQUIRED)`` does not falsely
        # hand the file off to ``ros2_cmake``.
        cleaned = strip_comments(raw_text)

        if _is_ros2_cmake(cleaned):
            # ROS2 owns this file. Skip silently -- the discover.yaml
            # wiring usually has a separate glob for ros2_cmake but a
            # repo that wires both globs to the same files must get
            # consistent, non-double-counted output.
            continue

        discovered_from.append(rel)
        cmake_dir = cmake.parent.relative_to(root).as_posix()
        if cmake_dir == ".":
            cmake_dir = ""

        project = _project_name(cleaned, fallback=cmake.parent.name or "cmake")
        project_nid = _ensure_project_node(nodes, project, rel)
        # Per-file variable scope. ADR 0057 limits expansion to within
        # the same CMakeLists.txt; we do not propagate across files.
        scope: dict[str, str] = {}
        known_targets: set[str] = set()

        # Pass 1: collect every ``add_executable`` / ``add_library`` so
        # ``target_link_libraries`` can decide internal vs external.
        # We still expand variables inline so a set(...) earlier in the
        # file affects every later call.
        for call in iter_calls(cleaned):
            cmd = call.command
            args = _expand_args(call.args, scope)
            if cmd == "set":
                apply_set(args, scope)
                continue
            if cmd in ("add_executable", "add_library") and args:
                # First arg is the target name (skip type-spec tokens
                # for add_library so we record the right name).
                known_targets.add(args[0])

        # Pass 2: emit nodes/edges. Reset the scope so each pass walks
        # the same set values (the lexer is total; this is cheap).
        scope = {}
        for call in iter_calls(cleaned):
            cmd = call.command
            args = _expand_args(call.args, scope)
            if cmd == "set":
                apply_set(args, scope)
                continue
            if cmd == "add_executable":
                handle_add_executable(
                    args, project, cmake_dir, nodes, edges, rel,
                )
            elif cmd == "add_library":
                handle_add_library(
                    args, project, cmake_dir, nodes, edges, rel,
                )
            elif cmd == "target_sources":
                handle_target_sources(
                    args, project, cmake_dir, nodes, edges, rel,
                )
            elif cmd == "target_link_libraries":
                handle_target_link_libraries(
                    args, project, nodes, edges, rel, known_targets,
                )
            elif cmd == "target_include_directories":
                handle_target_include_directories(
                    args, project, cmake_dir, nodes, rel,
                )
            elif cmd == "target_compile_definitions":
                handle_target_compile_definitions(
                    args, project, nodes, rel,
                )
            elif cmd == "find_package":
                handle_find_package(args, project_nid, nodes, edges, rel)

        # Wire the project node to its build targets so ``wd impact``
        # can walk project -> contains -> target -> contains -> file.
        for target_name in sorted(known_targets):
            target_nid = build_target_id(project, target_name)
            if target_nid in nodes:
                edges.append(
                    {
                        "from": project_nid,
                        "to": target_nid,
                        "type": "contains",
                        "props": {
                            "source_strategy": STRATEGY,
                            "confidence": "definite",
                            "file": rel,
                        },
                    }
                )

    return StrategyResult(nodes, edges, discovered_from)


def _ensure_project_node(nodes: dict, project: str, file_rel: str) -> str:
    """Mint a ``package:cpp:<project>`` node and return its ID."""
    nid = package_id("cpp", project)
    nodes.setdefault(
        nid,
        {
            "type": "package",
            "label": project,
            "props": {
                "name": project,
                "file": file_rel,
                "source_strategy": STRATEGY,
                "authority": "canonical",
                "confidence": "definite",
                "roles": ["config"],
                "build_system": "cmake",
            },
        },
    )
    return nid
