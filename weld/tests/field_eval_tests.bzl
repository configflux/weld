"""Field-evaluation regression corpora (bd uuxaz, bd d76r1, bd lcq0c).

Three external evaluations drove the same synthetic polyrepo workspace -- four
repos, then five -- through the shipped `wd` and filed twenty-two findings
between them. These targets are our copy of that workspace and of their
probes, so the next evaluation finds out from this gate instead of from a
report.

Six targets, five subjects:

* `weld_field_eval_corpus_test` -- the in-process half. Builds the root
  meta-graph the way `wd discover` does and asserts on what the resolver
  actually merged into it.
* `weld_field_eval_e2e_test` -- the nine v0.24.0 findings as probes through
  `python -m weld` in a subprocess, every one an expected failure until its
  fix lands.
* `weld_field_eval_v0250_e2e_test` -- round three's four, the same way and for
  the same reason (ADR 0141 D5: red before green). Its own module rather than
  more classes in the file above, so a round's probes carry a round's bundle
  provenance and neither module's bootstrap pays for the other's.
* `weld_field_eval_regression_e2e_test` -- the evaluator's own regression
  script (`verify-all-fixes.sh` as of round three), which must pass today.
* `weld_field_eval_probe_inventory_test` -- the guard on both probe files:
  one probe per finding, none skipped, each naming the finding and bd issue it
  reproduces. Structural and subprocess-free, so it stays a millisecond-scale
  check.
* `weld_field_eval_bundle_test` -- the fifth subject and the only tagged
  target here: the evaluator's three *scripts*, run as scripts against a `wd`
  shim, out of `weld/tests/fixtures/field_eval/`. The ports above assert the same
  claims in-process; this one asserts that the artifacts we hand back still
  work, which no in-process port can. See its own header for the lane
  justification.

The first five are **hermetic and untagged** and run in the fast loop
(docs/testing-hygiene.md "Which lane does my test belong in?"). The three E2E
targets shell the CLI and `git`, which the sandbox provides -- the fixture
supplies its own git identity, exactly as the `subprocess_tests` lane does --
and none needs an ambient tree-sitter grammar: the two checks that would
(0.23.1's 03a/08) self-skip on the import.

Runtime budget: the E2E targets bootstrap one workspace per module (five
`wd init` + six `wd discover`) and then run probes on top. They are split by
subject rather than merged partly for that -- four modest targets where one
merged target would sit well over the 10 s ceiling. Measured serially and
uncached (`--nocache_test_results --local_test_jobs=1`) after round three grew
the workspace to six repos and 48 files: probe_inventory 0.3 s, corpus 1.3 s,
regression_e2e 3.2 s, v0250_e2e 3.4 s, bundle 5.4 s, e2e 6.5 s -- all inside
budget. Measure this way if you revisit it: the same set run 20-wide under
`--runs_per_test=3` reports e2e at 16.6 s, which is machine contention rather
than the target, and reading that figure as the target's cost is how a
green-and-fine suite acquires a bogus "over budget" note. The harness sets
`PYTHONPYCACHEPREFIX` for a related reason -- a read-only runfiles tree makes
every one of the ~40 subprocesses recompile weld, which cost 3x the total.
"""

load("@rules_python//python:defs.bzl", "py_test")
load("@rules_shell//shell:sh_test.bzl", "sh_test")

#: The workspace materialiser and its file bodies ride in `srcs` (the
#: `_impact_test_helpers` pattern) rather than becoming a py_library: they are
#: test data, not a surface anything imports outside these targets.
#:
#: `_graph_invariants.py` used to ride here on that same premise and no longer
#: does (bd 5038-hdutn / ADR 0139). The test-quality program falsified it for
#: that one file: the golden families and the purge family assert the same
#: invariants, so it is `//weld/tests:graph_invariants_lib` in `_DEPS` below,
#: depended on once instead of pasted into a dozen more `srcs` lists.
#: `_probe_markers.py` joins them (bd 5038-lrnx1.1): the red-probe marker
#: contract moved out of the E2E harness when the Node/Next.js readiness
#: corpus started using it too, so one decorator and one reader serve both
#: inventory guards. It rides in `srcs` for the same reason its neighbours do.
_FIXTURE = [
    "_field_eval_corpus_fixture.py",
    "_field_eval_corpus_sources.py",
    "_field_eval_corpus_sources_csharp.py",
    "_probe_markers.py",
]

_E2E_FIXTURE = _FIXTURE + ["_field_eval_e2e_harness.py"]

_DEPS = [
    ":graph_invariants_lib",
    "//weld:contract",
    "//weld:runtime",
    "//weld:workspace",
    "//weld:yaml",
    "//weld/cross_repo",
]

_E2E_DEPS = _DEPS + [
    "//weld/strategies",
    "//weld/strategies:helpers",
]

def field_eval_tests():
    py_test(
        name = "weld_field_eval_corpus_test",
        srcs = _E2E_FIXTURE + ["weld_field_eval_corpus_test.py"],
        deps = _DEPS,
        env = {"PYTHONHASHSEED": "0"},
    )

    # data: these launch the real `python -m weld` entry point out of the
    # runfiles tree (the `weld_cli_launch_path_test` pattern), so the module
    # entrypoint has to be there as a regular package, not synthesised.
    [py_test(
        name = _name,
        srcs = _E2E_FIXTURE + [_name + ".py"],
        data = ["//weld:module_entrypoint"],
        deps = _E2E_DEPS,
        env = {"PYTHONHASHSEED": "0"},
    ) for _name in (
        "weld_field_eval_e2e_test",
        "weld_field_eval_regression_e2e_test",
        "weld_field_eval_v0250_e2e_test",
    )]

    py_test(
        name = "weld_field_eval_probe_inventory_test",
        srcs = _E2E_FIXTURE + [
            "weld_field_eval_e2e_test.py",
            "weld_field_eval_v0250_e2e_test.py",
            "weld_field_eval_probe_inventory_test.py",
        ],
        deps = _E2E_DEPS,
        env = {"PYTHONHASHSEED": "0"},
    )

    # `no-sandbox` + `local`: this target runs the evaluator's scripts
    # *unmodified*, so it inherits their environment -- an ambient `git` (six
    # repos, three linked worktrees) and, for verify-all-fixes.sh's checks 03a
    # and 08 only, the ambient `tree_sitter_c_sharp` grammar this repo
    # deliberately does not pin (ADR 0069). That is docs/testing-hygiene.md
    # "Which lane does my test belong in?" item 2, and `integration` alongside
    # it per item 3. No git skips the target; no grammar tolerates exactly
    # those two check ids out of 22, so the lane never becomes a no-op.
    #
    # The corpus fixture rides in `data` rather than `deps`: the drift guard
    # imports it out of the source tree the shim already puts on PYTHONPATH.
    sh_test(
        name = "weld_field_eval_bundle_test",
        srcs = ["field_eval/weld_field_eval_bundle_test.sh"],
        data = [
            "_field_eval_corpus_fixture.py",
            "_field_eval_corpus_sources.py",
            "_field_eval_corpus_sources_csharp.py",
            "fixtures/field_eval/bootstrap-fixture.sh",
            "fixtures/field_eval/make-fixture.sh",
            "fixtures/field_eval/verify-all-fixes.sh",
            "weld_test_lib.sh",
            "//weld:module_entrypoint",
            "//weld/strategies",
        ],
        local = True,
        tags = ["integration", "no-sandbox"],
    )
