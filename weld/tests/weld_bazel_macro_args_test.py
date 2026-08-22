"""Parameterized macro call binding (ADR 0109 amendment, ADR 0123, bd iysm).

ADR 0109 measured zero parameterized macro definitions and zero
argument-passing call sites in this repo and left both unevaluated:
"the day one does, it yields no targets rather than wrong ones."
``weld/tests/bench/bench_py_test.bzl`` is that day --
``def bench_py_test(name, tags = [], local = True, **kwargs)``, called 22
times with keyword arguments, every call yielding nothing before this
change.

The suite inherits ADR 0105's asymmetry and is written to match it: every
target the strategy emits must be one bazel really has (a positional
argument, a keyword argument, a default, a ``**kwargs`` splat), while a call
or definition shape :func:`weld.strategies._bazel_macro_args.bind_macro_call`
does not resolve unambiguously is allowed to yield *nothing* -- asserted here
as silence, never a best guess. The pure binder is tested in isolation
first (``BindMacroCallTest``), the two AST finders that partition macro defs
and find their call sites next, and the strategy end to end last -- the same
layering :mod:`weld.tests.weld_bazel_loads_test` uses for the zero-parameter
case this amends.
"""

from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path

from weld.strategies._bazel_macro_args import (
    bind_macro_call,
    param_macro_calls,
    param_macro_defs,
)
from weld.strategies.bazel import extract


def _def_and_call(def_src: str, call_src: str) -> tuple[ast.FunctionDef, ast.Call]:
    """Parse a ``def`` and a call expression in isolation, for a pure unit test."""
    func_def = ast.parse(def_src).body[0]
    assert isinstance(func_def, ast.FunctionDef)
    call = ast.parse(call_src, mode="eval").body
    assert isinstance(call, ast.Call)
    return func_def, call


class BindMacroCallTest(unittest.TestCase):
    """The binder in isolation: which call/def shapes bind, and to what."""

    def test_positional_argument_binds_by_index(self) -> None:
        func_def, call = _def_and_call(
            "def t(name, srcs):\n    pass\n", 't("a_test", ["a.py"])'
        )
        bound = bind_macro_call(func_def, call, {}, {})
        self.assertEqual(bound["name"], "a_test")
        self.assertEqual(bound["srcs"], ["a.py"])

    def test_keyword_argument_binds_by_name_in_any_order(self) -> None:
        func_def, call = _def_and_call(
            "def t(name, srcs):\n    pass\n",
            't(srcs = ["a.py"], name = "a_test")',
        )
        bound = bind_macro_call(func_def, call, {}, {})
        self.assertEqual(bound["name"], "a_test")
        self.assertEqual(bound["srcs"], ["a.py"])

    def test_missing_parameter_uses_its_default(self) -> None:
        func_def, call = _def_and_call(
            'def t(name, srcs = ["default.py"]):\n    pass\n', 't(name = "a_test")'
        )
        bound = bind_macro_call(func_def, call, {}, {})
        self.assertEqual(bound["srcs"], ["default.py"])

    def test_default_evaluates_in_the_defining_scope_not_the_callers(self) -> None:
        func_def, call = _def_and_call(
            "def t(name, srcs = _DEFAULT):\n    pass\n", 't(name = "a_test")'
        )
        bound = bind_macro_call(
            func_def, call, {"_DEFAULT": ["wrong.py"]}, {"_DEFAULT": ["right.py"]}
        )
        self.assertEqual(bound["srcs"], ["right.py"])

    def test_positional_value_evaluates_in_the_callers_scope(self) -> None:
        func_def, call = _def_and_call("def t(name, srcs):\n    pass\n", "t(_N, _S)")
        bound = bind_macro_call(func_def, call, {"_N": "a_test", "_S": ["a.py"]}, {})
        self.assertEqual(bound["name"], "a_test")
        self.assertEqual(bound["srcs"], ["a.py"])

    def test_kwargs_splat_collects_every_unnamed_keyword(self) -> None:
        """The bench_py_test shape: srcs/deps/data all arrive via **kwargs."""
        func_def, call = _def_and_call(
            "def t(name, **kwargs):\n    pass\n",
            't(name = "a_test", srcs = ["a.py"], deps = ["//x:y"])',
        )
        bound = bind_macro_call(func_def, call, {}, {})
        self.assertEqual(bound["kwargs"], {"srcs": ["a.py"], "deps": ["//x:y"]})

    def test_call_site_star_unpacking_declines(self) -> None:
        func_def, call = _def_and_call("def t(name, srcs = []):\n    pass\n", "t(*_A)")
        self.assertIsNone(bind_macro_call(func_def, call, {"_A": ["a_test"]}, {}))

    def test_call_site_doublestar_unpacking_declines(self) -> None:
        func_def, call = _def_and_call("def t(name):\n    pass\n", "t(**_E)")
        self.assertIsNone(bind_macro_call(func_def, call, {"_E": {"name": "a"}}, {}))

    def test_too_many_positional_arguments_declines(self) -> None:
        func_def, call = _def_and_call(
            "def t(name):\n    pass\n", 't("a_test", "extra")'
        )
        self.assertIsNone(bind_macro_call(func_def, call, {}, {}))

    def test_unknown_keyword_with_no_kwargs_catchall_declines(self) -> None:
        func_def, call = _def_and_call(
            "def t(name):\n    pass\n", 't(name = "a_test", bogus = 1)'
        )
        self.assertIsNone(bind_macro_call(func_def, call, {}, {}))

    def test_required_parameter_never_bound_declines(self) -> None:
        func_def, call = _def_and_call("def t(name, srcs):\n    pass\n", "t()")
        self.assertIsNone(bind_macro_call(func_def, call, {}, {}))

    def test_duplicate_binding_declines(self) -> None:
        func_def, call = _def_and_call(
            "def t(name):\n    pass\n", 't("a_test", name = "b_test")'
        )
        self.assertIsNone(bind_macro_call(func_def, call, {}, {}))

    def test_unfiltered_vararg_def_declines_even_called_directly(self) -> None:
        """Defense in depth: this function re-checks the shape
        param_macro_defs already filters on, rather than trusting the
        caller pre-filtered -- see the def's own docstring."""
        func_def, call = _def_and_call(
            "def t(name, *extra):\n    pass\n", 't("a_test")'
        )
        self.assertIsNone(bind_macro_call(func_def, call, {}, {}))

    def test_unfiltered_positional_only_def_declines_even_called_directly(self) -> None:
        func_def, call = _def_and_call("def t(name, /):\n    pass\n", 't("a_test")')
        self.assertIsNone(bind_macro_call(func_def, call, {}, {}))


