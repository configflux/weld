"""MCP server test targets.

The tool surface weld exposes over MCP: the server itself, its federated
tools, the ambient smoke test, the stdio guard, and the ``wd mcp``
config/CLI wiring. ``mcp_expected_tools.py`` is the shared tool-name pin, so
it rides in ``srcs`` wherever a target asserts against it; ``mcp_stdio_client.py``
is the shared subprocess/JSON-RPC driver, and does the same.

The launch-path and shadow guards for the same entry points live next door in
launch_path_tests.bzl -- one subject each, so a failure names its own claim.

Extracted verbatim from weld/tests/BUILD.bazel (bd hpv7). Names, srcs, and
deps are unchanged, so every label stays //weld/tests:<name>.
"""

load("@rules_python//python:defs.bzl", "py_test")

_RUNTIME = ["//weld:runtime"]

def mcp_tests():
    py_test(
        name = "weld_mcp_server_test",
        srcs = ["mcp_expected_tools.py", "weld_mcp_server_test.py"],
        deps = _RUNTIME,
    )

    # The smoke test drives the server as a child process; mcp_stdio_client.py
    # owns that spawn, its bounded teardown, and the JSON-RPC framing, so it
    # rides in srcs here and carries its own fd-lifecycle pin next door.
    py_test(
        name = "weld_mcp_smoke_test",
        srcs = [
            "mcp_expected_tools.py",
            "mcp_stdio_client.py",
            "weld_mcp_smoke_test.py",
        ],
        deps = _RUNTIME,
    )

    py_test(
        name = "mcp_stdio_client_test",
        srcs = ["mcp_stdio_client.py", "mcp_stdio_client_test.py"],
        deps = _RUNTIME,
    )

    [py_test(
        name = _name,
        srcs = [_name + ".py"],
        deps = _RUNTIME,
    ) for _name in (
        "weld_mcp_federation_test",
        "weld_mcp_federation_tools_test",
        "weld_mcp_config_test",
        "weld_mcp_cli_test",
        "weld_mcp_children_status_budget_test",
    )]

    # Hermetic: simulates absent vs pre-2.0 vs supported mcp SDK via
    # sys.modules, so every guard branch and the serverInfo version are
    # pinned regardless of what the runfiles carry (the smoke test can only
    # exercise the ambient one).
    py_test(
        name = "weld_mcp_stdio_guard_test",
        srcs = ["weld_mcp_stdio_guard_test.py"],
        deps = _RUNTIME,
    )
