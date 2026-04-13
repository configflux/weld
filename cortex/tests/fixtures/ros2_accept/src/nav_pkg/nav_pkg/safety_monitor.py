# ROS2 acceptance fixture: safety monitor node.
#
# A lifecycle node that subscribes to sensor data and publishes
# emergency stops.
import rclpy
from rclpy.lifecycle import LifecycleNode

import std_msgs.msg
import sensor_msgs.msg

class SafetyMonitor(LifecycleNode):
    def __init__(self):
        super().__init__("safety_monitor")
        self.scan_sub = self.create_subscription(
            sensor_msgs.msg.LaserScan, "scan", self._on_scan, 10
        )
        self.estop_pub = self.create_publisher(
            std_msgs.msg.Bool, "emergency_stop", 10
        )
        self.declare_parameter("min_distance", 0.5)

    def _on_scan(self, msg):
        pass

def main():
    rclpy.init()
    rclpy.spin(SafetyMonitor())
