"""Subprocess lane: weld exercised as a real process (``sh_test``).

Every target here shells the CLI instead of importing it, which is the only
way to observe argv handling, exit codes, stdout/stderr separation, SIGPIPE,
and the runfiles layout an installed weld actually sees.

bd 0zf6 (f5ku step 5): these subprocess-weld sh_tests find weld via the
``//weld:module_entrypoint`` runfiles entry; the git-shelling ones set their
own git identity and run ``git init --quiet``, so they are sandbox-hermetic
(no opt-out).

Extracted verbatim from weld/tests/BUILD.bazel (bd hpv7) -- the whole lane in
one place, and the single largest block of headroom that file gets back.
Names, srcs, and data are unchanged, so every label stays
//weld/tests:<name>.
"""

load("@rules_shell//shell:sh_test.bzl", "sh_test")

def subprocess_tests():
    sh_test(
        name = "weld_fixture_init_test",
        srcs = ["weld_fixture_init_test.sh"],
        data = [
            "weld_test_lib.sh",
            ":fixture_files",
            "//weld:module_entrypoint",
        ],
    )

    sh_test(
        name = "weld_fixture_discover_test",
        srcs = ["weld_fixture_discover_test.sh"],
        data = [
            "weld_test_lib.sh",
            ":fixture_files",
            "//weld:module_entrypoint",
            "//weld/strategies",
        ],
    )

    sh_test(
        name = "weld_external_repo_init_test",
        srcs = ["weld_external_repo_init_test.sh"],
        data = [
            "weld_test_lib.sh",
            ":fixture_files",
            "//weld:module_entrypoint",
        ],
    )

    sh_test(
        name = "weld_external_repo_discover_test",
        srcs = ["weld_external_repo_discover_test.sh"],
        data = [
            "weld_test_lib.sh",
            ":fixture_files",
            "//weld:module_entrypoint",
            "//weld/strategies",
        ],
    )

    sh_test(
        name = "weld_test",
        srcs = ["weld_test.sh"],
        data = ["weld_test_lib.sh", "//weld:module_entrypoint"],
    )

    sh_test(
        name = "weld_cli_sigpipe_test",
        srcs = ["weld_cli_sigpipe_test.sh"],
        data = ["weld_test_lib.sh", "//weld:module_entrypoint"],
    )

    sh_test(
        name = "weld_file_index_test",
        srcs = ["weld_file_index_test.sh"],
        data = [
            "weld_test_lib.sh",
            "//weld:module_entrypoint",
        ],
    )

    # bd fw90: hermetic copy helper as data.
    sh_test(
        name = "weld_discover_test",
        srcs = ["weld_discover_test.sh"],
        data = [
            "_source_tree_copy.py",
            "weld_test_lib.sh",
            "//weld:module_entrypoint",
            "//weld/strategies",
        ],
    )

    # ADR 0008 incremental discovery: split from
    # weld_incremental_discovery_test.sh into 3 focused sh_tests sharing
    # _incremental_discovery_lib.sh (state-file/idempotency, mutations,
    # full-fallback/flags) so each stays within the default line-count cap
    # (former closed grandfather entry); re-sandboxed per f5ku step 5
    # (925a9ff).
    [sh_test(
        name = _name,
        srcs = [_name + ".sh"],
        data = [
            "_incremental_discovery_lib.sh",
            "weld_test_lib.sh",
            "//weld:module_entrypoint",
            "//weld/strategies",
        ],
    ) for _name in (
        "weld_incremental_discovery_state_test",
        "weld_incremental_discovery_mutations_test",
        "weld_incremental_discovery_fallback_test",
    )]

    sh_test(
        name = "weld_init_test",
        srcs = ["weld_init_test.sh"],
        data = [
            "weld_test_lib.sh",
            "//weld:module_entrypoint",
        ],
    )

    sh_test(
        name = "weld_stale_test",
        srcs = ["weld_stale_test.sh"],
        data = [
            "weld_test_lib.sh",
            "//weld:module_entrypoint",
        ],
    )

    sh_test(
        name = "weld_strategy_unit_test",
        srcs = ["weld_strategy_unit_test.sh"],
        data = [
            "weld_test_lib.sh",
            "//weld:module_entrypoint",
            "//weld/strategies",
        ],
    )

    # bd 0zf6: structural layout check (no real pip install); reads
    # VERSION/MODULE.bazel/pyproject from runfiles -> sandbox-hermetic.
    sh_test(
        name = "weld_pip_install_test",
        srcs = ["weld_pip_install_test.sh"],
        data = [
            "weld_test_lib.sh",
            "//:MODULE.bazel",
            "//:VERSION",
            "//weld:module_entrypoint",
            "//weld:pyproject.toml",
        ],
    )
