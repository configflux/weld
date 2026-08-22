"""Starlark-aware BUILD parsing (ADR 0105, bd s3pq).

The load-bearing property is asymmetric, and the tests are written to match:
every target the parser emits must be a real one, while a construct it cannot
evaluate is allowed to yield nothing. A missing target costs a query; an
invented target ID is indistinguishable from a real one at the point of use and
poisons every join through it (the ``_target_ids`` lesson). So the "cannot
evaluate" cases below assert *silence*, not a best guess.
"""

from __future__ import annotations

import unittest

from weld.strategies._bazel_starlark import parse_targets

RULES = {"py_library", "py_test", "sh_test", "genrule", "filegroup"}


def names(text: str) -> list[str]:
    targets = parse_targets(text, RULES)
    assert targets is not None
    return sorted(t["name"] for t in targets)


def only(text: str) -> dict:
    targets = parse_targets(text, RULES)
    assert targets is not None and len(targets) == 1, targets
    return targets[0]


class LiteralDeclarationTest(unittest.TestCase):
    """The shape ADR 0044's regex parser already handled must keep working."""

    def test_single_line_rule(self) -> None:
        text = 'py_test(name = "a_test", srcs = ["a_test.py"], deps = ["//weld:runtime"])'
        target = only(text)
        self.assertEqual(target["rule"], "py_test")
        self.assertEqual(target["name"], "a_test")
        self.assertEqual(target["srcs"], ["a_test.py"])
        self.assertEqual(target["deps"], ["//weld:runtime"])

    def test_multi_line_rule(self) -> None:
        text = (
            "py_library(\n"
            '    name = "lib",\n'
            '    srcs = ["a.py", "b.py"],\n'
            '    deps = ["//weld:runtime"],\n'
            ")\n"
        )
        target = only(text)
        self.assertEqual(target["name"], "lib")
        self.assertEqual(target["srcs"], ["a.py", "b.py"])

    def test_unknown_rule_is_ignored(self) -> None:
        self.assertEqual(names('cc_binary(name = "x", srcs = ["x.cc"])'), [])

    def test_empty_file_declares_nothing(self) -> None:
        self.assertEqual(parse_targets("", RULES), [])


class ComprehensionTest(unittest.TestCase):
    """The shape that made 115 declarations produce 33 nodes (bd s3pq)."""

    def test_comprehension_over_tuple(self) -> None:
        text = (
            'load("@rules_python//python:defs.bzl", "py_test")\n'
            '[py_test(name = _n, srcs = [_n + ".py"], deps = ["//weld:runtime"])\n'
            ' for _n in ("a_test", "b_test", "c_test")]\n'
        )
        self.assertEqual(names(text), ["a_test", "b_test", "c_test"])

    def test_comprehension_over_list(self) -> None:
        text = '[py_test(name = _n, srcs = [_n + ".py"]) for _n in ["a_test", "b_test"]]'
        self.assertEqual(names(text), ["a_test", "b_test"])

    def test_loop_variable_expands_in_srcs(self) -> None:
        text = '[py_test(name = _n, srcs = [_n + ".py"]) for _n in ("only_test",)]'
        self.assertEqual(only(text)["srcs"], ["only_test.py"])

    def test_conditional_srcs_picks_the_matching_branch(self) -> None:
        """The ``srcs = (helpers if _n in (...) else []) + [_n + ".py"]`` shape."""
        text = (
            "[py_test(\n"
            "    name = _n,\n"
            '    srcs = (["helpers.py"] if _n in ("a_test",) else []) + [_n + ".py"],\n'
            '    deps = ["//weld:runtime"],\n'
            ') for _n in ("a_test", "b_test")]\n'
        )
        targets = {t["name"]: t for t in parse_targets(text, RULES) or []}
        self.assertEqual(targets["a_test"]["srcs"], ["helpers.py", "a_test.py"])
        self.assertEqual(targets["b_test"]["srcs"], ["b_test.py"])

    def test_conditional_deps_uses_equality(self) -> None:
        text = (
            "[py_test(\n"
            "    name = _n,\n"
            '    deps = ["//weld:runtime"]\n'
            '        + (["@pypi//tree_sitter"] if _n == "ts_test" else []),\n'
            ') for _n in ("ts_test", "plain_test")]\n'
        )
        targets = {t["name"]: t for t in parse_targets(text, RULES) or []}
        self.assertEqual(
            targets["ts_test"]["deps"], ["//weld:runtime", "@pypi//tree_sitter"]
        )
        self.assertEqual(targets["plain_test"]["deps"], ["//weld:runtime"])

    def test_not_in_and_not_eq_are_supported(self) -> None:
        text = (
            '[py_test(name = _n, srcs = ["x.py"] if _n not in ("a",) else ["y.py"])'
            ' for _n in ("a", "b")]'
        )
        targets = {t["name"]: t for t in parse_targets(text, RULES) or []}
        self.assertEqual(targets["a"]["srcs"], ["y.py"])
        self.assertEqual(targets["b"]["srcs"], ["x.py"])

    def test_literal_and_comprehension_targets_coexist(self) -> None:
        text = (
            'py_test(name = "literal_test", srcs = ["literal_test.py"])\n'
            '[py_test(name = _n, srcs = [_n + ".py"]) for _n in ("gen_test",)]\n'
        )
        self.assertEqual(names(text), ["gen_test", "literal_test"])

    def test_comprehension_call_is_not_also_emitted_unbound(self) -> None:
        """The inner call must not be yielded a second time with ``_n`` unbound."""
        text = '[py_test(name = _n, srcs = [_n + ".py"]) for _n in ("a_test",)]'
        self.assertEqual(len(parse_targets(text, RULES) or []), 1)


