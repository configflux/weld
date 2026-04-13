"""Tests for the ``ros2_topology`` extraction strategy — C++ half.

Exercises the runtime
topology extractor against the ``talker.cpp`` / ``listener.cpp``
fixtures under ``cortex/tests/fixtures/ros2_workspace/src/demo_pkg/src``.

The strategy is a line/token recogniser (no tree-sitter dependency) so
these tests run in the Bazel sandbox without optional grammars.
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

from cortex.contract import validate_fragment  # noqa: E402
from cortex.strategies import ros2_topology  # noqa: E402

_FIXTURE_ROOT = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "ros2_workspace"
)

class Ros2TopologyCppFixtureTest(unittest.TestCase):
    """Run the strategy against the bundled talker/listener fixtures."""

    def setUp(self) -> None:
        self.result = ros2_topology.extract(
            _FIXTURE_ROOT,
            {"glob": "src/**/*.cpp"},
            {},
        )

    def test_talker_emits_ros_node_with_runtime_name(self) -> None:
        nid = "ros_node:demo_pkg::Talker"
        self.assertIn(nid, self.result.nodes)
        node = self.result.nodes[nid]
        self.assertEqual(node["type"], "ros_node")
        props = node["props"]
        self.assertEqual(props.get("class_name"), "demo_pkg::Talker")
        self.assertEqual(props.get("runtime_name"), "talker")
        # Non-lifecycle talker must not be marked lifecycle: true.
        self.assertFalse(props.get("lifecycle", False))

    def test_listener_lifecycle_node_flagged(self) -> None:
        nid = "ros_node:demo_pkg::Listener"
        self.assertIn(nid, self.result.nodes)
        node = self.result.nodes[nid]
        self.assertTrue(node["props"].get("lifecycle"))
        self.assertEqual(node["props"].get("runtime_name"), "listener")

    def test_composable_node_registration_marks_composable(self) -> None:
        nid = "ros_node:demo_pkg::Talker"
        self.assertTrue(self.result.nodes[nid]["props"].get("composable"))

    def test_publisher_creates_topic_and_produces_edge(self) -> None:
        topic_nid = "ros_topic:chatter"
        self.assertIn(topic_nid, self.result.nodes)
        topic = self.result.nodes[topic_nid]
        self.assertEqual(topic["type"], "ros_topic")
        self.assertEqual(
            topic["props"].get("message_type"), "std_msgs/msg/String"
        )
        self.assertFalse(topic["props"].get("dynamic", False))
        edge = next(
            (
                e for e in self.result.edges
                if e["from"] == "ros_node:demo_pkg::Talker"
                and e["to"] == topic_nid
                and e["type"] == "produces"
            ),
            None,
        )
        self.assertIsNotNone(edge)

    def test_publisher_wires_topic_to_interface(self) -> None:
        topic_nid = "ros_topic:chatter"
        iface_nid = "ros_interface:std_msgs/msg/String"
        self.assertIn(iface_nid, self.result.nodes)
        impl = next(
            (
                e for e in self.result.edges
                if e["from"] == topic_nid
                and e["to"] == iface_nid
                and e["type"] == "implements"
            ),
            None,
        )
        self.assertIsNotNone(impl)

    def test_dynamic_topic_name_gets_dynamic_sentinel(self) -> None:
        # The second publisher in talker.cpp uses a non-literal first
        # argument: create_publisher<...>(topic_name_, ...).  It must
        # become a ros_topic with dynamic: true and a counter-qualified
        # id so multiple dynamic sites do not collide.
        dyn = [
            nid for nid in self.result.nodes
            if nid.startswith("ros_topic:<dynamic>:demo_pkg::Talker/")
        ]
        self.assertEqual(len(dyn), 1, f"dynamic topics: {dyn}")
        topic = self.result.nodes[dyn[0]]
        self.assertTrue(topic["props"].get("dynamic"))
        # And there must be a produces edge from the owning node.
        edge = next(
            (
                e for e in self.result.edges
                if e["from"] == "ros_node:demo_pkg::Talker"
                and e["to"] == dyn[0]
                and e["type"] == "produces"
            ),
            None,
        )
        self.assertIsNotNone(edge)

    def test_subscription_creates_consumes_edge(self) -> None:
        topic_nid = "ros_topic:camera/image"
        self.assertIn(topic_nid, self.result.nodes)
        self.assertEqual(
            self.result.nodes[topic_nid]["props"].get("message_type"),
            "sensor_msgs/msg/Image",
        )
        edge = next(
            (
                e for e in self.result.edges
                if e["from"] == "ros_node:demo_pkg::Listener"
                and e["to"] == topic_nid
                and e["type"] == "consumes"
            ),
            None,
        )
        self.assertIsNotNone(edge)

    def test_service_server_emits_exposes_edge(self) -> None:
        svc_nid = "ros_service:ping"
        self.assertIn(svc_nid, self.result.nodes)
        self.assertEqual(self.result.nodes[svc_nid]["type"], "ros_service")
        self.assertEqual(
            self.result.nodes[svc_nid]["props"].get("service_type"),
            "demo_pkg/srv/Ping",
        )
        edge = next(
            (
                e for e in self.result.edges
                if e["from"] == "ros_node:demo_pkg::Talker"
                and e["to"] == svc_nid
                and e["type"] == "exposes"
            ),
            None,
        )
        self.assertIsNotNone(edge)

    def test_service_client_emits_consumes_edge(self) -> None:
        svc_nid = "ros_service:ping_client"
        self.assertIn(svc_nid, self.result.nodes)
        edge = next(
            (
                e for e in self.result.edges
                if e["from"] == "ros_node:demo_pkg::Talker"
                and e["to"] == svc_nid
                and e["type"] == "consumes"
            ),
            None,
        )
        self.assertIsNotNone(edge)

    def test_action_server_emits_exposes_edge(self) -> None:
        act_nid = "ros_action:fibonacci"
        self.assertIn(act_nid, self.result.nodes)
        self.assertEqual(self.result.nodes[act_nid]["type"], "ros_action")
        self.assertEqual(
            self.result.nodes[act_nid]["props"].get("action_type"),
            "demo_pkg/action/Fibonacci",
        )
        exposes = [
            e for e in self.result.edges
            if e["from"] == "ros_node:demo_pkg::Talker"
            and e["to"] == act_nid
            and e["type"] == "exposes"
        ]
        self.assertTrue(exposes)

    def test_action_client_emits_consumes_edge(self) -> None:
        act_nid = "ros_action:fibonacci"
        consumes = [
            e for e in self.result.edges
            if e["from"] == "ros_node:demo_pkg::Listener"
            and e["to"] == act_nid
            and e["type"] == "consumes"
        ]
        self.assertTrue(consumes)

    def test_declare_parameter_emits_configures_edge(self) -> None:
        param_nid = "ros_parameter:demo_pkg::Talker/period_ms"
        self.assertIn(param_nid, self.result.nodes)
        props = self.result.nodes[param_nid]["props"]
        self.assertTrue(props.get("declared"))
        self.assertEqual(props.get("parameter_type"), "int")
        edge = next(
            (
                e for e in self.result.edges
                if e["from"] == "ros_node:demo_pkg::Talker"
                and e["to"] == param_nid
                and e["type"] == "configures"
            ),
            None,
        )
        self.assertIsNotNone(edge)

    def test_get_parameter_without_declare_marks_undeclared(self) -> None:
        # The talker does ``get_parameter("undeclared_flag")`` with no
        # matching declare_parameter — the extractor must still emit the
        # parameter node but with declared: false.
        nid = "ros_parameter:demo_pkg::Talker/undeclared_flag"
        self.assertIn(nid, self.result.nodes)
        self.assertFalse(
            self.result.nodes[nid]["props"].get("declared", True)
        )

    def test_fragment_validates_clean(self) -> None:
        fragment = {
            "nodes": self.result.nodes,
            "edges": list(self.result.edges),
        }
        errors = validate_fragment(
            fragment,
            source_label="strategy:ros2_topology",
            allow_dangling_edges=True,
        )
        self.assertEqual(
            errors, [], f"unexpected validation errors: {errors}"
        )

class Ros2TopologyCppEdgeCaseTest(unittest.TestCase):
    """Isolated cases that are awkward to express in the shared fixture."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def _write(self, rel: str, body: str) -> None:
        path = self.tmp / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    def _run(self) -> ros2_topology.StrategyResult:
        return ros2_topology.extract(
            self.tmp, {"glob": "**/*.cpp"}, {}
        )

    def test_file_scope_publisher_binds_to_file_caller_sentinel(self) -> None:
        # A publisher created at file scope (not inside a class body) has
        # no enclosing ros_node, so the extractor must bind it to a file
        # caller sentinel matching the layer-1 convention.
        self._write(
            "standalone.cpp",
            textwrap.dedent(
                """\
                #include "rclcpp/rclcpp.hpp"
                static auto global_pub =
                    rclcpp::create_publisher<std_msgs::msg::String>(
                        "file_topic", 10);
                """
            ),
        )
        result = self._run()
        topic_nid = "ros_topic:file_topic"
        self.assertIn(topic_nid, result.nodes)
        # The source node for the produces edge is the layer-1 convention
        # <lang>:<module>:<file> sentinel.  We accept any ``symbol:cpp:``
        # caller that ends with ``:<file>``.
        file_edges = [
            e for e in result.edges
            if e["to"] == topic_nid
            and e["type"] == "produces"
            and e["from"].startswith("symbol:cpp:")
            and e["from"].endswith(":<file>")
        ]
        self.assertEqual(len(file_edges), 1, f"edges: {result.edges}")

    def test_unresolved_message_type_does_not_break_extraction(self) -> None:
        # A templated message type whose string contains no ``::`` should
        # still emit the topic with an unresolved sentinel message_type
        # and not blow up.
        self._write(
            "weird.cpp",
            textwrap.dedent(
                """\
                #include "rclcpp/rclcpp.hpp"
                namespace demo { class Quirky : public rclcpp::Node {
                 public:
                  Quirky() : Node("quirky") {
                    p_ = create_publisher<MyAlias>("weird", 10);
                  }
                  rclcpp::Publisher<MyAlias>::SharedPtr p_;
                }; }
                """
            ),
        )
        result = self._run()
        self.assertIn("ros_node:demo::Quirky", result.nodes)
        self.assertIn("ros_topic:weird", result.nodes)
        self.assertEqual(
            result.nodes["ros_topic:weird"]["props"].get("message_type"),
            "<unresolved>",
        )

if __name__ == "__main__":
    unittest.main()
