"""Launch-path and import-shadow guards for weld's entry points.

These cover one invariant at two layers, once per ``python -m`` entry point:
``_path`` pins in-process which ``sys.path`` entry the entry point drops and
which it must leave alone; ``_shadow`` launches the real thing from a
directory carrying stealth shim modules and asserts none of them ran. Only a
subprocess can witness the ordering -- the guard has to beat the entry
module's own imports -- and every shadow test carries a negative control
proving the fixture still bites an unguarded interpreter, so none of them can
pass vacuously.

``mcp_*`` shadow the MCP server (dataclasses/json/mcp plus a forged mcp
dist-info); ``cli_*`` shadow ``python -m weld`` and additionally measure the
floor ``-m`` imposes with an empty package launched the same way, because the
CLI's residual is that floor rather than zero.

``weld_mcp_serve_launch_shadow_test`` is the third member and the only one
that asserts zero: ``wd mcp serve`` is entered as a console script, whose
``sys.path[0]`` is the script's own directory, so it never has a
launch-directory entry to drop. Its shadow set therefore includes the floor
members too, and it carries a second control -- an empty ``-m`` target from
the same directory, required to be non-empty -- so the zero is a measured
contrast rather than an inert fixture.

Extracted from weld/tests/BUILD.bazel (bd hpv7). These targets and this prose
previously shared one 2,100-character folded line, held there only by that
file's 400-line cap -- the shape that has silently swallowed a target twice.
Names, srcs, data, and deps are unchanged, so every label stays
//weld/tests:<name>.
"""

load("@rules_python//python:defs.bzl", "py_test")

_RUNTIME = ["//weld:runtime"]

_IN_PROCESS_AND_SHADOW = (
    "weld_mcp_launch_path_test",
    "weld_mcp_launch_shadow_test",
    "weld_mcp_serve_launch_shadow_test",
    "weld_cli_launch_shadow_test",
)

def launch_path_tests():
    [py_test(
        name = _name,
        srcs = [_name + ".py"],
        deps = _RUNTIME,
    ) for _name in _IN_PROCESS_AND_SHADOW]

    # data: this one launches the real ``python -m weld`` entry point.
    py_test(
        name = "weld_cli_launch_path_test",
        srcs = ["weld_cli_launch_path_test.py"],
        data = ["//weld:module_entrypoint"],
        deps = _RUNTIME,
    )
