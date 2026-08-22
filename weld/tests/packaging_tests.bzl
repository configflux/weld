"""Pip-wheel / source-hygiene test targets and their shared helper libraries.

bd u14l: ``_source_tree_copy.py`` and ``_pip_wheel_hardening.py`` are factored
into ``py_library`` here (not duplicated in each consumer's ``srcs``) so each
helper is PyCompiled exactly once -- otherwise a mixed sandbox state (the
helper tests sandboxed, the exclusive tests ``local = True``) makes two targets
write the same ``__pycache__/*.pyc`` with different action keys, which Bazel
reports as a conflicting-actions error. Imports are unchanged
(``from weld.tests._source_tree_copy import ...``).

The exclusive pair keeps ``local = True`` plus the ``exclusive`` tag.
``local = True``: the pip-wheel build needs the real toolchain. ``exclusive``:
ck8l replaced the old chmod-to-0o444 perimeter -- which mutated the live
``weld/__init__.py``'s mode and, via Bazel's hardlinked execroot, raced
concurrent runs to a stuck-0o444 leak and an EACCES permission error on
sandbox input copy (bd ck8l/5ko1) -- with a content-snapshot perimeter in
``_pip_wheel_hardening.py`` that touches no mode and shares no inode, so that
race is now structurally impossible. Both tests still build a pip wheel,
though, so ``exclusive`` is kept as cheap belt-and-suspenders defence in depth
against any future shared-state coupling between concurrent wheel builds; it
is not load-bearing for the (eliminated) inode-mode race.

Extracted verbatim from weld/tests/BUILD.bazel (bd hpv7), where the six
targets shared two folded lines. Names, srcs, deps, tags, and locality are
unchanged, so every label stays //weld/tests:<name>.
"""

load("@rules_python//python:defs.bzl", "py_library", "py_test")

_HELPER_LIBS = ["//weld:runtime", ":source_tree_copy_lib", ":pip_wheel_hardening_lib"]
_EXCLUSIVE_TAGS = ["no-sandbox", "exclusive", "integration"]

def packaging_tests():
    py_library(
        name = "source_tree_copy_lib",
        srcs = ["_source_tree_copy.py"],
        deps = ["//weld:runtime"],
    )

    py_library(
        name = "pip_wheel_hardening_lib",
        srcs = ["_pip_wheel_hardening.py"],
        deps = ["//weld:runtime"],
    )

    [py_test(
        name = _name,
        srcs = [_name + ".py"],
        deps = _HELPER_LIBS,
        local = True,
        tags = _EXCLUSIVE_TAGS,
    ) for _name in ("weld_mcp_install_smoke_test", "weld_source_pollution_guard_test")]

    py_test(
        name = "weld_source_tree_copy_test",
        srcs = ["_source_tree_copy_test.py"],
        main = "_source_tree_copy_test.py",
        deps = ["//weld:runtime", ":source_tree_copy_lib"],
    )

    py_test(
        name = "weld_pip_wheel_hardening_test",
        srcs = ["_pip_wheel_hardening_test.py"],
        main = "_pip_wheel_hardening_test.py",
        deps = ["//weld:runtime", ":pip_wheel_hardening_lib"],
    )
