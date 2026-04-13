"""Tests for the ``ros2_launch`` extraction strategy.

The strategy extracts the canonical ``LaunchDescription([Node(...)])``
shape from ``*.launch.py`` files using stdlib ``ast`` — the explicit
non-goal from the epic is full Python evaluation, so only literal
``Node(...)`` kwargs are resolved.  Non-literal kwargs are silently
skipped but must not abort the file.
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
from cortex.strategies import ros2_launch  # noqa: E402

_FIXTURE_ROOT = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "ros2_workspace"
)
_LAUNCH_REL = "src/demo_pkg/launch/demo.launch.py"

class Ros2LaunchFixtureTest(unittest.TestCase):
    """Run the strategy against the bundled demo.launch.py fixture."""

    def setUp(self) -> None:
        self.result = ros2_launch.extract(
            _FIXTURE_ROOT,
            {"glob": "src/**/*.launch.py"},
            {},
        )

    def test_discovered_from_includes_launch_file(self) -> None:
        self.assertIn(_LAUNCH_REL, self.result.discovered_from)

    def test_talker_node_emitted_with_launch_keying(self) -> None:
        nid = "ros_node:demo_pkg/talker"
        self.assertIn(nid, self.result.nodes)
        node = self.result.nodes[nid]
        self.assertEqual(node["type"], "ros_node")
        props = node["props"]
        self.assertEqual(props.get("package"), "demo_pkg")
        self.assertEqual(props.get("executable"), "talker")
        self.assertEqual(props.get("runtime_name"), "talker")
        self.assertEqual(props.get("source_strategy"), "ros2_launch")
        self.assertEqual(props.get("file"), _LAUNCH_REL)

    def test_listener_node_emitted(self) -> None:
        nid = "ros_node:demo_pkg/listener"
        self.assertIn(nid, self.result.nodes)
        props = self.result.nodes[nid]["props"]
        self.assertEqual(props.get("executable"), "listener")
        self.assertEqual(props.get("runtime_name"), "listener")

    def test_dynamic_executable_node_still_emitted_without_executable(
        self,
    ) -> None:
        # A Node(...) with a non-literal ``executable=`` kwarg must still
        # produce a ros_node so launch coverage isn't destroyed by a
        # single runtime expression.  We key by package+name when the
        # executable is not a literal string.
        nid = "ros_node:demo_pkg/dynamic_exec"
        self.assertIn(nid, self.result.nodes)
        props = self.result.nodes[nid]["props"]
        self.assertEqual(props.get("package"), "demo_pkg")
        # Non-literal executable -> field omitted, not set to a bogus str.
        self.assertNotIn("executable", props)

    def test_launch_emits_orchestrates_from_file_to_ros_node(self) -> None:
        # The .launch.py file is the orchestrator; every Node(...) in the
        # LaunchDescription becomes an ``orchestrates`` edge from the
        # file sentinel to the ros_node.
        file_nid = f"file:{_LAUNCH_REL}"
        self.assertIn(file_nid, self.result.nodes)
        self.assertEqual(self.result.nodes[file_nid]["type"], "file")
        targets = sorted(
            e["to"] for e in self.result.edges
            if e["from"] == file_nid and e["type"] == "orchestrates"
        )
        self.assertEqual(
            targets,
            [
                "ros_node:demo_pkg/dynamic_exec",
                "ros_node:demo_pkg/listener",
                "ros_node:demo_pkg/talker",
            ],
        )

    def test_launch_emits_depends_on_from_node_to_package(self) -> None:
        # Each launch-emitted ros_node ties back to its ros_package
        # sentinel.  This is how downstream queries can go from a launch
        # entry back to the package that ships the executable.
        edges = [
            e for e in self.result.edges
            if e["type"] == "depends_on"
            and e["to"] == "ros_package:demo_pkg"
            and e["from"].startswith("ros_node:demo_pkg/")
        ]
        froms = sorted({e["from"] for e in edges})
        self.assertEqual(
            froms,
            [
                "ros_node:demo_pkg/dynamic_exec",
                "ros_node:demo_pkg/listener",
                "ros_node:demo_pkg/talker",
            ],
        )

    def test_launch_parameters_emit_configures_edges(self) -> None:
        # The talker Node has parameters=[{"period_ms": 500, "prefix": ...}].
        # Each dict literal entry becomes a ros_parameter node + configures
        # edge from the launch-emitted ros_node.
        period_nid = "ros_parameter:talker/period_ms"
        prefix_nid = "ros_parameter:talker/prefix"
        self.assertIn(period_nid, self.result.nodes)
        self.assertIn(prefix_nid, self.result.nodes)
        # Launch-derived params are declared (they're live at launch).
        self.assertTrue(
            self.result.nodes[period_nid]["props"].get("declared")
        )
        expected = {period_nid, prefix_nid}
        actual = {
            e["to"] for e in self.result.edges
            if e["from"] == "ros_node:demo_pkg/talker"
            and e["type"] == "configures"
        }
        self.assertTrue(expected.issubset(actual))

    def test_launch_remapping_emits_relates_to_topic_edge(self) -> None:
        # The listener Node has
        # remappings=[("chatter", "chatter_remapped")].  The extractor
        # must emit a ros_topic sentinel for the *target* (the name the
        # listener actually subscribes to at runtime) and a relates_to
        # edge that carries the original topic as ``remap_from``.
        topic_nid = "ros_topic:chatter_remapped"
        self.assertIn(topic_nid, self.result.nodes)
        self.assertEqual(
            self.result.nodes[topic_nid]["type"], "ros_topic"
        )
        edge = next(
            (
                e for e in self.result.edges
                if e["from"] == "ros_node:demo_pkg/listener"
                and e["to"] == topic_nid
                and e["type"] == "relates_to"
            ),
            None,
        )
        self.assertIsNotNone(edge)
        self.assertEqual(edge["props"].get("kind"), "remap")
        self.assertEqual(edge["props"].get("remap_from"), "chatter")

    def test_launch_ignores_non_launch_py_files(self) -> None:
        # The extractor's own glob excludes plain .py files.  Sanity
        # check: the talker.py file at
        # src/demo_pkg/demo_pkg/talker.py must not produce any
        # ``ros_node:demo_pkg.talker.Talker`` because that is the
        # topology layer's job.
        self.assertNotIn(
            "ros_node:demo_pkg.talker.Talker", self.result.nodes
        )

    def test_fragment_validates_clean(self) -> None:
        fragment = {
            "nodes": self.result.nodes,
            "edges": list(self.result.edges),
        }
        errors = validate_fragment(
            fragment,
            source_label="strategy:ros2_launch",
            allow_dangling_edges=True,
        )
        self.assertEqual(
            errors, [], f"unexpected validation errors: {errors}"
        )

class Ros2LaunchEdgeCaseTest(unittest.TestCase):
    """Isolated cases that are awkward to express in the shared fixture."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def _write(self, rel: str, body: str) -> None:
        path = self.tmp / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    def _run(self):
        return ros2_launch.extract(
            self.tmp, {"glob": "**/*.launch.py"}, {}
        )

    def test_non_canonical_function_shape_is_ignored(self) -> None:
        # A .launch.py file that does not contain a
        # ``LaunchDescription([...])`` list literal must be skipped
        # silently — the extractor is not a general Python interpreter.
        self._write(
            "weird.launch.py",
            textwrap.dedent(
                """\
                def generate_launch_description():
                    desc = LaunchDescription()
                    desc.add_action(Node(package="p", executable="e"))
                    return desc
                """
            ),
        )
        result = self._run()
        # No nodes should come out of a non-canonical shape.
        self.assertEqual(
            [nid for nid in result.nodes if nid.startswith("ros_node:")],
            [],
        )

    def test_missing_package_or_executable_skips_node_entry(self) -> None:
        # A Node(...) entry with neither a literal ``package`` nor a
        # literal ``name`` cannot be keyed; the extractor skips it but
        # must still process sibling entries.
        self._write(
            "mixed.launch.py",
            textwrap.dedent(
                """\
                from launch import LaunchDescription
                from launch_ros.actions import Node

                def generate_launch_description():
                    return LaunchDescription([
                        Node(executable="ghost"),  # no package, no name
                        Node(package="p", executable="x", name="x1"),
                    ])
                """
            ),
        )
        result = self._run()
        self.assertIn("ros_node:p/x1", result.nodes)
        # Only the well-formed entry survives.
        launch_nodes = [
            nid for nid in result.nodes if nid.startswith("ros_node:")
        ]
        self.assertEqual(launch_nodes, ["ros_node:p/x1"])

    def test_syntax_error_file_is_skipped(self) -> None:
        # An unparseable launch file must not abort the strategy — it
        # is simply skipped and later files keep extracting.
        self._write("broken.launch.py", "class :::\n")
        self._write(
            "ok.launch.py",
            textwrap.dedent(
                """\
                from launch import LaunchDescription
                from launch_ros.actions import Node

                def generate_launch_description():
                    return LaunchDescription([
                        Node(package="p", executable="e", name="e"),
                    ])
                """
            ),
        )
        result = self._run()
        self.assertIn("ros_node:p/e", result.nodes)

    def test_non_literal_remap_tuples_are_skipped(self) -> None:
        # ``remappings=[(src, "fixed")]`` where src is a variable must
        # not crash the extractor; the ros_node itself still lands and
        # literal sibling tuples still emit their edges.
        self._write(
            "remap.launch.py",
            textwrap.dedent(
                """\
                from launch import LaunchDescription
                from launch_ros.actions import Node

                src = "scan"

                def generate_launch_description():
                    return LaunchDescription([
                        Node(
                            package="p",
                            executable="e",
                            name="n",
                            remappings=[
                                (src, "ignored"),
                                ("tick", "tock"),
                            ],
                        ),
                    ])
                """
            ),
        )
        result = self._run()
        self.assertIn("ros_node:p/n", result.nodes)
        topic_nids = sorted(
            nid for nid in result.nodes if nid.startswith("ros_topic:")
        )
        self.assertEqual(topic_nids, ["ros_topic:tock"])

    def test_non_dict_parameters_are_skipped(self) -> None:
        # ``parameters=[config_path]`` (a variable reference) is a
        # common real-world shape.  The extractor must not attempt to
        # synthesise ros_parameter nodes from non-dict entries; the
        # ros_node itself still lands.
        self._write(
            "params.launch.py",
            textwrap.dedent(
                """\
                from launch import LaunchDescription
                from launch_ros.actions import Node

                config_path = "/etc/foo.yaml"

                def generate_launch_description():
                    return LaunchDescription([
                        Node(
                            package="p",
                            executable="e",
                            name="n",
                            parameters=[config_path],
                        ),
                    ])
                """
            ),
        )
        result = self._run()
        self.assertIn("ros_node:p/n", result.nodes)
        params = [
            nid for nid in result.nodes if nid.startswith("ros_parameter:")
        ]
        self.assertEqual(params, [])

if __name__ == "__main__":
    unittest.main()
