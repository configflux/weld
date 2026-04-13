// Stub ROS2 C++ source used only by kg tests; not built.
//
// Exercises the surface area of the ``ros2_topology`` (C++ half) extractor:
//   - rclcpp::Node subclass with a runtime name passed to the Node()
//     super-constructor
//   - create_publisher<std_msgs::msg::String>("chatter", ...)
//   - create_service<demo_pkg::srv::Ping>("ping", ...)
//   - create_client<demo_pkg::srv::Ping>("ping_client")
//   - rclcpp_action::create_server<demo_pkg::action::Fibonacci>(...)
//   - declare_parameter<int>("period_ms", 500)
//   - get_parameter("undeclared_flag") (declared: false path)
//   - a dynamic (non-literal) topic name for the fallback id path
//   - RCLCPP_COMPONENTS_REGISTER_NODE(demo_pkg::Talker) at file scope

#include "rclcpp/rclcpp.hpp"
#include "rclcpp_action/rclcpp_action.hpp"
#include "std_msgs/msg/string.hpp"
#include "demo_pkg/srv/ping.hpp"
#include "demo_pkg/action/fibonacci.hpp"

namespace demo_pkg {

class Talker : public rclcpp::Node {
 public:
  Talker() : Node("talker") {
    publisher_ = create_publisher<std_msgs::msg::String>("chatter", 10);
    service_ = create_service<demo_pkg::srv::Ping>(
        "ping", [](const auto, auto) {});
    client_ = create_client<demo_pkg::srv::Ping>("ping_client");
    action_server_ = rclcpp_action::create_server<demo_pkg::action::Fibonacci>(
        this, "fibonacci", nullptr, nullptr, nullptr);
    declare_parameter<int>("period_ms", 500);
    auto flag = get_parameter("undeclared_flag");
    // Dynamic topic name: non-literal first argument.
    dyn_publisher_ = create_publisher<std_msgs::msg::String>(
        topic_name_, 10);
  }

 private:
  std::string topic_name_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr publisher_;
  rclcpp::Publisher<std_msgs::msg::String>::SharedPtr dyn_publisher_;
  rclcpp::Service<demo_pkg::srv::Ping>::SharedPtr service_;
  rclcpp::Client<demo_pkg::srv::Ping>::SharedPtr client_;
  rclcpp_action::Server<demo_pkg::action::Fibonacci>::SharedPtr action_server_;
};

}  // namespace demo_pkg

RCLCPP_COMPONENTS_REGISTER_NODE(demo_pkg::Talker)
