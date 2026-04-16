"""Tests for the ``ros2_package`` extraction strategy.

Each test builds a small on-disk
fixture, runs ``ros2_package.extract`` against it, and asserts the
resulting fragment is well-formed via ``validate_fragment``.
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
from weld.strategies import ros2_package  # noqa: E402

_MANIFEST = textwrap.dedent(
    """\
    <?xml version="1.0"?>
    <package format="3">
      <name>demo_pkg</name>
      <version>0.1.0</version>
      <description>Demo package for tests.</description>
      <maintainer email="demo@example.com">Demo Maintainer</maintainer>
      <license>Apache-2.0</license>

      <buildtool_depend>ament_cmake</buildtool_depend>
      <depend>rclcpp</depend>
      <depend>std_msgs</depend>
      <build_depend>rosidl_default_generators</build_depend>
      <exec_depend>rosidl_default_runtime</exec_depend>
      <test_depend>ament_lint_auto</test_depend>

      <export>
        <build_type>ament_cmake</build_type>
      </export>
    </package>
    """
)

class Ros2PackageStrategyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        pkg_dir = self.tmp / "src" / "demo_pkg"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "package.xml").write_text(_MANIFEST, encoding="utf-8")
        (pkg_dir / "CMakeLists.txt").write_text(
            "cmake_minimum_required(VERSION 3.8)\n", encoding="utf-8"
        )

    def _run(self):
        return ros2_package.extract(
            self.tmp, {"glob": "**/package.xml"}, {}
        )

    def test_emits_ros_package_node_with_core_props(self) -> None:
        result = self._run()
        self.assertIn("ros_package:demo_pkg", result.nodes)
        node = result.nodes["ros_package:demo_pkg"]
        self.assertEqual(node["type"], "ros_package")
        props = node["props"]
        self.assertEqual(props["version"], "0.1.0")
        self.assertEqual(props["license"], "Apache-2.0")
        self.assertEqual(props["build_type"], "ament_cmake")
        self.assertEqual(props["source_strategy"], "ros2_package")
        self.assertIn("Demo Maintainer", props["maintainer"])
        self.assertIn("demo_pkg", props["file"])

    def test_emits_depends_on_edges_for_each_depend_tag(self) -> None:
        result = self._run()
        dep_edges = [
            e for e in result.edges
            if e["from"] == "ros_package:demo_pkg" and e["type"] == "depends_on"
        ]
        wanted = {
            "ros_package:ament_cmake",
            "ros_package:rclcpp",
            "ros_package:std_msgs",
            "ros_package:rosidl_default_generators",
            "ros_package:rosidl_default_runtime",
            "ros_package:ament_lint_auto",
        }
        targets = {e["to"] for e in dep_edges}
        self.assertEqual(targets, wanted)

    def test_dependency_sentinels_have_ros_package_type(self) -> None:
        result = self._run()
        for name in ("rclcpp", "std_msgs", "ament_cmake"):
            nid = f"ros_package:{name}"
            self.assertIn(nid, result.nodes)
            self.assertEqual(result.nodes[nid]["type"], "ros_package")

    def test_contains_edges_to_sibling_files(self) -> None:
        result = self._run()
        contains = [
            e for e in result.edges
            if e["from"] == "ros_package:demo_pkg" and e["type"] == "contains"
        ]
        targets = {e["to"] for e in contains}
        self.assertIn("file:src/demo_pkg/package.xml", targets)
        self.assertIn("file:src/demo_pkg/CMakeLists.txt", targets)

    def test_fragment_validates_clean(self) -> None:
        result = self._run()
        fragment = {"nodes": result.nodes, "edges": list(result.edges)}
        errors = validate_fragment(
            fragment, source_label="strategy:ros2_package"
        )
        self.assertEqual(errors, [], f"unexpected validation errors: {errors}")

    def test_no_duplicate_depends_on_edges_when_same_dep_repeated(self) -> None:
        # A package.xml that lists rclcpp as both <depend> and <build_depend>
        # should not produce two identical depends_on edges.
        pkg_dir = self.tmp / "src" / "demo_pkg"
        (pkg_dir / "package.xml").write_text(
            textwrap.dedent(
                """\
                <?xml version="1.0"?>
                <package format="3">
                  <name>dup_pkg</name>
                  <version>0.0.1</version>
                  <description>Duplicate-dep check.</description>
                  <maintainer email="d@example.com">D</maintainer>
                  <license>Apache-2.0</license>
                  <depend>rclcpp</depend>
                  <build_depend>rclcpp</build_depend>
                  <exec_depend>rclcpp</exec_depend>
                  <export><build_type>ament_cmake</build_type></export>
                </package>
                """
            ),
            encoding="utf-8",
        )
        result = self._run()
        deps = [
            e for e in result.edges
            if e["from"] == "ros_package:dup_pkg"
            and e["to"] == "ros_package:rclcpp"
            and e["type"] == "depends_on"
        ]
        self.assertEqual(len(deps), 1)

    def test_malformed_manifest_is_skipped_gracefully(self) -> None:
        bad = self.tmp / "src" / "bad_pkg"
        bad.mkdir()
        (bad / "package.xml").write_text("<not valid xml", encoding="utf-8")
        # Must not raise; just skips the bad file.
        result = self._run()
        self.assertNotIn("ros_package:bad_pkg", result.nodes)

if __name__ == "__main__":
    unittest.main()
