"""Tests for the ``ros2_cmake`` extraction strategy.

Verifies the line-recognizer for
``find_package``, ``add_executable`` / ``ament_target_dependencies``,
``rosidl_generate_interfaces``, and ``rclcpp_components_register_nodes``.
"""

from __future__ import annotations

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.contract import validate_fragment  # noqa: E402
from weld.strategies import ros2_cmake  # noqa: E402

_PACKAGE_XML = textwrap.dedent(
    """\
    <?xml version="1.0"?>
    <package format="3">
      <name>demo_pkg</name>
      <version>0.1.0</version>
      <description>Demo.</description>
      <maintainer email="d@example.com">D</maintainer>
      <license>Apache-2.0</license>
      <export><build_type>ament_cmake</build_type></export>
    </package>
    """
)

_CMAKE = textwrap.dedent(
    """\
    cmake_minimum_required(VERSION 3.8)
    project(demo_pkg)

    # This commented-out dep must be ignored:
    # find_package(ignored_pkg REQUIRED)

    find_package(ament_cmake REQUIRED)
    find_package(rclcpp REQUIRED)
    find_package(std_msgs REQUIRED)
    find_package(rosidl_default_generators REQUIRED)

    rosidl_generate_interfaces(demo_pkg_interfaces
      "msg/Status.msg"
      "srv/Ping.srv"
    )

    add_executable(demo_talker src/demo_talker.cpp)
    ament_target_dependencies(demo_talker rclcpp std_msgs)

    add_executable(demo_component src/demo_component.cpp)
    ament_target_dependencies(demo_component rclcpp)

    rclcpp_components_register_nodes(demo_component "demo_pkg::DemoComponent")

    ament_package()
    """
)

class Ros2CmakeStrategyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        pkg_dir = self.tmp / "src" / "demo_pkg"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "package.xml").write_text(_PACKAGE_XML, encoding="utf-8")
        (pkg_dir / "CMakeLists.txt").write_text(_CMAKE, encoding="utf-8")

    def _run(self):
        return ros2_cmake.extract(
            self.tmp, {"glob": "**/CMakeLists.txt"}, {}
        )

    def test_find_package_emits_depends_on(self) -> None:
        result = self._run()
        dep_targets = {
            e["to"]
            for e in result.edges
            if e["from"] == "ros_package:demo_pkg"
            and e["type"] == "depends_on"
            and e["props"].get("kind") == "find_package"
        }
        self.assertIn("ros_package:ament_cmake", dep_targets)
        self.assertIn("ros_package:rclcpp", dep_targets)
        self.assertIn("ros_package:std_msgs", dep_targets)
        # Commented-out dep must not appear.
        self.assertNotIn("ros_package:ignored_pkg", dep_targets)

    def test_add_executable_creates_build_targets(self) -> None:
        result = self._run()
        self.assertIn(
            "build-target:ros2:demo_pkg:demo_talker", result.nodes
        )
        self.assertIn(
            "build-target:ros2:demo_pkg:demo_component", result.nodes
        )
        builds = [
            e for e in result.edges
            if e["from"] == "ros_package:demo_pkg" and e["type"] == "builds"
        ]
        targets = {e["to"] for e in builds}
        self.assertIn("build-target:ros2:demo_pkg:demo_talker", targets)
        self.assertIn("build-target:ros2:demo_pkg:demo_component", targets)

    def test_ament_target_dependencies_wires_target_deps(self) -> None:
        result = self._run()
        deps = [
            e for e in result.edges
            if e["from"] == "build-target:ros2:demo_pkg:demo_talker"
            and e["type"] == "depends_on"
            and e["props"].get("kind") == "ament_target_dependencies"
        ]
        targets = {e["to"] for e in deps}
        self.assertEqual(
            targets, {"ros_package:rclcpp", "ros_package:std_msgs"}
        )

    def test_rosidl_generate_interfaces_emits_interface_hint(self) -> None:
        result = self._run()
        nid = "ros_interface:demo_pkg:demo_pkg_interfaces"
        self.assertIn(nid, result.nodes)
        self.assertEqual(result.nodes[nid]["type"], "ros_interface")
        match = next(
            (
                e for e in result.edges
                if e["from"] == "ros_package:demo_pkg"
                and e["to"] == nid
                and e["type"] == "builds"
            ),
            None,
        )
        self.assertIsNotNone(match)

    def test_rclcpp_components_register_nodes_emits_node_hint(self) -> None:
        result = self._run()
        node_nid = "ros_node:demo_pkg::DemoComponent"
        self.assertIn(node_nid, result.nodes)
        self.assertEqual(result.nodes[node_nid]["type"], "ros_node")
        match = next(
            (
                e for e in result.edges
                if e["from"] == "build-target:ros2:demo_pkg:demo_component"
                and e["to"] == node_nid
                and e["type"] == "implements"
            ),
            None,
        )
        self.assertIsNotNone(match)

    def test_fragment_validates_clean(self) -> None:
        result = self._run()
        fragment = {"nodes": result.nodes, "edges": list(result.edges)}
        errors = validate_fragment(
            fragment, source_label="strategy:ros2_cmake"
        )
        self.assertEqual(errors, [], f"unexpected validation errors: {errors}")

    def test_owning_package_falls_back_to_dir_when_no_manifest(self) -> None:
        # A CMakeLists.txt with no sibling package.xml should still be
        # processed and owned by its directory name.
        solo = self.tmp / "standalone"
        solo.mkdir()
        (solo / "CMakeLists.txt").write_text(
            "find_package(rclcpp REQUIRED)\n", encoding="utf-8"
        )
        result = self._run()
        self.assertIn("ros_package:standalone", result.nodes)
        self.assertTrue(
            any(
                e["from"] == "ros_package:standalone"
                and e["to"] == "ros_package:rclcpp"
                for e in result.edges
            )
        )

if __name__ == "__main__":
    unittest.main()
