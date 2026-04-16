"""Strategy: ROS2 ``CMakeLists.txt`` line-recognizer.

This is intentionally **not** a full CMake parser. It scans each
``CMakeLists.txt`` line-by-line for the four macros that dominate ROS2
packaging and that are load-bearing for connected-work consumers:

- ``find_package(<name> REQUIRED ...)``
  → ``depends_on`` edge from the owning ``ros_package`` to
    ``ros_package:<name>`` (sentinel if unseen).
- ``add_executable(<target> <srcs...>)`` / ``ament_target_dependencies(
  <target> <deps...>)``
  → ``build-target`` node ``build-target:ros2:<pkg>:<target>`` with a
    ``builds`` edge from the package, plus ``depends_on`` edges from the
    target to each listed ament dependency.
- ``rosidl_generate_interfaces(<target> <files...>)``
  → ``builds`` edge from the package to an interface-generation hint node
    ``ros_interface:<pkg>:<target>``. A later layer
    (``ros2_interfaces``, project-f7y.5) fills in the per-message detail.
- ``rclcpp_components_register_nodes(<target> "ns::Class" ...)``
  → ``ros_node:<ns::Class>`` hint node + an ``implements`` edge from the
    build target to the component node. A later layer
    (``ros2_topology``, project-f7y.6/7) fills in topic wiring.

Owning package resolution: the strategy walks upward from the
``CMakeLists.txt`` file until it finds a sibling ``package.xml`` and reads
``<name>`` from it (or falls back to the directory name). This keeps the
strategy independent of the ordering of source entries in
``.weld/discover.yaml``.

See the ROS2 connected-structure schema ADR.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from weld.strategies._helpers import StrategyResult, filter_glob_results, should_skip

_STRATEGY = "ros2_cmake"

# Pre-compile one matcher per macro. We capture the full parenthesised
# argument group as a single string and split it in Python — CMake argument
# lists are whitespace-separated and may span lines, so we read logical
# blocks rather than raw source lines.
_BLOCK_RE = re.compile(
    r"(find_package|add_executable|ament_target_dependencies|"
    r"rosidl_generate_interfaces|rclcpp_components_register_nodes)"
    r"\s*\(([^)]*)\)",
    re.DOTALL,
)

# Strip CMake comments (``# ...`` to end of line) before block scanning so
# that commented-out ``find_package`` calls do not pollute the graph.
_COMMENT_RE = re.compile(r"#[^\n]*")

def _owning_package(cmake_path: Path, root: Path) -> str:
    """Return the ROS package name that owns *cmake_path*.

    Walks up from *cmake_path* until a sibling ``package.xml`` is found
    and returns its ``<name>``. If no manifest is found before the repo
    root, returns the directory name as a best-effort fallback.
    """
    current = cmake_path.parent
    while True:
        manifest = current / "package.xml"
        if manifest.is_file():
            try:
                tree = ET.parse(manifest)
                name = tree.getroot().find("name")
                if name is not None and name.text:
                    return name.text.strip()
            except (OSError, ET.ParseError):
                pass
            return current.name
        if current == root or current.parent == current:
            return cmake_path.parent.name
        current = current.parent

def _split_args(block: str) -> list[str]:
    """Return the whitespace-separated CMake arguments from a block body.

    Quoted strings are unquoted; empty tokens are dropped.
    """
    tokens: list[str] = []
    for raw in block.split():
        tok = raw.strip()
        if not tok:
            continue
        if (tok.startswith('"') and tok.endswith('"')) or (
            tok.startswith("'") and tok.endswith("'")
        ):
            tok = tok[1:-1]
        tokens.append(tok)
    return tokens

def _pkg_nid(name: str) -> str:
    return f"ros_package:{name}"

def _ensure_sentinel(nodes: dict[str, dict], name: str) -> None:
    nid = _pkg_nid(name)
    nodes.setdefault(
        nid,
        {
            "type": "ros_package",
            "label": name,
            "props": {
                "source_strategy": _STRATEGY,
                "authority": "external",
                "confidence": "inferred",
                "roles": ["config"],
            },
        },
    )

def _handle_find_package(
    args: list[str], owner_nid: str, nodes: dict, edges: list, rel: str
) -> None:
    if not args:
        return
    name = args[0]
    _ensure_sentinel(nodes, name)
    edges.append(
        {
            "from": owner_nid,
            "to": _pkg_nid(name),
            "type": "depends_on",
            "props": {
                "source_strategy": _STRATEGY,
                "confidence": "definite",
                "kind": "find_package",
                "file": rel,
            },
        }
    )

def _handle_add_executable(
    args: list[str],
    owner_name: str,
    owner_nid: str,
    nodes: dict,
    edges: list,
    rel: str,
) -> None:
    if not args:
        return
    target = args[0]
    nid = f"build-target:ros2:{owner_name}:{target}"
    nodes.setdefault(
        nid,
        {
            "type": "build-target",
            "label": f"ros2 {owner_name}:{target}",
            "props": {
                "file": rel,
                "target_name": target,
                "source_strategy": _STRATEGY,
                "authority": "canonical",
                "confidence": "definite",
                "roles": ["build"],
            },
        },
    )
    edges.append(
        {
            "from": owner_nid,
            "to": nid,
            "type": "builds",
            "props": {
                "source_strategy": _STRATEGY,
                "confidence": "definite",
            },
        }
    )

def _handle_ament_target_dependencies(
    args: list[str], owner_name: str, nodes: dict, edges: list, rel: str
) -> None:
    if len(args) < 2:
        return
    target = args[0]
    target_nid = f"build-target:ros2:{owner_name}:{target}"
    # If add_executable was not yet scanned, create a stub we can wire to.
    nodes.setdefault(
        target_nid,
        {
            "type": "build-target",
            "label": f"ros2 {owner_name}:{target}",
            "props": {
                "file": rel,
                "target_name": target,
                "source_strategy": _STRATEGY,
                "authority": "canonical",
                "confidence": "definite",
                "roles": ["build"],
            },
        },
    )
    for dep in args[1:]:
        _ensure_sentinel(nodes, dep)
        edges.append(
            {
                "from": target_nid,
                "to": _pkg_nid(dep),
                "type": "depends_on",
                "props": {
                    "source_strategy": _STRATEGY,
                    "confidence": "definite",
                    "kind": "ament_target_dependencies",
                    "file": rel,
                },
            }
        )

def _handle_rosidl_generate_interfaces(
    args: list[str], owner_name: str, owner_nid: str, nodes: dict, edges: list, rel: str
) -> None:
    if not args:
        return
    target = args[0]
    nid = f"ros_interface:{owner_name}:{target}"
    nodes.setdefault(
        nid,
        {
            "type": "ros_interface",
            "label": f"{owner_name}/{target}",
            "props": {
                "file": rel,
                "target_name": target,
                "source_strategy": _STRATEGY,
                "authority": "canonical",
                "confidence": "inferred",  # filled in by ros2_interfaces
                "roles": ["config"],
            },
        },
    )
    edges.append(
        {
            "from": owner_nid,
            "to": nid,
            "type": "builds",
            "props": {
                "source_strategy": _STRATEGY,
                "confidence": "definite",
            },
        }
    )

def _handle_rclcpp_components_register_nodes(
    args: list[str], owner_name: str, nodes: dict, edges: list, rel: str
) -> None:
    if len(args) < 2:
        return
    target = args[0]
    target_nid = f"build-target:ros2:{owner_name}:{target}"
    nodes.setdefault(
        target_nid,
        {
            "type": "build-target",
            "label": f"ros2 {owner_name}:{target}",
            "props": {
                "file": rel,
                "target_name": target,
                "source_strategy": _STRATEGY,
                "authority": "canonical",
                "confidence": "definite",
                "roles": ["build"],
            },
        },
    )
    for cls in args[1:]:
        node_nid = f"ros_node:{cls}"
        nodes.setdefault(
            node_nid,
            {
                "type": "ros_node",
                "label": cls,
                "props": {
                    "file": rel,
                    "class_name": cls,
                    "source_strategy": _STRATEGY,
                    "authority": "canonical",
                    "confidence": "inferred",  # filled in by ros2_topology
                    "roles": ["implementation"],
                },
            },
        )
        edges.append(
            {
                "from": target_nid,
                "to": node_nid,
                "type": "implements",
                "props": {
                    "source_strategy": _STRATEGY,
                    "confidence": "definite",
                },
            }
        )

def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract ROS2 build edges from CMakeLists.txt files."""
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
            root, sorted(parent.glob(Path(pattern).name))
        )

    for cmake in matched:
        if not cmake.is_file():
            continue
        if should_skip(cmake, excludes):
            continue

        rel = str(cmake.relative_to(root))
        discovered_from.append(rel)

        try:
            text = cmake.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        text = _COMMENT_RE.sub("", text)

        owner_name = _owning_package(cmake, root)
        owner_nid = _pkg_nid(owner_name)
        # Only register a light sentinel if the package.xml strategy has
        # not already populated this node. This keeps the cmake strategy
        # usable standalone for the tests without clobbering richer props.
        _ensure_sentinel(nodes, owner_name)

        for match in _BLOCK_RE.finditer(text):
            macro = match.group(1)
            args = _split_args(match.group(2))
            if macro == "find_package":
                _handle_find_package(args, owner_nid, nodes, edges, rel)
            elif macro == "add_executable":
                _handle_add_executable(
                    args, owner_name, owner_nid, nodes, edges, rel
                )
            elif macro == "ament_target_dependencies":
                _handle_ament_target_dependencies(
                    args, owner_name, nodes, edges, rel
                )
            elif macro == "rosidl_generate_interfaces":
                _handle_rosidl_generate_interfaces(
                    args, owner_name, owner_nid, nodes, edges, rel
                )
            elif macro == "rclcpp_components_register_nodes":
                _handle_rclcpp_components_register_nodes(
                    args, owner_name, nodes, edges, rel
                )

    return StrategyResult(nodes, edges, discovered_from)
