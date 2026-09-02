"""Framework / source strategy + enrichment-metadata test targets.

Extracted verbatim from weld/tests/BUILD.bazel, which indexes subjects rather
than listing targets. Target names, srcs, data, and deps are unchanged so
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

    # bd 5038-rhuc: systematic node/edge contract-conformance check,
    # generalizing rgru's per-strategy `test_emitted_nodes_satisfy_the_
    # contract` (python_package_strategy_test.py, weld_csharp_package_
    # strategy_test.py) into one reusable checker. Hermetic unit fixtures
    # here; the real-repo zero-violations gate is
    # weld_node_edge_contract_repo_test (BUILD.bazel, tags=["external"] --
    # same shape as weld_cross_source_edge_provenance_repo_test).
    py_test(
        name = "weld_graph_contract_check_test",
        srcs = ["weld_graph_contract_check_test.py"],
        deps = [
            "//weld:contract",
            "//weld:runtime",
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

    # bd 5038-lrnx1.4 / ADR 0142 D3: the two maps that make a first-party
    # TypeScript import spelling bind to its file, and the resolution path
    # they feed. Three targets rather than one because they answer to three
    # different surfaces -- the npm manifest, the tsconfig, and the graph --
    # and a merged file would be past the 400-line cap on arrival.
    py_test(
        name = "weld_ts_workspace_members_test",
        srcs = ["weld_ts_workspace_members_test.py"],
        deps = ["//weld/strategies"],
    )

    py_test(
        name = "weld_ts_tsconfig_paths_test",
        srcs = ["weld_ts_tsconfig_paths_test.py"],
        deps = ["//weld/strategies"],
    )

    # //weld:runtime for graph_closure: this is where the strategy-side map
    # and the closure-side consumer are checked against each other, which is
    # the seam the whole fix runs through.
    py_test(
        name = "weld_ts_first_party_resolution_test",
        srcs = ["weld_ts_first_party_resolution_test.py"],
        deps = [
            "//weld:runtime",
            "//weld/strategies",
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

    # bd 5038-lrnx1.7 / ADR 0142 D4: the Next.js app-router strategy. The
    # derivation rules (app-directory anchor, route groups, parallel slots,
    # private folders, dynamic segments) are unit-tested on paths; the
    # extraction half runs over a tree on disk.
    py_test(
        name = "weld_next_strategy_test",
        srcs = ["weld_next_strategy_test.py"],
        deps = [
            "//weld:contract",
            "//weld:node_ids",
            "//weld/strategies",
            "//weld/strategies:helpers",
        ],
    )

    # bd iurvv: the route boundary-file placeholder, structurally. Every
    # `boundary_file_node` in weld/strategies is DISCOVERED by the test (an
    # AST scan of the package's own sources) rather than listed, so the deps
    # are the strategy package plus the two production rules the assertions
    # are phrased in: the merge veto and the confidence vocabulary. It carries
    # no grammar and shells nothing -- the system-level half is the e2e target
    # below.
    py_test(
        name = "weld_route_boundary_placeholder_test",
        srcs = ["weld_route_boundary_placeholder_test.py"],
        deps = [
            "//weld:contract",
            "//weld:discover_node_merge",
            "//weld/strategies",
            "//weld/strategies:helpers",
        ],
    )

    # bd iurvv, system level: two source entries claiming one file: id, and
    # what the orchestrator does with them. Only a whole `wd discover` shows
    # it -- each strategy is correct alone -- so this shells the real CLI over
    # a real git repo the way weld_discover_segment_glob_e2e_test does.
    #
    # The three grammars are declared, not hoped for: without them the
    # tree-sitter pass mints no file node and every evidence assertion would
    # pass vacuously, which is the one way a defect-reproducing probe can lie.
    # All three are in third_party/python/requirements_lock.txt, so this stays
    # hermetic and untagged in the fast loop rather than joining the ADR 0069
    # ambient lane (the reasoning node_eval_tests.bzl records for the same
    # decision).
    #
    # data: launches the real `python -m weld` entry point out of the runfiles
    # tree (the `weld_cli_launch_path_test` pattern), so the module entrypoint
    # has to be there as a regular package, not synthesised. Sandbox-hermetic:
    # the corpus git-inits its own repo and sets its own identity.
    py_test(
        name = "weld_route_boundary_placeholder_e2e_test",
        srcs = [
            "_route_boundary_corpus.py",
            "weld_route_boundary_placeholder_e2e_test.py",
        ],
        data = ["//weld:module_entrypoint"],
        deps = [
            ":graph_invariants_lib",
            "//weld:runtime",
            "//weld/strategies",
            "//weld/strategies:helpers",
            "@pypi//tree_sitter",
            "@pypi//tree_sitter_go",
            "@pypi//tree_sitter_rust",
            "@pypi//tree_sitter_typescript",
        ],
        env = {"PYTHONHASHSEED": "0"},
    )

    # bd bz5w9, system level: every Dockerfile in a repo collapsing onto one
    # `dockerfile:` node. Each layer is correct alone -- the walk finds both
    # files, the inventory hashes both, the strategy reads both -- so only a
    # whole `wd discover` shows the two merging on the way into the node
    # table. Shells the real CLI over a real git repo the way
    # weld_discover_segment_glob_e2e_test does, and reads the count back
    # through `wd stats --json` so the number a user sees is the one asserted.
    #
    # No tree-sitter deps: the fixture wires only the dockerfile strategy, so
    # no grammar-backed pass runs and the test stays hermetic and untagged in
    # the fast loop.
    #
    # data: launches the real `python -m weld` entry point out of the runfiles
    # tree (the `weld_cli_launch_path_test` pattern), so the module entrypoint
    # has to be there as a regular package, not synthesised. Sandbox-hermetic:
    # the fixture git-inits its own repo and sets its own identity, which the
    # shared `_cli_e2e_harness.py` in srcs is what supplies.
    py_test(
        name = "weld_discover_dockerfile_identity_e2e_test",
        srcs = [
            "_cli_e2e_harness.py",
            "weld_discover_dockerfile_identity_e2e_test.py",
        ],
        data = ["//weld:module_entrypoint"],
        deps = [
            "//weld:runtime",
            "//weld/strategies",
            "//weld/strategies:helpers",
        ],
        env = {"PYTHONHASHSEED": "0"},
    )

    # bd bz5w9, unit level: the id *spelling* the e2e probe deliberately does
    # not assert. `wd context`, `wd impact`, the compose strategy's
    # depends_on target and the documented examples all name the id, so the
    # root-Dockerfile back-compat promise and the one-claimant alias rule
    # each need a pin of their own.
    py_test(
        name = "weld_dockerfile_identity_test",
        srcs = ["weld_dockerfile_identity_test.py"],
        deps = [
            "//weld:runtime",
            "//weld/strategies",
            "//weld/strategies:helpers",
        ],
    )
