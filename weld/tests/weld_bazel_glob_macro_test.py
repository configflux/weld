"""``glob()``/``native.glob()`` inside an expanded macro body (bd x9lg, ADR
0044 amendment).

Before this change, ``_expand_loads`` (``weld/strategies/bazel.py``)
evaluated a macro body under ``macro.bindings`` alone, which never carried
the glob resolver -- only the calling BUILD file's own top-level ``env`` did.
Three real targets in this repo hit exactly this shape --
``weld/tests/examples_tests.bzl``'s ``demo_discover_golden_files``,
``tools/tier_check_gate_targets.bzl``'s ``tier_check_gate_lane_wiring_test``,
``tools/publish_targets.bzl``'s ``publishignore_completeness_test`` -- and
silently lost real membership. ``native.glob(...)`` is also the only
spelling a ``.bzl`` file may legally use (a bare ``glob`` name is not in
scope there, the same way a bare ``py_test`` is not), so ``eval_expr`` had to
recognize it as a second call shape -- see ``weld_bazel_glob_test.py`` for
that half, unit-tested at the evaluator level. This file is the integration
half: the full ``extract()`` pipeline, real files on disk.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld.strategies.bazel import extract


class GlobInMacroBodyIntegrationTest(unittest.TestCase):

    def _extract(self, files: dict[str, str]):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel, text in files.items():
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
            return extract(root, {"glob": "**/BUILD.bazel"}, {})

    @staticmethod
    def _to_ids(res, from_nid: str, edge_type: str, prefix: str) -> set[str]:
        """Edge destinations of *edge_type* from *from_nid*, narrowed to *prefix*.

        A ``srcs``/``data`` entry resolves to every plausible node-ID spelling
        (ADR 0111) -- ``.py`` offers both ``file:`` and ``config:``, an
        extension outside :data:`weld.strategies._target_ids.FILE_NODE_EXTENSIONS`
        offers only ``config:``. Narrowing to one prefix here is what
        :mod:`weld.tests.weld_bazel_macro_args_test` already does for the same
        reason: this suite is pinning glob resolution, not the separate,
        already-tested ID-spelling contract.
        """
        return {
            e["to"] for e in res.edges
            if e["from"] == from_nid and e["type"] == edge_type
            and e["to"].startswith(prefix)
        }

    def test_native_glob_in_a_zero_arg_macro_filegroup(self) -> None:
        """Mirrors ``demo_discover_golden_files``: a filegroup whose whole
        ``srcs`` is a macro-body ``native.glob(...)`` call. ``.json`` is not a
        ``file:``-eligible extension (ADR 0111), so the real repo target's own
        edges land as ``config:`` -- asserted here exactly as measured."""
        res = self._extract({
            "p/golden/a.json": "{}",
            "p/golden/b.json": "{}",
            "p/defs.bzl": (
                "def m():\n"
                '    native.filegroup(name = "fg", srcs = native.glob(["golden/*.json"]))\n'
            ),
            "p/BUILD.bazel": 'load(":defs.bzl", "m")\nm()\n',
        })
        contains = self._to_ids(res, "build-target://p:fg", "contains", "config:")
        self.assertEqual(contains, {"config:p_golden_a_json", "config:p_golden_b_json"})

    def test_native_glob_composed_with_a_literal_in_data(self) -> None:
        """The sharper regression: ``[...] + native.glob(...)`` previously lost
        the WHOLE list, literal entry included, because ``BinOp.Add`` demands
        both sides evaluate (``tier_check_gate_lane_wiring_test``'s shape)."""
        res = self._extract({
            "p/extra_a.py": "",
            "p/defs.bzl": (
                "def m():\n"
                '    py_test(name = "t", data = ["fixed.txt"] + '
                'native.glob(["extra_*.py"]))\n'
            ),
            "p/BUILD.bazel": 'load(":defs.bzl", "m")\nm()\n',
        })
        data_edges = {
            e["to"] for e in res.edges
            if e["from"] == "test-target://p:t" and e["type"] == "depends_on"
        }
        self.assertIn("file:p/extra_a", data_edges)  # the glob member, .py
        self.assertIn("config:p_fixed_txt", data_edges)  # the literal, .txt

    def test_native_glob_in_a_parameterized_macro(self) -> None:
        res = self._extract({
            "p/a.py": "",
            "p/defs.bzl": "def m(name, **kwargs):\n    py_test(name = name, **kwargs)\n",
            "p/BUILD.bazel": (
                'load(":defs.bzl", "m")\n'
                'm(name = "t", srcs = native.glob(["*.py"]))\n'
            ),
        })
        contains = self._to_ids(res, "test-target://p:t", "contains", "file:")
        self.assertEqual(contains, {"file:p/a"})

    def test_bare_glob_in_a_macro_body_also_resolves(self) -> None:
        """Not valid real Starlark, but the same bindings-threading fix covers
        it, and x9lg names it as its own shape: pin the behaviour either way."""
        res = self._extract({
            "p/a.py": "",
            "p/defs.bzl": 'def m():\n    py_test(name = "t", srcs = glob(["*.py"]))\n',
            "p/BUILD.bazel": 'load(":defs.bzl", "m")\nm()\n',
        })
        contains = self._to_ids(res, "test-target://p:t", "contains", "file:")
        self.assertEqual(contains, {"file:p/a"})

    def test_glob_resolves_against_the_calling_package_not_the_bzl(self) -> None:
        """The same macro, loaded by two different packages, must glob each
        caller's own files -- never the ``.bzl``'s directory, and never a
        result cached from the other caller (x9lg's own stated answer)."""
        res = self._extract({
            "shared/defs.bzl": (
                'def m():\n'
                '    native.filegroup(name = "fg", srcs = native.glob(["*.py"]))\n'
            ),
            "shared/only_here.py": "",
            "a/only_a.py": "",
            "a/BUILD.bazel": 'load("//shared:defs.bzl", "m")\nm()\n',
            "b/only_b.py": "",
            "b/BUILD.bazel": 'load("//shared:defs.bzl", "m")\nm()\n',
        })
        a_contains = self._to_ids(res, "build-target://a:fg", "contains", "file:")
        b_contains = self._to_ids(res, "build-target://b:fg", "contains", "file:")
        self.assertEqual(a_contains, {"file:a/only_a"})
        self.assertEqual(b_contains, {"file:b/only_b"})

    def test_native_glob_at_build_top_level_also_resolves(self) -> None:
        """Regression guard: the wider recognizer must not narrow the
        already-working bare-``glob()``-at-BUILD-level path, and the
        ``native.``-prefixed spelling now works there too even though no
        BUILD file in this repo uses it (only ``.bzl`` macro bodies do)."""
        res = self._extract({
            "p/a.py": "",
            "p/BUILD.bazel": 'py_test(name = "t", srcs = native.glob(["*.py"]))\n',
        })
        contains = self._to_ids(res, "test-target://p:t", "contains", "file:")
        self.assertEqual(contains, {"file:p/a"})

    def test_glob_in_a_macro_default_expression_is_unevaluatable(self) -> None:
        """Named boundary (ADR 0126): a default is evaluated against the
        defining ``.bzl``'s own scope, which this fix does not extend with
        the calling package's glob resolver -- unmeasured in this repo
        (``bench_py_test``'s only defaults are ``tags``/``local`` literals),
        so decline rather than guess which package it would even mean."""
        res = self._extract({
            "p/a.py": "",
            "p/defs.bzl": (
                'def m(name, srcs = native.glob(["*.py"])):\n'
                "    py_test(name = name, srcs = srcs)\n"
            ),
            "p/BUILD.bazel": 'load(":defs.bzl", "m")\nm(name = "t")\n',
        })
        self.assertIn("test-target://p:t", res.nodes)  # name still binds
        contains = self._to_ids(res, "test-target://p:t", "contains", "file:")
        self.assertEqual(contains, set())  # srcs' default does not

    def test_repeated_extracts_agree(self) -> None:
        files = {
            "p/a.py": "",
            "p/b.py": "",
            "p/defs.bzl": (
                'def m():\n'
                '    native.filegroup(name = "fg", srcs = native.glob(["*.py"]))\n'
            ),
            "p/BUILD.bazel": 'load(":defs.bzl", "m")\nm()\n',
        }
        first = self._extract(files)
        second = self._extract(files)
        self.assertEqual(first.nodes, second.nodes)
        self.assertEqual(first.edges, second.edges)


if __name__ == "__main__":
    unittest.main()