class UnevaluatableIsSilentTest(unittest.TestCase):
    """What the parser cannot resolve exactly, it must drop -- never guess."""

    def test_filtered_comprehension_yields_nothing(self) -> None:
        text = '[py_test(name = _n) for _n in ("a", "b") if _n != "a"]'
        self.assertEqual(names(text), [])

    def test_computed_iterable_yields_nothing(self) -> None:
        text = "[py_test(name = _n) for _n in glob([\"*_test.py\"])]"
        self.assertEqual(names(text), [])

    def test_nested_comprehension_yields_nothing(self) -> None:
        text = (
            '[[py_test(name = _a + _b) for _a in ("x",)] for _b in ("y",)]'
        )
        self.assertEqual(names(text), [])

    def test_unresolved_name_drops_the_target(self) -> None:
        """No name means no node ID to mint, so the target is dropped whole."""
        self.assertEqual(names("py_test(name = SOME_CONSTANT)"), [])

    def test_missing_name_drops_the_target(self) -> None:
        self.assertEqual(names('py_test(srcs = ["a.py"])'), [])

    def test_glob_srcs_keeps_the_target_and_drops_the_entries(self) -> None:
        """srcs are edges, not identity: an opaque one costs only its entries."""
        target = only('py_test(name = "a_test", srcs = glob(["*.py"]))')
        self.assertEqual(target["name"], "a_test")
        self.assertEqual(target["srcs"], [])

    def test_select_deps_keeps_the_target_and_drops_the_entries(self) -> None:
        target = only('py_library(name = "lib", deps = select({"//c": ["//a"]}))')
        self.assertEqual(target["name"], "lib")
        self.assertEqual(target["deps"], [])

    def test_unsupported_comparison_drops_both_branches(self) -> None:
        target = only('py_test(name = "a", srcs = ["x.py"] if 1 < 2 else ["y.py"])')
        self.assertEqual(target["srcs"], [])

    def test_nested_list_entry_is_not_spliced_in(self) -> None:
        target = only('py_test(name = "a", srcs = [["nested.py"], "real.py"])')
        self.assertEqual(target["srcs"], ["real.py"])


class UnparseableFileTest(unittest.TestCase):
    """A file weld cannot parse is a failure to report, not a decision (bd hch4)."""

    def test_syntax_error_returns_none_not_empty(self) -> None:
        self.assertIsNone(parse_targets("py_test(name = ", RULES))

    def test_none_is_distinct_from_a_file_declaring_nothing(self) -> None:
        self.assertEqual(parse_targets("# just a comment\n", RULES), [])

    def test_pathological_nesting_is_reported_not_raised(self) -> None:
        """A file that exhausts the stack must not take the discovery run down.

        This strategy runs over every BUILD file in the repo mid-orchestration,
        so an unhandled error here surfaces as a crash somewhere far from its
        cause (the pt38 shape). Either outcome is acceptable -- parsed, or
        reported unparseable -- but never an exception.
        """
        text = 'py_test(name = "a", srcs = ' + "[" * 200 + "]" * 200 + ")"
        result = parse_targets(text, RULES)
        self.assertTrue(result is None or isinstance(result, list))

    def test_deep_concatenation_is_reported_not_raised(self) -> None:
        text = 'py_test(name = "a", srcs = ' + " + ".join(['["x.py"]'] * 400) + ")"
        result = parse_targets(text, RULES)
        self.assertTrue(result is None or isinstance(result, list))


class DeterminismTest(unittest.TestCase):
    """Same BUILD file, same targets, same order (ADR 0043)."""

    def test_repeated_parses_agree(self) -> None:
        text = (
            'py_test(name = "z_test", srcs = ["z_test.py"])\n'
            '[py_test(name = _n, srcs = [_n + ".py"]) for _n in ("b_test", "a_test")]\n'
        )
        first = parse_targets(text, RULES)
        second = parse_targets(text, RULES)
        self.assertEqual(first, second)

    def test_comprehension_order_follows_the_iterable(self) -> None:
        text = '[py_test(name = _n) for _n in ("b_test", "a_test")]'
        emitted = [t["name"] for t in parse_targets(text, RULES) or []]
        self.assertEqual(emitted, ["b_test", "a_test"])


class RealRepoBuildFileTest(unittest.TestCase):
    """The regression this exists for, against this repo's own BUILD file."""

    def test_comprehension_generated_target_is_modelled(self) -> None:
        from pathlib import Path

        build = Path(__file__).resolve().parent / "BUILD.bazel"
        targets = parse_targets(build.read_text(encoding="utf-8"), RULES)
        self.assertIsNotNone(targets)
        emitted = {t["name"] for t in targets or []}
        # Declared inside a list comprehension -- invisible before ADR 0105.
        self.assertIn("discover_node_merge_test", emitted)
        # The literal declarations must not have regressed.
        self.assertIn("weld_init_workspace_test", emitted)
        # Far more than the 33 nodes the regex parser produced for this file.
        self.assertGreater(len(emitted), 200)


if __name__ == "__main__":
    unittest.main()
