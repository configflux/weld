"""Example and demo test targets."""

load("@rules_python//python:defs.bzl", "py_test")

def examples_tests():
    py_test(
        name = "weld_examples_test",
        srcs = ["weld_examples_test.py"],
        data = [
            "//examples:example_files",
        ],
        deps = [
            "//weld:contract",
            "//weld:runtime",
            "//weld:yaml",
            "//weld/strategies:helpers",
        ],
    )

    native.filegroup(
        name = "demo_discover_golden_files",
        srcs = native.glob(["golden/demo_discover/*.json"]),
    )

    py_test(
        name = "weld_demo_discover_golden_test",
        srcs = ["weld_demo_discover_golden_test.py"],
        data = [
            ":demo_discover_golden_files",
            "//examples:example_files",
        ],
        deps = [
            "//weld:runtime",
        ],
        env = {"PYTHONHASHSEED": "0"},
    )
