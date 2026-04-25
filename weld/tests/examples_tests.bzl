"""Example and demo test targets."""

def examples_tests():
    native.py_test(
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
        local = True,
        tags = ["no-sandbox"],
    )

    native.filegroup(
        name = "demo_discover_golden_files",
        srcs = native.glob(["golden/demo_discover/*.json"]),
    )

    native.py_test(
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
        local = True,
        tags = ["no-sandbox"],
    )
