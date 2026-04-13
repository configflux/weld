"""ROS2 source-entry generation for ``cortex init``.

Lives alongside ``cortex/init.py`` so the ``init.py`` grandfather cap (420
lines, see ``tools/lint_repo.py``) does not grow just to wire the ROS2
strategies. The helper produces ready-to-use YAML fragments for the five
``ros2_*`` strategies plus a C++ tree-sitter entry rooted at the
workspace ``src/`` directory so the ROS2 C++ nodes get call-graph edges.

ADR: ``docs/adrs/0016-kg-ros2-knowledge-graph.md`` (draft) covers the
node-type story; wiring into ``cortex init`` is this task.
"""

from __future__ import annotations

def _entry(
    glob: str, node_type: str, strategy: str,
    *, comment: str, extra: dict[str, str] | None = None,
) -> str:
    """Return a YAML source-entry block matching ``cortex/init._source_entry``."""
    lines: list[str] = [f"\n  # --- {comment} ---"]
    lines.append(f'  - glob: "{glob}"')
    lines.append(f"    type: {node_type}")
    lines.append(f"    strategy: {strategy}")
    if extra:
        for k, v in extra.items():
            lines.append(f"    {k}: {v}")
    return "\n".join(lines)

def ros2_source_entries(pkg_roots: list[str]) -> list[str]:
    """Return YAML source entries wiring every ROS2 strategy.

    ``pkg_roots`` is the list returned by ``detect_ros2``; the function
    assumes it is non-empty. Entries use whole-workspace globs rather
    than per-package ones because the ROS2 strategies each walk the
    matched files and key nodes by package anyway (see the strategy
    modules in ``cortex/strategies/ros2_*.py``).
    """
    entries: list[str] = []

    entries.append(_entry(
        "**/package.xml", "ros_package", "ros2_package",
        comment="ROS2 package manifests",
    ))
    entries.append(_entry(
        "**/CMakeLists.txt", "ros_package", "ros2_cmake",
        comment="ROS2 CMake build files",
    ))
    entries.append(_entry(
        "**/*.msg", "ros_interface", "ros2_interfaces",
        comment="ROS2 .msg interfaces",
    ))
    entries.append(_entry(
        "**/*.srv", "ros_interface", "ros2_interfaces",
        comment="ROS2 .srv interfaces",
    ))
    entries.append(_entry(
        "**/*.action", "ros_interface", "ros2_interfaces",
        comment="ROS2 .action interfaces",
    ))

    # Topology: scan C++ and Python sources under the workspace src/
    # tree.  ``ros2_topology`` dispatches by file extension internally
    # (see ``cortex/strategies/ros2_topology.py``), so emit one glob per
    # language family.  Using ``src/**/*`` keeps the scan focused on
    # first-party package code rather than build artefacts.
    entries.append(_entry(
        "src/**/*.cpp", "ros_node", "ros2_topology",
        comment="ROS2 runtime topology (C++)",
    ))
    entries.append(_entry(
        "src/**/*.py", "ros_node", "ros2_topology",
        comment="ROS2 runtime topology (Python)",
    ))

    entries.append(_entry(
        "**/*.launch.py", "ros_launch", "ros2_launch",
        comment="ROS2 launch files (Python)",
    ))
    entries.append(_entry(
        "**/*.launch.xml", "ros_launch", "ros2_launch",
        comment="ROS2 launch files (XML)",
    ))
    entries.append(_entry(
        "**/*.launch.yaml", "ros_launch", "ros2_launch",
        comment="ROS2 launch files (YAML)",
    ))

    return entries
