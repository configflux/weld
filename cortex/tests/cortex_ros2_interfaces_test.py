"""Tests for the ``ros2_interfaces`` extraction strategy.

Each test builds a small
on-disk fixture, runs ``ros2_interfaces.extract`` against it, and
asserts the resulting fragment is well-formed via ``validate_fragment``.
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
from cortex.strategies import ros2_interfaces  # noqa: E402

_MSG = textwrap.dedent(
    """\
    # Example message with a constant, an array, and a header.
    uint8 STATUS_OK = 0
    uint8 STATUS_ERROR = 1

    std_msgs/Header header
    string label
    float64 value
    float64[] samples  # trailing comment should be stripped
    uint8 status
    """
)

_SRV = textwrap.dedent(
    """\
    string key
    float64 value
    ---
    bool success
    string message
    """
)

_ACTION = textwrap.dedent(
    """\
    geometry_msgs/Pose target
    float64 max_speed
    ---
    bool success
    float64 final_distance
    ---
    float64 progress
    float64 current_distance
    """
)

def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

class Ros2InterfacesStrategyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        pkg_root = self.tmp / "src" / "demo_pkg"
        _write(pkg_root / "msg" / "Demo.msg", _MSG)
        _write(pkg_root / "srv" / "Set.srv", _SRV)
        _write(pkg_root / "action" / "Move.action", _ACTION)

    def _run(self, glob: str) -> "ros2_interfaces.StrategyResult":
        return ros2_interfaces.extract(self.tmp, {"glob": glob}, {})

    # -- .msg ---------------------------------------------------------------

    def test_msg_emits_ros_interface_node(self) -> None:
        result = self._run("**/*.msg")
        nid = "ros_interface:demo_pkg/msg/Demo"
        self.assertIn(nid, result.nodes)
        node = result.nodes[nid]
        self.assertEqual(node["type"], "ros_interface")
        self.assertEqual(node["props"]["package"], "demo_pkg")
        self.assertEqual(node["props"]["interface_kind"], "msg")
        self.assertEqual(node["props"]["source_strategy"], "ros2_interfaces")
        self.assertIn("Demo.msg", node["props"]["file"])

    def test_msg_fields_are_parsed(self) -> None:
        result = self._run("**/*.msg")
        fields = result.nodes["ros_interface:demo_pkg/msg/Demo"]["props"][
            "fields"
        ]
        by_name = {f["name"]: f for f in fields}
        # Constants
        self.assertEqual(by_name["STATUS_OK"]["type"], "uint8")
        self.assertEqual(by_name["STATUS_OK"]["default"], "0")
        self.assertEqual(by_name["STATUS_ERROR"]["default"], "1")
        # Regular fields
        self.assertEqual(by_name["header"]["type"], "std_msgs/Header")
        self.assertEqual(by_name["label"]["type"], "string")
        self.assertEqual(by_name["value"]["type"], "float64")
        # Array type preserved as-is
        self.assertEqual(by_name["samples"]["type"], "float64[]")
        # Trailing comment must not leak into the name
        self.assertEqual(by_name["samples"]["name"], "samples")
        # Blank lines and full-line comments are ignored
        self.assertNotIn("Example", by_name)

    def test_msg_contains_edge_to_package(self) -> None:
        result = self._run("**/*.msg")
        pkg_nid = "ros_package:demo_pkg"
        iface_nid = "ros_interface:demo_pkg/msg/Demo"
        self.assertIn(pkg_nid, result.nodes)
        match = [
            e for e in result.edges
            if e["from"] == pkg_nid
            and e["to"] == iface_nid
            and e["type"] == "contains"
        ]
        self.assertEqual(len(match), 1)

    # -- .srv ---------------------------------------------------------------

    def test_srv_emits_request_and_response_fields(self) -> None:
        result = self._run("**/*.srv")
        nid = "ros_interface:demo_pkg/srv/Set"
        self.assertIn(nid, result.nodes)
        props = result.nodes[nid]["props"]
        self.assertEqual(props["interface_kind"], "srv")
        req_names = {f["name"] for f in props["request_fields"]}
        resp_names = {f["name"] for f in props["response_fields"]}
        self.assertEqual(req_names, {"key", "value"})
        self.assertEqual(resp_names, {"success", "message"})
        # Types survived the split
        by_req = {f["name"]: f["type"] for f in props["request_fields"]}
        by_resp = {f["name"]: f["type"] for f in props["response_fields"]}
        self.assertEqual(by_req["key"], "string")
        self.assertEqual(by_resp["success"], "bool")

    def test_srv_contains_edge_to_package(self) -> None:
        result = self._run("**/*.srv")
        contains = [
            e for e in result.edges
            if e["from"] == "ros_package:demo_pkg"
            and e["to"] == "ros_interface:demo_pkg/srv/Set"
            and e["type"] == "contains"
        ]
        self.assertEqual(len(contains), 1)

    # -- .action ------------------------------------------------------------

    def test_action_emits_parent_and_three_sub_interfaces(self) -> None:
        result = self._run("**/*.action")
        parent = "ros_interface:demo_pkg/action/Move"
        self.assertIn(parent, result.nodes)
        for suffix in ("_Goal", "_Result", "_Feedback"):
            self.assertIn(f"{parent}{suffix}", result.nodes)
        self.assertEqual(
            result.nodes[parent]["props"]["interface_kind"], "action"
        )
        self.assertEqual(
            result.nodes[f"{parent}_Goal"]["props"]["interface_kind"],
            "action_Goal",
        )

    def test_action_parent_props_contain_all_three_blocks(self) -> None:
        result = self._run("**/*.action")
        parent = result.nodes["ros_interface:demo_pkg/action/Move"]
        goal_names = {f["name"] for f in parent["props"]["goal_fields"]}
        result_names = {f["name"] for f in parent["props"]["result_fields"]}
        feedback_names = {
            f["name"] for f in parent["props"]["feedback_fields"]
        }
        self.assertEqual(goal_names, {"target", "max_speed"})
        self.assertEqual(result_names, {"success", "final_distance"})
        self.assertEqual(feedback_names, {"progress", "current_distance"})

    def test_action_sub_interface_fields_match_parent_blocks(self) -> None:
        result = self._run("**/*.action")
        goal = result.nodes["ros_interface:demo_pkg/action/Move_Goal"]
        result_node = result.nodes[
            "ros_interface:demo_pkg/action/Move_Result"
        ]
        feedback = result.nodes[
            "ros_interface:demo_pkg/action/Move_Feedback"
        ]
        self.assertEqual(
            {f["name"] for f in goal["props"]["fields"]},
            {"target", "max_speed"},
        )
        self.assertEqual(
            {f["name"] for f in result_node["props"]["fields"]},
            {"success", "final_distance"},
        )
        self.assertEqual(
            {f["name"] for f in feedback["props"]["fields"]},
            {"progress", "current_distance"},
        )

    def test_action_parent_contains_each_sub_interface(self) -> None:
        result = self._run("**/*.action")
        parent = "ros_interface:demo_pkg/action/Move"
        contained = {
            e["to"]
            for e in result.edges
            if e["from"] == parent and e["type"] == "contains"
        }
        for suffix in ("_Goal", "_Result", "_Feedback"):
            self.assertIn(f"{parent}{suffix}", contained)

    def test_action_package_contains_parent_and_each_sub_interface(
        self,
    ) -> None:
        result = self._run("**/*.action")
        pkg = "ros_package:demo_pkg"
        contained = {
            e["to"]
            for e in result.edges
            if e["from"] == pkg and e["type"] == "contains"
        }
        self.assertIn("ros_interface:demo_pkg/action/Move", contained)
        for suffix in ("_Goal", "_Result", "_Feedback"):
            self.assertIn(
                f"ros_interface:demo_pkg/action/Move{suffix}", contained
            )

    # -- Cross-cutting ------------------------------------------------------

    def test_fragment_validates_clean_for_all_three_kinds(self) -> None:
        # Running the strategy three times (once per extension) is the
        # shape we expect when discover.yaml registers separate source
        # entries; the union must also validate as a single fragment.
        nodes: dict = {}
        edges: list = []
        for glob in ("**/*.msg", "**/*.srv", "**/*.action"):
            result = self._run(glob)
            nodes.update(result.nodes)
            edges.extend(result.edges)
        fragment = {"nodes": nodes, "edges": edges}
        errors = validate_fragment(
            fragment, source_label="strategy:ros2_interfaces"
        )
        self.assertEqual(
            errors, [], f"unexpected validation errors: {errors}"
        )

    def test_unknown_extension_is_ignored(self) -> None:
        # A broad glob that also sweeps unrelated files must not produce
        # any interface nodes for non-.msg/.srv/.action files.
        extra = self.tmp / "src" / "demo_pkg" / "README.txt"
        extra.write_text("not an interface", encoding="utf-8")
        result = self._run("**/*")
        for nid in result.nodes:
            if nid.startswith("ros_interface:"):
                self.assertFalse(
                    nid.endswith("README") or nid.endswith(".txt"),
                    f"unexpected interface node: {nid}",
                )

    def test_owning_package_falls_back_to_parent_dir(self) -> None:
        # A .msg dropped directly under a package dir (no msg/ subdir)
        # should still be owned by that directory name.
        odd = self.tmp / "src" / "lonely_pkg"
        odd.mkdir()
        (odd / "Solo.msg").write_text("int32 n\n", encoding="utf-8")
        result = self._run("**/*.msg")
        self.assertIn("ros_interface:lonely_pkg/msg/Solo", result.nodes)
        self.assertIn("ros_package:lonely_pkg", result.nodes)

    def test_malformed_fields_are_skipped_not_fatal(self) -> None:
        # Lines with only one token (e.g. typo'd field) must be dropped
        # silently rather than raising.
        (self.tmp / "src" / "demo_pkg" / "msg" / "Partial.msg").write_text(
            "string only_one_token_line\nfloat64 good\njunkline\n",
            encoding="utf-8",
        )
        result = self._run("**/*.msg")
        fields = result.nodes["ros_interface:demo_pkg/msg/Partial"][
            "props"
        ]["fields"]
        names = {f["name"] for f in fields}
        self.assertIn("only_one_token_line", names)
        self.assertIn("good", names)
        self.assertNotIn("junkline", names)

    # -- Fixture workspace parity ------------------------------------------

    def test_runs_against_committed_ros2_workspace_fixture(self) -> None:
        # Reality check against the on-disk fixture used by the other
        # ros2 tests — asserts the strategy behaves the same whether
        # the files come from tempfile or the committed fixture.
        fixture_root = (
            Path(__file__).resolve().parent
            / "fixtures"
            / "ros2_workspace"
        )
        if not (fixture_root / "src" / "demo_pkg" / "msg" / "Demo.msg").exists():
            self.skipTest("ros2_workspace fixture not available in this run")
        msg_res = ros2_interfaces.extract(
            fixture_root, {"glob": "**/*.msg"}, {}
        )
        srv_res = ros2_interfaces.extract(
            fixture_root, {"glob": "**/*.srv"}, {}
        )
        act_res = ros2_interfaces.extract(
            fixture_root, {"glob": "**/*.action"}, {}
        )
        self.assertIn("ros_interface:demo_pkg/msg/Demo", msg_res.nodes)
        self.assertIn("ros_interface:demo_pkg/srv/Set", srv_res.nodes)
        self.assertIn(
            "ros_interface:demo_pkg/action/Move", act_res.nodes
        )
        self.assertIn(
            "ros_interface:demo_pkg/action/Move_Goal", act_res.nodes
        )

if __name__ == "__main__":
    unittest.main()
