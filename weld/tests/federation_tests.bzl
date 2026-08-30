"""Workspace + federation test targets (the ``//weld:workspace`` surface).

Everything that answers "which repos are in this workspace, and how does a
root read combine them": workspace config and scanning, the child ledger,
federated query/context/render, descent, bootstrap, and the child-staleness
oracle.

Cross-repo *resolvers* -- the ``//weld/cross_repo`` surface that joins the
federated children together -- live next door in cross_repo_tests.bzl.

Extracted verbatim from weld/tests/BUILD.bazel (bd hpv7). Names, srcs, deps,
and env are unchanged, so every label stays //weld/tests:<name>.
"""

load("@rules_python//python:defs.bzl", "py_test")

_RUNTIME_WORKSPACE = ["//weld:runtime", "//weld:workspace"]
_HASHSEED = {"PYTHONHASHSEED": "0"}

# cmpd: weld_workspace_config_test split by responsibility; scanner
# (scan_nested_repos) tests live in weld_workspace_scan_test.
# weld_workspace_config_test is declared on its own below (it needs the
# //weld/cross_repo dep for the allowlist-vs-registry drift guard,
# bd 5038-f74dd), so only the scanner test remains in this loop.
_WORKSPACE_ONLY = (
    "weld_workspace_scan_test",
)

_FEDERATION = (
    "weld_federation_test",
    "weld_federation_status_ordering_test",
    "weld_federation_cache_test",
    "weld_federation_graph_shape_test",
    "weld_context_fallback_test",
    "weld_federation_cli_render_test",
    # ADR 0134 (Finding 02, ...uuxaz.2): a graph-backed federated read at a
    # root with no root graph.json must surface the same cannot-answer
    # "No Weld graph found" guidance + non-zero exit the single-repo path does,
    # not a well-formed empty result at exit 0; wd find stays exempt.
    "weld_federation_missing_graph_test",
    # ADR 0134 (Finding 01, ...uuxaz.3): wd brief federates at a polyrepo root
    # -- it spans child graphs like wd query / weld_brief -- instead of reading
    # only the root meta-graph and returning a silent empty result.
    "weld_federation_brief_test",
)

_DETERMINISTIC = (
    "weld_root_discovery_test",
    "discover_recurse_test",
    "discover_recurse_byte_identity_test",
    "discover_federate_python_origin_test",
    "discover_intra_repo_origin_test",
    "weld_workspace_bootstrap_test",
    "weld_bootstrap_root_sidecar_test",
    "weld_workspace_bootstrap_exclude_test",
    "weld_workspace_bootstrap_gitignore_test",
    "weld_workspace_bootstrap_reset_test",
    "weld_workspace_gitignore_mask_test",
    "weld_workspace_scan_filter_test",
    "weld_discover_output_test",
    "weld_federation_worktree_test",
    "weld_federation_freshness_test",
    "weld_init_worktree_test",
)

# ADR 0066: federated child-staleness oracle (part 1) + workspace status
# 'stale' / root wd stale aggregation (part 2) + auto-recurse stale children
# on root reads (part 3).
_STALENESS = (
    "weld_federation_staleness_test",
    "weld_federation_child_staleness_surface_test",
    "weld_federation_auto_refresh_test",
)

def federation_tests():
    [py_test(
        name = _name,
        srcs = [_name + ".py"],
        deps = ["//weld:workspace"],
    ) for _name in _WORKSPACE_ONLY]

    # bd 5038-f74dd: the loader/validator surface plus its drift guard that
    # pins KNOWN_CROSS_REPO_STRATEGIES to the resolver registry -- hence the
    # extra //weld/cross_repo dep this one test needs on top of //weld:workspace.
    py_test(
        name = "weld_workspace_config_test",
        srcs = ["weld_workspace_config_test.py"],
        deps = ["//weld:workspace", "//weld/cross_repo"],
    )

    py_test(
        name = "weld_workspace_state_test",
        srcs = ["weld_workspace_state_test.py"],
        deps = _RUNTIME_WORKSPACE,
    )

    # cpzx: the ledger writer's own non-polyrepo refusal. Split from
    # weld_workspace_state_test because that file is at the 400-line cap.
    py_test(
        name = "weld_workspace_state_gate_test",
        srcs = ["weld_workspace_state_gate_test.py"],
        deps = _RUNTIME_WORKSPACE,
    )

    # 5038-9jz2: non-dict graph.json payload -> corrupt classification via
    # the shared validate_dict_payload guard. Split from
    # weld_workspace_state_test for the same reason as the gate test above:
    # that file is at the 400-line cap.
    py_test(
        name = "weld_workspace_state_dict_shape_test",
        srcs = ["weld_workspace_state_dict_shape_test.py"],
        deps = _RUNTIME_WORKSPACE,
    )

    [py_test(
        name = _name,
        srcs = [_name + ".py"],
        deps = _RUNTIME_WORKSPACE,
    ) for _name in _FEDERATION]

    py_test(
        name = "weld_federation_descent_test",
        srcs = ["_federation_descent_fixtures.py", "weld_federation_descent_test.py"],
        deps = _RUNTIME_WORKSPACE,
    )

    # ADR 0091: root-less containment-cycle SCC descent anchors.
    py_test(
        name = "weld_federation_descent_cycle_test",
        srcs = ["_federation_descent_fixtures.py", "weld_federation_descent_cycle_test.py"],
        deps = _RUNTIME_WORKSPACE,
    )

    # bd v5t0: multi-token federated fan-out must not starve
    # lexicographically-later children.
    py_test(
        name = "weld_federation_query_starvation_test",
        srcs = ["_federation_sqlite_fixtures.py", "weld_federation_query_starvation_test.py"],
        deps = ["//weld:runtime"],
    )

    [py_test(
        name = _name,
        srcs = [_name + ".py"],
        deps = _RUNTIME_WORKSPACE,
        env = _HASHSEED,
    ) for _name in _DETERMINISTIC + _STALENESS]
