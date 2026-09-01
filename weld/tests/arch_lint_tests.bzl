"""`wd lint` -- the architectural lint suite and the module surface it rests on.

Six targets over `weld.arch_lint` and its split-out siblings (boundary rules,
custom rules, cycle detection and its non-structural-edge exclusion, orphan
detection), plus `weld_cli_module_split_surface_test`, which is part of the
same group rather than a neighbour of it: it pins the cross-module import
surface of `weld._graph_cli` / `weld.arch_lint` and the single-home invariant
for the emit writers -- the failure mode a module split introduces silently,
and the reason three of the lint suites above are separate files at all.

All seven are hermetic and untagged, depend on `//weld:runtime` alone, and run
in the fast loop.

Extracted verbatim from weld/tests/BUILD.bazel (bd hpv7's headroom mechanism,
taken here for bd 5038-hdutn): names, srcs, deps and tags are unchanged, so
every label stays //weld/tests:<name>.
"""

load("@rules_python//python:defs.bzl", "py_test")

def arch_lint_tests():
    py_test(name = "weld_arch_lint_test", srcs = ["weld_arch_lint_test.py"], deps = ["//weld:runtime"])
    py_test(name = "weld_cli_module_split_surface_test", srcs = ["weld_cli_module_split_surface_test.py"], deps = ["//weld:runtime"])  # pins the cross-module import surface of weld._graph_cli / weld.arch_lint and the single-home invariant for the emit writers -- the failure mode a module split introduces silently
    py_test(name = "weld_arch_lint_boundary_test", srcs = ["weld_arch_lint_boundary_test.py"], deps = ["//weld:runtime"])
    py_test(name = "weld_arch_lint_custom_rules_test", srcs = ["weld_arch_lint_custom_rules_test.py"], deps = ["//weld:runtime"])
    py_test(name = "weld_arch_lint_cycles_test", srcs = ["weld_arch_lint_cycles_test.py"], deps = ["//weld:runtime"])
    py_test(name = "weld_arch_lint_cycles_exclusion_test", srcs = ["weld_arch_lint_cycles_exclusion_test.py"], deps = ["//weld:runtime"])  # bd 5038-ojg27: NON_STRUCTURAL_EDGE_TYPES (relates_to/documents/validates/calls/decorates/references) no longer form no-circular-deps violations; split from weld_arch_lint_cycles_test.py to stay under the line-count cap
    py_test(name = "weld_arch_lint_orphan_test", srcs = ["weld_arch_lint_orphan_test.py"], deps = ["//weld:runtime"])
