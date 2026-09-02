"""Coverage staleness (ADR 0101): the accounting, and the CLI probe on it.

One subject, five targets. ADR 0101's third freshness signal asks whether the
graph's own inventory still covers what discovery would resolve *today* -- the
question neither ADR 0017 signal can reach, because a file that was never
ingested has no commit range to diff and raises no status entry.

The four unit targets sit on one shared fixture (`_coverage_stale_lib.py`) so
they cannot drift on what "in scope" means; the E2E target drives the same
contract through the real `python -m weld`, over the config `wd init` itself
generates.

Extracted from weld/tests/BUILD.bazel (bd 5038-2z5no): the E2E target needs
more than the one line that file gives a declaration, and the four unit
targets are the same subject, so grouping them here buys the headroom rather
than spending the file's last lines on it.
"""

load("@rules_python//python:defs.bzl", "py_test")

def coverage_staleness_tests():
    [py_test(name = _n, srcs = [_n + ".py", "_coverage_stale_lib.py"], deps = ["//weld:contract", "//weld:runtime", "//weld/strategies", "//weld/strategies:helpers"]) for _n in ("weld_coverage_staleness_test", "weld_coverage_scope_match_test", "weld_inventory_vouching_test", "weld_inventory_coverage_audit_test")]  # ADR 0101: an in-scope file the graph never ingested is invisible to both ADR 0017 signals (no commit range to diff once the recorded SHA is HEAD, no status entry when the file is committed and clean), so freshness answered {stale: false, commits_behind: 0} indefinitely. _staleness_test covers the probe, its fold into compute_stale_info, and the refresh it must trigger; _scope_match_test pins the path-list matcher against a real glob walk -- the never-over-report direction specifically, since over-reporting scope makes a file permanently uncoverable and refreshes on every read; _inventory_coverage_audit_test (bd qmbp) pins the third question neither probe asked -- an inventory can name the body on disk exactly and still claim node-bearing files it does not anchor, which reads clean through both, so mark_state_published audits the claim before stamping the token

    # bd 5038-2z5no: the same accounting as a whole run, over the config the
    # *product* generates. `in_scope_files` never called `expand_braces`, so a
    # brace glob matched nothing there while `walk_glob` expanded it and
    # matched everything it named -- and since `wd init` writes `**/*.{ts,tsx}`
    # and `**/*.{js,jsx,mjs,cjs}` for every Node repo, the never-ingested
    # signal was dead for the whole language. In-process this looks fine from
    # either side; only a whole run shows the two halves disagreeing.
    #
    # No grammar deps: the probe reads the *inventory*, which records every
    # file `resolve_source_files` resolved whether or not a strategy emitted a
    # node for it, so the tree-sitter warning `wd discover` prints without one
    # costs it nothing.
    #
    # data: launches the real `python -m weld` entry point out of the runfiles
    # tree (the `weld_cli_launch_path_test` pattern), so the module entrypoint
    # has to be there as a regular package, not synthesised. Sandbox-hermetic:
    # it git-inits its own repo. `_cli_e2e_harness.py` in srcs is the
    # subprocess plumbing this shares with the ADR 0112 glob probes.
    py_test(
        name = "weld_coverage_brace_glob_e2e_test",
        srcs = [
            "weld_coverage_brace_glob_e2e_test.py",
            "_cli_e2e_harness.py",
        ],
        data = ["//weld:module_entrypoint"],
        deps = [
            "//weld:runtime",
            "//weld/strategies",
            "//weld/strategies:helpers",
        ],
        env = {"PYTHONHASHSEED": "0"},
    )
