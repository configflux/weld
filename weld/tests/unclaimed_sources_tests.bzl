"""Unclaimed-source drift: the check, its remedy, and the dialect probe.

One subject, six targets. `.weld/discover.yaml` is generated once and never
revisited, so a checkout keeps discovering with the config it was initialised
with; ADR 0135 makes that gap visible (`wd doctor` / `wd prime`), and
`wd init --refresh` closes it without discarding hand edits. ADR 0144 moves
what "claimed" means -- from a `strategy:` name appearing anywhere in the
config to an enabled entry actually matching a file -- which is the seam the
first E2E probe below drives; its 2026-09-02 amendment adds the comparison
that runs beside the language one, keyed on the entry, which is the second's.

Extracted from weld/tests/BUILD.bazel (bd 5038-wqea5): the E2E target needs
more than the one line that file gives a declaration, and its three siblings
are the same subject, so grouping them here buys the headroom rather than
spending the file's last line on it.
"""

load("@rules_python//python:defs.bzl", "py_test")

def unclaimed_sources_tests():
    # ADR 0135 / field-eval Finding 05: wd doctor + wd prime warn when a
    # language present on disk has no wired strategy (100% of source invisible
    # while doctor reports healthy), and wd init stamps its version into the
    # generated discover.yaml. ADR 0144 rewrote the comparison these cases pin
    # -- the claim is now a matched file -- and the claim cases it added pushed
    # the pair past the 400-line cap, so the rule and the surfaces that report
    # it are two targets: _sources_ feeds source entries and repo-relative
    # paths and never touches a filesystem, _surfaces_ owns the disk walk, the
    # doctor row, the prime line, the suppression id and the version stamp.
    py_test(
        name = "weld_unclaimed_sources_test",
        srcs = ["weld_unclaimed_sources_test.py"],
        deps = ["//weld:runtime", "//weld:yaml"],
    )

    py_test(
        name = "weld_unclaimed_surfaces_test",
        srcs = ["weld_unclaimed_surfaces_test.py"],
        deps = ["//weld:runtime", "//weld:yaml"],
    )

    # ADR 0135 / field-eval Finding 05 follow-on: wd init --refresh merges
    # newly-detected strategies into an existing discover.yaml (append-only,
    # preserving hand edits + custom strategies) instead of wd init --force
    # discarding them; bumps the version stamp.
    py_test(
        name = "weld_init_refresh_test",
        srcs = ["weld_init_refresh_test.py"],
        deps = ["//weld:runtime", "//weld:yaml"],
    )

    # field-eval 0.24.0 N7: wd init --refresh wired only the tree-sitter
    # backbone where --force wires the whole language stack (3 of 10 strategies
    # on a .NET repo) *and* cleared the unclaimed-source warning, so a clean wd
    # doctor no longer said the rest was invisible. Both commands now emit from
    # one table (weld/_init_language_entries.py); these cases run --refresh and
    # --force over the same tree and compare, rather than pinning a strategy
    # list that would drift the way the two tables did. Split from
    # weld_init_refresh_test (different subject, and the 400-line cap).
    py_test(
        name = "weld_init_refresh_parity_test",
        srcs = ["weld_init_refresh_parity_test.py"],
        deps = ["//weld:runtime", "//weld:yaml"],
    )

    # ADR 0144 / bd 5038-wqea5, red on purpose and flipped by the fix: a config
    # wiring `**/*.ts` left `.tsx` and `.js` unread while doctor, prime and
    # `--refresh` all reported the config current. Every surface the bug reached
    # is a command, so this drives the real CLI in a subprocess out of the
    # runfiles tree (the weld_cli_launch_path_test / node-eval pattern) rather
    # than calling the detector in-process, where it would have agreed with
    # itself. No grammar deps: it materialises four repos and parses no source.
    py_test(
        name = "weld_unclaimed_dialect_e2e_test",
        srcs = ["weld_unclaimed_dialect_e2e_test.py"],
        data = ["//weld:module_entrypoint"],
        deps = ["//weld:runtime"],
        env = {"PYTHONHASHSEED": "0"},
    )

    # bd 5038-j5o5d, unit level: the record `wd init` writes into a
    # discover.yaml naming what it wired, which is what lets `--refresh` tell
    # an entry it never offered from one a maintainer deleted. Held apart from
    # the E2E probe below because these are the cases a system test cannot
    # reach cheaply -- rewriting a record inside someone else's file, each
    # entry shape's key, and the one-time migration for a config written
    # before the record existed.
    py_test(
        name = "weld_init_wired_ledger_test",
        srcs = ["weld_init_wired_ledger_test.py"],
        deps = ["//weld:runtime", "//weld:yaml"],
    )

    # bd 5038-j5o5d, red on purpose and flipped by the fix: `--refresh`'s unit
    # of work was an unclaimed *language*, so a root config or a framework
    # entry -- neither of which is a language -- could never be delivered to an
    # existing project. The probe drives the real CLI because the bug spans the
    # merge, the config it writes and the graph the next `wd discover` builds
    # from it; `_cli_e2e_harness.py` in srcs is the subprocess plumbing, and
    # `data` puts the module entry point in the runfiles tree. No grammar deps:
    # the entries under test are `config_file` and `express`.
    py_test(
        name = "weld_init_refresh_entries_e2e_test",
        srcs = [
            "_cli_e2e_harness.py",
            "weld_init_refresh_entries_e2e_test.py",
        ],
        data = ["//weld:module_entrypoint"],
        deps = ["//weld:runtime"],
        env = {"PYTHONHASHSEED": "0"},
    )
