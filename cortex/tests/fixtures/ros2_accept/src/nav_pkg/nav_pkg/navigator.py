# ROS2 acceptance fixture: navigator node.
#
# Exercises topic publishing, subscription, and service creation
# for the ros2_topology strategy (Python half).
import rclpy
from rclpy.node import Node

import nav_pkg.srv
import geometry_msgs.msg

class Navigator(Node):
    def __init__(self):
        super().__init__("navigator")
        self.cmd_pub = self.create_publisher(
            geometry_msgs.msg.Twist, "cmd_vel", 10
        )
        self.odom_sub = self.create_subscription(
            geometry_msgs.msg.Odometry, "odom", self._on_odom, 10
        )
        self.waypoint_srv = self.create_service(
            nav_pkg.srv.SetWaypoint, "set_waypoint", self._set_wp
        )
        self.declare_parameter("max_speed", 1.0)

    def _on_odom(self, msg):
        pass

    def _set_wp(self, request, response):
        return response

def main():
    rclpy.init()
    rclpy.spin(Navigator())
