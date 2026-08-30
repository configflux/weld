"""Cross-repo resolver test targets (the ``//weld/cross_repo`` surface).

The strategies that join a federated workspace into one graph: the resolver
base and its incremental path, drift detection, service-graph and gRPC/channel
/compose topology resolvers, config overrides, package-import resolution, and
the end-to-end polyrepo integration.

The workspace and federation machinery these run on top of lives next door in
federation_tests.bzl.

Extracted verbatim from weld/tests/BUILD.bazel (bd hpv7), including the four
targets that shared one folded line. Names, srcs, deps, and env are unchanged,
so every label stays //weld/tests:<name>.
"""

load("@rules_python//python:defs.bzl", "py_test")

_CROSS_REPO = ["//weld/cross_repo"]

_CROSS_REPO_ONLY = (
    "weld_cross_repo_base_test",
    "weld_cross_repo_incremental_test",
    "weld_drift_detect_test",
    "weld_cross_repo_compose_topology_test",
)

_FEDERATE = (
    "discover_federate_test",
    "discover_federate_origin_test",
    "discover_federate_cpp_origin_test",
    "weld_cross_repo_service_graph_test",
)

_ORIGIN_FIXTURE_USERS = ("discover_federate_origin_test", "discover_federate_cpp_origin_test")

def cross_repo_tests():
    [py_test(
        name = _name,
        srcs = [_name + ".py"],
        deps = _CROSS_REPO,
    ) for _name in _CROSS_REPO_ONLY]

    [py_test(
        name = _name,
        srcs = (["_discover_federate_origin_fixtures.py"] if _name in _ORIGIN_FIXTURE_USERS else []) + [_name + ".py"],
        deps = ["//weld/cross_repo", "//weld:runtime", "//weld:workspace"],
    ) for _name in _FEDERATE]

    py_test(
        name = "weld_cross_repo_grpc_test",
        srcs = ["cross_repo_grpc_fixtures.py", "weld_cross_repo_grpc_test.py"],
        deps = _CROSS_REPO,
    )

    # ADR 0090: channel_binding cross-repo resolver (FakeGraph unit + real
    # MQTT federation).
    py_test(
        name = "weld_cross_repo_channel_test",
        srcs = ["weld_cross_repo_channel_test.py"],
        deps = ["//weld/cross_repo", "//weld:runtime", "//weld:workspace", "//weld/strategies", "//weld/strategies:helpers"],
        env = {"PYTHONHASHSEED": "0"},
    )

    py_test(
        name = "weld_cross_repo_overrides_test",
        srcs = ["weld_cross_repo_overrides_test.py"],
        deps = ["//weld/cross_repo", "//weld:yaml"],
    )

    [py_test(
        name = _name,
        srcs = [_name + ".py"],
        deps = ["//weld/cross_repo", "//weld:workspace"],
    ) for _name in ("weld_package_import_resolver_test", "weld_package_import_resolver_csharp_test")]

    # bd b1k8: integration test against real Graph.load() shape (no _G stub);
    # htf9: shared _iter_nodes helper.
    [py_test(
        name = _name,
        srcs = [_name + ".py"],
        deps = ["//weld/cross_repo", "//weld:runtime", "//weld:contract"],
    ) for _name in ("weld_package_import_resolver_real_graph_test", "weld_cross_repo_iter_nodes_test")]

    py_test(
        name = "weld_polyrepo_integration_test",
        srcs = ["weld_polyrepo_integration_test.py"],
        deps = ["//weld/cross_repo", "//weld:workspace"],
    )

    # Finding 06 (field-eval v0.23.1): package_graph joins manifest package
    # dependencies (PackageReference / pyproject / go.mod) to the producing
    # repo node. Reads manifests off disk, so the test writes real files.
    py_test(
        name = "weld_cross_repo_package_graph_test",
        srcs = ["weld_cross_repo_package_graph_test.py"],
        deps = ["//weld/cross_repo", "//weld:workspace"],
    )
