"""ROS 2 contract and strategy test targets.

Extracted verbatim from weld/tests/BUILD.bazel, which indexes subjects rather
than listing targets. Target names, srcs, data, and deps are unchanged so
every label stays //weld/tests:<name>.
"""

load("@rules_python//python:defs.bzl", "py_test")

def ros2_tests():
    py_test(
        name = "weld_contract_ros2_test",
        srcs = ["weld_contract_ros2_test.py"],
        deps = ["//weld:contract", "//weld:runtime"],
    )

    py_test(
        name = "weld_ros2_package_test",
        srcs = ["weld_ros2_package_test.py"],
        deps = [
            "//weld:contract",
            "//weld/strategies",
            "//weld/strategies:helpers",
        ],
    )

    py_test(
        name = "weld_ros2_cmake_test",
        srcs = ["weld_ros2_cmake_test.py"],
        deps = [
            "//weld:contract",
            "//weld/strategies",
            "//weld/strategies:helpers",
        ],
    )

    py_test(
        name = "weld_ros2_interfaces_test",
        srcs = ["weld_ros2_interfaces_test.py"],
        data = [":fixture_files"],
        deps = [
            "//weld:contract",
            "//weld/strategies",
            "//weld/strategies:helpers",
        ],
    )

    py_test(
        name = "weld_ros2_topology_cpp_test",
        srcs = ["weld_ros2_topology_cpp_test.py"],
        data = [":fixture_files", "//weld/languages:query_files"],
        deps = [
            "//weld:contract",
            "//weld/strategies",
            "//weld/strategies:helpers",
        ],
    )

    py_test(
        name = "weld_ros2_topology_py_test",
        srcs = ["weld_ros2_topology_py_test.py"],
        data = [":fixture_files", "//weld/languages:query_files"],
        deps = [
            "//weld:contract",
            "//weld/strategies",
            "//weld/strategies:helpers",
        ],
    )

    py_test(
        name = "weld_ros2_launch_test",
        srcs = ["weld_ros2_launch_test.py"],
        data = [":fixture_files"],
        deps = [
            "//weld:contract",
            "//weld/strategies",
            "//weld/strategies:helpers",
        ],
    )
