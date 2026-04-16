"""Acceptance tests for ROS2 interaction graph extraction (project-xoq.7.2).

Exercises the ros2_topology (Python), ros2_interfaces, and
ros2_package strategies against the ``ros2_accept`` fixture and
verifies the resulting interaction graph: nodes, topics, services,
parameters, interface types, package dependencies, and edges.

Per ADR 0018, all ROS2 surfaces are statically extracted from source
text without requiring colcon build or runtime introspection.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.contract import validate_fragment  # noqa: E402
from weld.strategies import ros2_interfaces  # noqa: E402
from weld.strategies import ros2_package  # noqa: E402
from weld.strategies import ros2_topology  # noqa: E402

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "ros2_accept"

class Ros2TopologyAcceptanceTest(unittest.TestCase):
    """Python topology extractor produces nodes and edges."""

    def setUp(self) -> None:
        self.result = ros2_topology.extract(
            _FIXTURE, {"glob": "src/**/*.py"}, {}
        )
        self.nodes = self.result.nodes
        self.edges = self.result.edges

    # -- ros_node nodes ------------------------------------------------------

    def test_navigator_node_extracted(self) -> None:
        nid = "ros_node:nav_pkg.navigator.Navigator"
        self.assertIn(nid, self.nodes)
        props = self.nodes[nid]["props"]
        self.assertEqual(props.get("runtime_name"), "navigator")
        self.assertFalse(props.get("lifecycle", False))

    def test_safety_monitor_lifecycle_node(self) -> None:
        nid = "ros_node:nav_pkg.safety_monitor.SafetyMonitor"
        self.assertIn(nid, self.nodes)
        props = self.nodes[nid]["props"]
        self.assertEqual(props.get("runtime_name"), "safety_monitor")
        self.assertTrue(props.get("lifecycle"))

    # -- ros_topic nodes -----------------------------------------------------

    def test_published_topics_extracted(self) -> None:
        topics = {
            nid for nid, n in self.nodes.items()
            if n["type"] == "ros_topic"
        }
        self.assertIn("ros_topic:cmd_vel", topics)
        self.assertIn("ros_topic:emergency_stop", topics)

    def test_subscribed_topics_extracted(self) -> None:
        topics = {
            nid for nid, n in self.nodes.items()
            if n["type"] == "ros_topic"
        }
        self.assertIn("ros_topic:odom", topics)
        self.assertIn("ros_topic:scan", topics)

    def test_topic_message_types(self) -> None:
        self.assertEqual(
            self.nodes["ros_topic:cmd_vel"]["props"]["message_type"],
            "geometry_msgs/msg/Twist",
        )
        self.assertEqual(
            self.nodes["ros_topic:scan"]["props"]["message_type"],
            "sensor_msgs/msg/LaserScan",
        )

    # -- ros_service and ros_parameter nodes ---------------------------------

    def test_service_extracted(self) -> None:
        self.assertIn("ros_service:set_waypoint", self.nodes)

    def test_parameters_extracted(self) -> None:
        params = {
            nid for nid, n in self.nodes.items()
            if n["type"] == "ros_parameter"
        }
        self.assertIn(
            "ros_parameter:nav_pkg.navigator.Navigator/max_speed",
            params,
        )
        self.assertIn(
            "ros_parameter:nav_pkg.safety_monitor.SafetyMonitor/"
            "min_distance",
            params,
        )

    # -- edges ---------------------------------------------------------------

    def test_produces_edges(self) -> None:
        produces = [
            (e["from"], e["to"])
            for e in self.edges
            if e["type"] == "produces"
        ]
        self.assertIn(
            (
                "ros_node:nav_pkg.navigator.Navigator",
                "ros_topic:cmd_vel",
            ),
            produces,
        )
        self.assertIn(
            (
                "ros_node:nav_pkg.safety_monitor.SafetyMonitor",
                "ros_topic:emergency_stop",
            ),
            produces,
        )

    def test_consumes_edges(self) -> None:
        consumes = [
            (e["from"], e["to"])
            for e in self.edges
            if e["type"] == "consumes"
        ]
        self.assertIn(
            (
                "ros_node:nav_pkg.navigator.Navigator",
                "ros_topic:odom",
            ),
            consumes,
        )
        self.assertIn(
            (
                "ros_node:nav_pkg.safety_monitor.SafetyMonitor",
                "ros_topic:scan",
            ),
            consumes,
        )

    def test_exposes_service_edge(self) -> None:
        exposes = [
            (e["from"], e["to"])
            for e in self.edges
            if e["type"] == "exposes"
        ]
        self.assertIn(
            (
                "ros_node:nav_pkg.navigator.Navigator",
                "ros_service:set_waypoint",
            ),
            exposes,
        )

    def test_topic_implements_interface_edges(self) -> None:
        impl_edges = [
            (e["from"], e["to"])
            for e in self.edges
            if e["type"] == "implements"
        ]
        self.assertIn(
            (
                "ros_topic:cmd_vel",
                "ros_interface:geometry_msgs/msg/Twist",
            ),
            impl_edges,
        )

    def test_topology_fragment_validates(self) -> None:
        errs = validate_fragment(
            {"nodes": self.nodes, "edges": self.edges},
            source_label="strategy:ros2_topology",
            allow_dangling_edges=True,
        )
        self.assertEqual(errs, [], f"validation errors: {errs}")

class Ros2InterfacesAcceptanceTest(unittest.TestCase):
    """Interface msg/srv files produce ros_interface nodes."""

    def test_msg_interface_extracted(self) -> None:
        result = ros2_interfaces.extract(
            _FIXTURE, {"glob": "src/**/*.msg"}, {}
        )
        self.assertIn(
            "ros_interface:nav_pkg/msg/Status", result.nodes
        )

    def test_srv_interface_extracted(self) -> None:
        result = ros2_interfaces.extract(
            _FIXTURE, {"glob": "src/**/*.srv"}, {}
        )
        self.assertIn(
            "ros_interface:nav_pkg/srv/SetWaypoint", result.nodes
        )

class Ros2PackageAcceptanceTest(unittest.TestCase):
    """package.xml produces package nodes and dependency edges."""

    def setUp(self) -> None:
        self.result = ros2_package.extract(
            _FIXTURE, {"glob": "src/**/package.xml"}, {}
        )

    def test_nav_pkg_node_extracted(self) -> None:
        self.assertIn("ros_package:nav_pkg", self.result.nodes)

    def test_dependency_edges(self) -> None:
        deps = [
            e["to"]
            for e in self.result.edges
            if e["from"] == "ros_package:nav_pkg"
            and e["type"] == "depends_on"
        ]
        self.assertIn("ros_package:rclpy", deps)
        self.assertIn("ros_package:geometry_msgs", deps)
        self.assertIn("ros_package:sensor_msgs", deps)

    def test_package_fragment_validates(self) -> None:
        errs = validate_fragment(
            {"nodes": self.result.nodes, "edges": self.result.edges},
            source_label="strategy:ros2_package",
            allow_dangling_edges=True,
        )
        self.assertEqual(errs, [], f"validation errors: {errs}")

if __name__ == "__main__":
    unittest.main()
