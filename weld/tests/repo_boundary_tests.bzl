"""Repo-boundary test targets: which files weld considers in scope.

One subject at four layers -- what the git-visible boundary admits, how
long a boundary snapshot may live, which entry points are obliged to
consult it at all, and which files a `glob:` pattern actually names.

The last layer lives here rather than in a file of its own because
`weld/glob_match.py` was split out of `weld/repo_boundary.py` and the two
are intentionally coupled (that module's own docstring): the glob walker
applies the boundary filter on the way out, so a defect in either answers
the same user-visible question wrongly. weld/tests/BUILD.bazel is at the
400-line cap, so a new .bzl would also cost it a load line it has no room
for.

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

    # Which files a `glob:` pattern names when the wildcard is in a
    # *directory* segment (bd uhxjc). The flat branch of walk_glob guarded on
    # `(root / pattern).parent.is_dir()`, which for `apps/*/package.json` is
    # the literal path `<root>/apps/*` -- never a directory, so the pattern
    # matched nothing at all. Needs //weld/strategies for the shared resolver
    # (ADR 0112), whose answer must equal the in-scope set discovery records.
    py_test(
        name = "weld_glob_segment_wildcard_test",
        srcs = ["weld_glob_segment_wildcard_test.py"],
        deps = [
            "//weld:runtime",
            "//weld/strategies",
        ],
    )

    # The same contract as a whole run: a real git repo, a real
    # `python -m weld discover` in a subprocess, then the graph, the
    # inventory and `wd stale` it produced. The in-process layers each look
    # fine on their own -- what only a whole run shows is that the empty
    # walk leaves every named file permanently `coverage_stale`.
    #
    # data: launches the real `python -m weld` entry point out of the
    # runfiles tree (the `weld_cli_launch_path_test` pattern), so the module
    # entrypoint has to be there as a regular package, not synthesised.
    # Sandbox-hermetic: it git-inits its own repo and sets its own identity,
    # the way the subprocess lane's git-shelling sh_tests do.
    py_test(
        name = "weld_discover_segment_glob_e2e_test",
        srcs = [
            "weld_discover_segment_glob_e2e_test.py",
            "_cli_e2e_harness.py",
        ],
        data = ["//weld:module_entrypoint"],
        deps = [
            "//weld:runtime",
            "//weld/strategies",
            "//weld/strategies:helpers",
        ],
    )

    # The same whole-run contract one layer up, on the walker's *callers*
    # (bd b9xgd): four strategies never moved onto the ADR 0112 shared
    # resolver, so the walker fix above cannot reach them. Two shapes, and
    # the probe asserts both -- fastapi/pydantic emit nothing for a wildcard
    # directory segment, compose/events_config fall back to the root
    # directory and emit the wrong set. Same declaration as its sibling: the
    # real `python -m weld` out of the runfiles tree, sandbox-hermetic
    # because it git-inits its own repo. `_cli_e2e_harness.py` in srcs is the
    # subprocess plumbing both probes share; `_segment_glob_fixture.py` is this
    # one's tree and config, split out so the probe file is its assertions.
    py_test(
        name = "weld_strategy_segment_glob_e2e_test",
        srcs = [
            "weld_strategy_segment_glob_e2e_test.py",
            "_cli_e2e_harness.py",
            "_segment_glob_fixture.py",
        ],
        data = ["//weld:module_entrypoint"],
        deps = [
            "//weld:runtime",
            "//weld/strategies",
            "//weld/strategies:helpers",
        ],
    )
