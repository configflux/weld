"""Repo-boundary test targets: which files weld considers in scope.

One subject at three layers -- what the git-visible boundary admits, how
long a boundary snapshot may live, and which entry points are obliged to
consult it at all.

Extracted from weld/tests/BUILD.bazel, which indexes subjects rather than
listing targets. Target names, srcs and deps are unchanged, so every label
stays //weld/tests:<name>.
"""

load("@rules_python//python:defs.bzl", "py_test")

def repo_boundary_tests():
    # What the boundary admits: git-visible files win over .gitignore,
    # symlinks out of an excluded tree stay excluded (ADR 0020).
    py_test(
        name = "weld_repo_boundary_test",
        srcs = ["weld_repo_boundary_test.py"],
        deps = [
            "//weld:runtime",
            "//weld/strategies:helpers",
        ],
    )

    # How long a snapshot lives (bd jbpb): scoped to one operation, never
    # to the process. A listing that outlives its operation is how a
    # long-lived host goes blind to files created after it started.
    py_test(
        name = "weld_repo_boundary_scope_test",
        srcs = ["weld_repo_boundary_scope_test.py"],
        deps = [
            "//weld:runtime",
            "//weld/strategies:helpers",
        ],
    )

    # Which entry points must consult the boundary at all.
    # bd 8m3f: converted to unittest.TestCase + main guard -> runs 14 cases
    # under Bazel's stdlib runner; sandbox-hermetic.
    py_test(
        name = "weld_boundary_entrypoint_test",
        srcs = ["weld_boundary_entrypoint_test.py"],
        deps = [
            "//weld/strategies",
            "//weld/strategies:helpers",
        ],
    )

    # How long a *glob* result may live (bd cjij). Same lifetime rule one
    # layer up: walk_glob results are memoized per operation, never per
    # process, so entries sharing a glob pay one traversal and the next
    # operation still sees the tree as it is then. Needs //weld/strategies
    # because the last case drives a real discovery run.
    py_test(
        name = "weld_glob_scope_test",
        srcs = ["weld_glob_scope_test.py"],
        deps = [
            "//weld:runtime",
            "//weld/strategies",
        ],
    )
