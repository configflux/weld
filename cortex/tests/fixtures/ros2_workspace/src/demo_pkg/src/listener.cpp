// Stub ROS2 C++ source used only by kg tests; not built.
//
// Exercises the subscription / lifecycle-node / client subset of the
// ``ros2_topology`` (C++ half) extractor:
//   - rclcpp_lifecycle::LifecycleNode subclass (lifecycle: true prop)
//   - create_subscription<sensor_msgs::msg::Image>("camera/image", ...)
//   - rclcpp_action::create_client<demo_pkg::action::Fibonacci>(...)
//   - declare_parameter<std::string>("frame_id", "camera_link")

#include "rclcpp/rclcpp.hpp"
#include "rclcpp_lifecycle/lifecycle_node.hpp"
#include "rclcpp_action/rclcpp_action.hpp"
#include "sensor_msgs/msg/image.hpp"
#include "demo_pkg/action/fibonacci.hpp"

namespace demo_pkg {

class Listener : public rclcpp_lifecycle::LifecycleNode {
 public:
  Listener() : LifecycleNode("listener") {
    subscription_ = create_subscription<sensor_msgs::msg::Image>(
        "camera/image", 10, [](sensor_msgs::msg::Image::SharedPtr) {});
    action_client_ = rclcpp_action::create_client<demo_pkg::action::Fibonacci>(
        this, "fibonacci");
    declare_parameter<std::string>("frame_id", "camera_link");
  }

 private:
  rclcpp::Subscription<sensor_msgs::msg::Image>::SharedPtr subscription_;
  rclcpp_action::Client<demo_pkg::action::Fibonacci>::SharedPtr action_client_;
};

}  // namespace demo_pkg
