# Stub rclpy source used only by Weld tests; not executed.
#
# Exercises the subscription / lifecycle-node / action-client subset of
# the ``ros2_topology`` (Python half) extractor:
#
# - ``rclpy.lifecycle.LifecycleNode`` subclass (lifecycle: True prop)
# - ``self.create_subscription(sensor_msgs.msg.Image, "camera/image", ...)``
# - ``ActionClient(self, demo_pkg.action.Fibonacci, "fibonacci")``
# - ``self.declare_parameter("frame_id", "camera_link")``

from rclpy.action import ActionClient
from rclpy.lifecycle import LifecycleNode

import demo_pkg.action
import sensor_msgs.msg

class Listener(LifecycleNode):
    def __init__(self) -> None:
        super().__init__("listener")
        self.subscription_ = self.create_subscription(
            sensor_msgs.msg.Image,
            "camera/image",
            self._on_image,
            10,
        )
        self.action_client_ = ActionClient(
            self, demo_pkg.action.Fibonacci, "fibonacci"
        )
        self.declare_parameter("frame_id", "camera_link")

    def _on_image(self, msg) -> None:  # pragma: no cover - fixture only
        del msg
