# Example ROS2 .msg fixture for the Weld ros2_interfaces extractor.
# Covers primitive fields, an array, and a constant.

uint8 STATUS_OK = 0
uint8 STATUS_ERROR = 1

std_msgs/Header header
string label
float64 value
float64[] samples
uint8 status