class ParamMacroDefsTest(unittest.TestCase):
    """The def-shape filter: which non-zero-parameter signatures are recognized."""

    def test_positional_with_default_is_recognized(self) -> None:
        tree = ast.parse("def t(name, tags = []):\n    pass\n")
        self.assertIn("t", param_macro_defs(tree))

    def test_kwargs_only_is_recognized(self) -> None:
        tree = ast.parse("def t(**kwargs):\n    pass\n")
        self.assertIn("t", param_macro_defs(tree))

    def test_zero_parameter_def_is_excluded(self) -> None:
        """Already weld.strategies._bazel_loads.macro_defs's bucket -- admitting
        it here too would let the same call site expand twice."""
        self.assertEqual(param_macro_defs(ast.parse("def t():\n    pass\n")), {})

    def test_vararg_is_excluded(self) -> None:
        tree = ast.parse("def t(name, *args):\n    pass\n")
        self.assertEqual(param_macro_defs(tree), {})

    def test_keyword_only_is_excluded(self) -> None:
        tree = ast.parse("def t(name, *, tags = []):\n    pass\n")
        self.assertEqual(param_macro_defs(tree), {})

    def test_positional_only_is_excluded(self) -> None:
        tree = ast.parse("def t(name, /):\n    pass\n")
        self.assertEqual(param_macro_defs(tree), {})


class ParamMacroCallsTest(unittest.TestCase):
    """Every call site naming a known macro, any arity, never deduplicated."""

    def test_finds_every_call_site_not_just_the_first(self) -> None:
        tree = ast.parse('t(name = "a")\nt(name = "b")\n')
        self.assertEqual([n for n, _ in param_macro_calls(tree, {"t"})], ["t", "t"])

    def test_zero_argument_call_is_included(self) -> None:
        """A macro whose every parameter defaults can legally be called bare."""
        self.assertEqual(len(param_macro_calls(ast.parse("t()\n"), {"t"})), 1)

    def test_unknown_name_is_not_matched(self) -> None:
        tree = ast.parse('other(name = "a")\n')
        self.assertEqual(param_macro_calls(tree, {"t"}), [])


