"""Docs / templates / brief test targets.

Extracted verbatim from weld/tests/BUILD.bazel, which indexes subjects rather
than listing targets. Target names, srcs, data, and deps are unchanged so
every label stays //weld/tests:<name>.
"""

load("@rules_python//python:defs.bzl", "py_test")

def docs_tests():
    py_test(
        name = "weld_templates_test",
        srcs = ["weld_templates_test.py"],
        data = [
            "//weld/templates",
            "//weld/docs",
        ],
        deps = [
            "//weld:contract",
            "//weld:runtime",
            "//weld:yaml",
            "//weld/strategies:helpers",
        ],
    )

    py_test(
        name = "weld_docs_validation_test",
        srcs = ["weld_docs_validation_test.py"],
        data = [
            "//weld/docs",
            "//weld/templates",
        ],
        deps = [
            "//weld:contract",
            "//weld:runtime",
        ],
    )

    py_test(
        name = "weld_agent_workflow_doc_test",
        srcs = ["weld_agent_workflow_doc_test.py"],
        data = [
            "//weld/docs",
        ],
        deps = ["//weld:runtime"],
    )

    [py_test(
        name = _name,
        srcs = [_name + ".py"],
        deps = ["//weld:contract", "//weld:runtime"],
    ) for _name in ("weld_brief_test", "weld_brief_v2_test", "weld_trace_test", "weld_warnings_test", "weld_brief_or_fallback_test")]

    py_test(
        name = "weld_brief_cli_test",
        srcs = ["weld_brief_cli_test.py"],
        deps = [
            "//weld:contract",
            "//weld:runtime",
        ],
    )

    py_test(
        name = "weld_synonym_expansion_test",
        srcs = ["weld_synonym_expansion_test.py"],
        deps = [
            "//weld:runtime",
        ],
    )
