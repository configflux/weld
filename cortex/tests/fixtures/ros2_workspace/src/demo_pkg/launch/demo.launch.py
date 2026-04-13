# Stub ROS2 launch file used only by kg tests; not executed.
#
# Exercises the canonical LaunchDescription([Node(...)]) shape recognised
# by the ``ros2_launch`` extractor.  The shape is:
#
#   from launch import LaunchDescription
#   from launch_ros.actions import Node
#
#   def generate_launch_description():
#       return LaunchDescription([
#           Node(package=..., executable=..., name=..., parameters=[...]),
#           ...
#       ])
#
# The extractor must resolve the three literal kwargs (``package``,
# ``executable``, ``name``), map dict-literal parameters to
# ``ros_parameter`` nodes, and ignore any non-literal kwargs rather than
# attempting general Python evaluation.

from launch import LaunchDescription
from launch_ros.actions import Node

def generate_launch_description():
    return LaunchDescription([
        Node(
            package="demo_pkg",
            executable="talker",
            name="talker",
            parameters=[{"period_ms": 500, "prefix": "demo"}],
        ),
        Node(
            package="demo_pkg",
            executable="listener",
            name="listener",
            remappings=[("chatter", "chatter_remapped")],
        ),
        # Dynamic executable: non-literal kwarg must be skipped without
        # aborting the file. The canonical package+name pair is still
        # enough to emit a ros_node.
        Node(
            package="demo_pkg",
            executable=some_runtime_expr(),  # noqa: F821
            name="dynamic_exec",
        ),
    ])
