"""Framework / source strategy + enrichment-metadata test targets.

Extracted verbatim from weld/tests/BUILD.bazel (line-count policy: the test
BUILD is shrink-only). Target names, srcs, data, and deps are unchanged so
every label stays //weld/tests:<name>.
"""

load("@rules_python//python:defs.bzl", "py_test")

def strategy_tests():
    py_test(
        name = "weld_confidence_discrimination_test",
        srcs = ["weld_confidence_discrimination_test.py"],
        deps = [
            "//weld:runtime",
            "//weld/strategies",
            "//weld/strategies:helpers",
        ],
    )

    py_test(
        name = "weld_scaffold_test",
        srcs = ["weld_scaffold_test.py"],
        deps = ["//weld:runtime"],
    )

    py_test(
        name = "weld_normalized_metadata_test",
        srcs = ["weld_normalized_metadata_test.py"],
        deps = [
            "//weld:contract",
            "//weld:runtime",
            "//weld:yaml",
            "//weld/strategies",
            "//weld/strategies:helpers",
        ],
    )

    py_test(
        name = "weld_normalized_metadata_edge_test",
        srcs = ["weld_normalized_metadata_edge_test.py"],
        deps = [
            "//weld:contract",
            "//weld:runtime",
            "//weld:yaml",
            "//weld/strategies",
            "//weld/strategies:helpers",
        ],
    )

    py_test(
        name = "weld_fastapi_enrichment_test",
        srcs = ["weld_fastapi_enrichment_test.py"],
        deps = [
            "//weld:contract",
            "//weld:runtime",
            "//weld:yaml",
            "//weld/strategies",
            "//weld/strategies:helpers",
        ],
    )

    py_test(
        name = "weld_http_client_strategy_test",
        srcs = ["weld_http_client_strategy_test.py"],
        deps = ["//weld:contract", "//weld/strategies", "//weld/strategies:helpers"],
    )

    py_test(
        name = "weld_grpc_proto_strategy_test",
        srcs = ["weld_grpc_proto_strategy_test.py"],
        data = [":fixture_files"],
        deps = ["//weld:contract", "//weld/strategies", "//weld/strategies:helpers"],
    )

    py_test(
        name = "weld_dds_idl_strategy_test",
        srcs = ["weld_dds_idl_strategy_test.py"],
        deps = ["//weld:contract", "//weld/strategies", "//weld/strategies:helpers"],
    )

    py_test(
        name = "weld_doc_authority_test",
        srcs = ["weld_doc_authority_test.py"],
        deps = ["//weld:contract", "//weld:runtime", "//weld:yaml", "//weld/strategies", "//weld/strategies:helpers"],
    )

    py_test(
        name = "weld_manifest_strategy_test",
        srcs = ["weld_manifest_strategy_test.py"],
        deps = [
            "//weld/strategies",
            "//weld/strategies:helpers",
        ],
    )

    py_test(
        name = "weld_gh_workflow_strategy_test",
        srcs = ["weld_gh_workflow_strategy_test.py"],
        deps = [
            "//weld/strategies",
            "//weld/strategies:helpers",
        ],
    )

    py_test(
        name = "weld_deploy_surface_strategy_test",
        srcs = ["weld_deploy_surface_strategy_test.py"],
        deps = [
            "//weld/strategies",
            "//weld/strategies:helpers",
        ],
    )

    py_test(
        name = "weld_runbook_strategy_test",
        srcs = ["weld_runbook_strategy_test.py"],
        deps = [
            "//weld/strategies",
            "//weld/strategies:helpers",
        ],
    )

    py_test(
        name = "weld_viz_frontend_strategy_test",
        srcs = ["weld_viz_frontend_strategy_test.py"],
        deps = [
            "//weld:contract",
            "//weld:node_ids",
            "//weld/strategies",
            "//weld/strategies:helpers",
        ],
    )
