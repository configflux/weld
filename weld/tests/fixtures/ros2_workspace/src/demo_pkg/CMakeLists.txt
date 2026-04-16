cmake_minimum_required(VERSION 3.8)
project(demo_pkg)

# Commented out dep must be ignored by the line-recognizer
# find_package(ignored_pkg REQUIRED)

find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(std_msgs REQUIRED)
find_package(rosidl_default_generators REQUIRED)

rosidl_generate_interfaces(demo_pkg_interfaces
  "msg/Status.msg"
  "srv/Ping.srv"
)

add_executable(demo_talker src/demo_talker.cpp)
ament_target_dependencies(demo_talker rclcpp std_msgs)

add_executable(demo_component src/demo_component.cpp)
ament_target_dependencies(demo_component rclcpp)

rclcpp_components_register_nodes(demo_component "demo_pkg::DemoComponent")

ament_package()
