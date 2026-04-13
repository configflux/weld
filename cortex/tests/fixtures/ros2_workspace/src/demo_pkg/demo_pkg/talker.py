# Stub rclpy source used only by kg tests; not executed.
#
# Exercises the surface area of the ``ros2_topology`` (Python half)
# extractor, deliberately mirroring ``src/talker.cpp`` so the shared
# contract tests line up:
#
# - ``rclpy.node.Node`` subclass whose ``__init__`` passes the runtime
#   name to ``super().__init__("talker")``
# - ``self.create_publisher(std_msgs.msg.String, "chatter", 10)``
# - ``self.create_service(demo_pkg.srv.Ping, "ping", handler)``
# - ``self.create_client(demo_pkg.srv.Ping, "ping_client")``
# - ``ActionServer(self, demo_pkg.action.Fibonacci, "fibonacci", handler)``
# - ``self.declare_parameter("period_ms", 500)``
# - ``self.get_parameter("undeclared_flag")`` (declared: False path)
# - a dynamic (non-literal) topic name for the fallback id path

import rclpy
from rclpy.action import ActionServer
from rclpy.node import Node

import demo_pkg.action
import demo_pkg.srv
import std_msgs.msg

class Talker(Node):
    def __init__(self) -> None:
        super().__init__("talker")
        self._topic_name = "chatter_dyn"
        self.publisher_ = self.create_publisher(
            std_msgs.msg.String, "chatter", 10
        )
        self.service_ = self.create_service(
            demo_pkg.srv.Ping, "ping", self._handle_ping
        )
        self.client_ = self.create_client(
            demo_pkg.srv.Ping, "ping_client"
        )
        self.action_server_ = ActionServer(
            self,
            demo_pkg.action.Fibonacci,
            "fibonacci",
            self._handle_fibonacci,
        )
        self.declare_parameter("period_ms", 500)
        self.get_parameter("undeclared_flag")
        # Dynamic topic name: non-literal second argument.
        self.dyn_publisher_ = self.create_publisher(
            std_msgs.msg.String, self._topic_name, 10
        )

    def _handle_ping(self, request, response):
        return response

    def _handle_fibonacci(self, goal_handle):
        return goal_handle

def main() -> None:
    rclpy.init()
    node = Talker()
    rclpy.spin(node)

if __name__ == "__main__":
    main()