class ParamMacroExpansionIntegrationTest(unittest.TestCase):
    """The strategy end to end -- what closes bd iysm's measured gap."""

    def _extract(self, files: dict[str, str]):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel, text in files.items():
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
            return extract(root, {"glob": "**/BUILD.bazel"}, {})

    def test_bench_py_test_shape_expands_every_call_site(self) -> None:
        """name/tags/local + **kwargs, 3 call sites -- the measured gap."""
        res = self._extract({
            "p/wrap.bzl": (
                'load("@rules_python//python:defs.bzl", "py_test")\n'
                "def bench_py_test(name, tags = [], local = True, **kwargs):\n"
                '    if "no-sandbox" not in tags:\n'
                '        tags = tags + ["no-sandbox"]\n'
                "    py_test(name = name, local = local, tags = tags, **kwargs)\n"
            ),
            "p/BUILD.bazel": (
                'load(":wrap.bzl", "bench_py_test")\n'
                'bench_py_test(name = "a_test", srcs = ["a_test.py"], deps = ["//x:y"])\n'
                'bench_py_test(name = "b_test", srcs = ["b_test.py"], tags = ["benchmark"])\n'
                'bench_py_test(name = "c_test", srcs = ["c_test.py"])\n'
            ),
        })
        for name in ("a_test", "b_test", "c_test"):
            self.assertIn(f"test-target://p:{name}", res.nodes, name)
        self.assertEqual(
            {
                e["to"] for e in res.edges
                if e["from"] == "test-target://p:a_test" and e["type"] == "contains"
                and e["to"].startswith("file:")
            },
            {"file:p/a_test"},
        )
        deps = {
            e["to"] for e in res.edges
            if e["from"] == "test-target://p:a_test" and e["type"] == "depends_on"
        }
        self.assertIn("build-target://x:y", deps)

    def test_macro_body_reassignment_does_not_block_expansion(self) -> None:
        """ADR 0123's named boundary: the ``if`` is walked over, not executed;
        harmless here because ``tags`` is never a label attribute."""
        res = self._extract({
            "p/wrap.bzl": (
                "def m(name, tags = []):\n"
                '    if "extra" not in tags:\n'
                '        tags = tags + ["extra"]\n'
                "    py_test(name = name)\n"
            ),
            "p/BUILD.bazel": 'load(":wrap.bzl", "m")\nm(name = "a_test")\n',
        })
        self.assertIn("test-target://p:a_test", res.nodes)

    def test_multiple_call_sites_are_not_deduplicated(self) -> None:
        """Unlike a zero-arg macro, two calls carry different arguments."""
        res = self._extract({
            "p/wrap.bzl": "def m(name, srcs = []):\n    py_test(name = name, srcs = srcs)\n",
            "p/BUILD.bazel": (
                'load(":wrap.bzl", "m")\n'
                'm(name = "a_test", srcs = ["a.py"])\n'
                'm(name = "b_test", srcs = ["b.py"])\n'
            ),
        })
        # ``contains`` carries one edge per plausible node-ID spelling of a
        # srcs entry (ADR 0111); narrowed to the ``file:`` spelling, same as
        # weld_bazel_loads_test.LoadIntegrationTest._contained_files.
        self.assertEqual(
            {
                e["to"] for e in res.edges
                if e["from"] == "test-target://p:a_test" and e["type"] == "contains"
                and e["to"].startswith("file:")
            },
            {"file:p/a"},
        )
        self.assertEqual(
            {
                e["to"] for e in res.edges
                if e["from"] == "test-target://p:b_test" and e["type"] == "contains"
                and e["to"].startswith("file:")
            },
            {"file:p/b"},
        )

    def test_vararg_macro_definition_still_yields_nothing(self) -> None:
        """Pinned red-first (bd iysm): ``*args`` is outside the supported subset."""
        res = self._extract({
            "p/wrap.bzl": "def m(name, *extra):\n    py_test(name = name)\n",
            "p/BUILD.bazel": 'load(":wrap.bzl", "m")\nm("a_test")\n',
        })
        self.assertNotIn("test-target://p:a_test", res.nodes)

    def test_call_site_star_unpacking_still_yields_nothing(self) -> None:
        res = self._extract({
            "p/wrap.bzl": "def m(name):\n    py_test(name = name)\n",
            "p/BUILD.bazel": (
                'load(":wrap.bzl", "m")\nARGS = ("a_test",)\nm(*ARGS)\n'
            ),
        })
        self.assertNotIn("test-target://p:a_test", res.nodes)

    def test_parameterized_macro_target_names_the_bzl_that_declared_it(self) -> None:
        res = self._extract({
            "m/t.bzl": "def t(name):\n    py_test(name = name)\n",
            "p/BUILD.bazel": 'load("//m:t.bzl", "t")\nt(name = "a_test")\n',
        })
        self.assertIn(
            {
                "from": "test-target://p:a_test",
                "to": "file:m/t",
                "type": "depends_on",
                "props": {
                    "source_strategy": "bazel",
                    "confidence": "definite",
                    "provenance": {"file": "p/BUILD.bazel"},
                },
            },
            res.edges,
        )

    def test_repeated_extracts_agree(self) -> None:
        files = {
            "p/wrap.bzl": "def m(name, srcs = []):\n    py_test(name = name, srcs = srcs)\n",
            "p/BUILD.bazel": (
                'load(":wrap.bzl", "m")\n'
                'm(name = "a_test", srcs = ["a.py"])\n'
                'm(name = "b_test", srcs = ["b.py"])\n'
            ),
        }
        first = self._extract(files)
        second = self._extract(files)
        self.assertEqual(first.nodes, second.nodes)
        self.assertEqual(first.edges, second.edges)


if __name__ == "__main__":
    unittest.main()
