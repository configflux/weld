"""``load()`` resolution and macro expansion in the bazel strategy.

The ADR 0044 amendment (bd 73xa, bd akwh, bd rh3l, bd oj3m). The suite inherits
the asymmetry ADR 0105 established and the tests are written to match it: every
target the strategy emits must be one bazel really has, while a construct the
evaluator cannot resolve is allowed to yield *nothing*. So the negative cases
below assert silence -- an unexpanded macro, a parameterized macro, a foreign
load -- rather than a best guess.

Hermetic throughout: every case writes the tree it needs into a temp dir. The
same modelling asserted against this repository's own BUILD files -- where all
four gaps were reported -- lives in ``weld_bazel_loads_repo_test``, which needs
the host tree and is tagged accordingly. What a hostile ``load()`` may *cost
and reach* -- the containment half, a security question with its own ADR --
lives in ``weld_bazel_loads_containment_test``.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld.strategies._bazel_loads import (
    load_module,
    resolve_bzl_label,
)
from weld.strategies.bazel import extract


def _reader(files: dict[str, str]):
    return lambda rel: files.get(rel)


class ResolveBzlLabelTest(unittest.TestCase):
    """Label -> repo-relative path. Total, and unforgiving about scope."""

    def test_absolute_label(self) -> None:
        self.assertEqual(
            resolve_bzl_label("//weld:runtime_srcs.bzl", "tools"),
            "weld/runtime_srcs.bzl",
        )

    def test_relative_label_resolves_against_package(self) -> None:
        self.assertEqual(resolve_bzl_label(":srcs.bzl", "tools"), "tools/srcs.bzl")

    def test_relative_label_at_repo_root(self) -> None:
        self.assertEqual(resolve_bzl_label(":top.bzl", ""), "top.bzl")

    def test_root_package_absolute(self) -> None:
        self.assertEqual(resolve_bzl_label("//:top.bzl", "weld"), "top.bzl")

    def test_foreign_workspace_is_dropped(self) -> None:
        self.assertIsNone(
            resolve_bzl_label("@rules_python//python:defs.bzl", "weld")
        )

    def test_non_bzl_suffix_is_dropped(self) -> None:
        self.assertIsNone(resolve_bzl_label("//weld:runtime", "weld"))

    def test_escaping_label_is_dropped(self) -> None:
        """A BUILD file is repository input reaching a filesystem read."""
        self.assertIsNone(resolve_bzl_label("//..:x.bzl", "weld"))
        self.assertIsNone(resolve_bzl_label(":../../secrets.bzl", "weld"))
        self.assertIsNone(resolve_bzl_label("//../etc:passwd.bzl", ""))

    def test_malformed_labels_are_dropped(self) -> None:
        for label in ("", ":", "//weld:", "weld/runtime_srcs.bzl", "//x.bzl"):
            self.assertIsNone(resolve_bzl_label(label, "weld"), label)


class LoadModuleTest(unittest.TestCase):
    """What a ``.bzl`` exports, and where each export really came from."""

    def test_constant_is_exported_with_its_own_path_as_origin(self) -> None:
        mod = load_module("weld/srcs.bzl", _reader({
            "weld/srcs.bzl": 'SRCS = ["a.py", "b.py"]\n',
        }))
        self.assertEqual(mod.bindings["SRCS"], ["a.py", "b.py"])
        self.assertEqual(mod.origins["SRCS"], "weld/srcs.bzl")
        self.assertEqual(mod.read_paths, ["weld/srcs.bzl"])

    def test_bindings_fold_sequentially(self) -> None:
        mod = load_module("p/x.bzl", _reader({
            "p/x.bzl": 'A = ["a"]\nB = ["b"]\nALL = A + B\n',
        }))
        self.assertEqual(mod.bindings["ALL"], ["a", "b"])

    def test_forwarded_constant_keeps_its_defining_origin(self) -> None:
        """A re-exporter is not where a reader should be sent to edit."""
        mod = load_module("p/mid.bzl", _reader({
            "p/mid.bzl": 'load(":base.bzl", "SRCS")\nDOUBLED = SRCS + SRCS\n',
            "p/base.bzl": 'SRCS = ["a.py"]\n',
        }))
        self.assertEqual(mod.origins["SRCS"], "p/base.bzl")
        self.assertEqual(mod.origins["DOUBLED"], "p/mid.bzl")
        self.assertIn("p/base.bzl", mod.read_paths)

    def test_zero_parameter_def_is_a_macro(self) -> None:
        mod = load_module("p/m.bzl", _reader({
            "p/m.bzl": 'def m():\n    py_test(name = "t")\n',
        }))
        self.assertIn("m", mod.macros)
        self.assertEqual(mod.macros["m"].path, "p/m.bzl")

    def test_parameterized_def_is_not_a_macro(self) -> None:
        """No parameter binding exists, so it is not one this module expands."""
        mod = load_module("p/m.bzl", _reader({
            "p/m.bzl": 'def m(name):\n    py_test(name = name)\n',
        }))
        self.assertEqual(mod.macros, {})

    def test_missing_file_exports_nothing(self) -> None:
        mod = load_module("p/gone.bzl", _reader({}))
        self.assertEqual((mod.bindings, mod.macros, mod.read_paths), ({}, {}, []))

    def test_unparseable_file_exports_nothing_but_is_still_provenance(self) -> None:
        mod = load_module("p/bad.bzl", _reader({"p/bad.bzl": "def ((("}))
        self.assertEqual(mod.bindings, {})
        self.assertEqual(mod.read_paths, ["p/bad.bzl"])

    def test_load_cycle_terminates(self) -> None:
        mod = load_module("p/a.bzl", _reader({
            "p/a.bzl": 'load(":b.bzl", "B")\nA = B\n',
            "p/b.bzl": 'load(":a.bzl", "A")\nB = ["b"]\n',
        }))
        self.assertEqual(mod.bindings["A"], ["b"])


class LoadIntegrationTest(unittest.TestCase):
    """The strategy end to end, over a tree written for each case."""

    def _contained_files(self, res, from_id: str) -> set[str]:
        """``contains`` targets of *from_id*, narrowed to ``file:``.

        A ``srcs`` entry emits one edge per plausible node-ID spelling (ADR
        0111) and the post-processor's dangling-edge sweep -- which a bare
        ``extract`` does not run -- keeps whichever resolved. The tests
        below are about which *file* reached ``srcs`` through a load, so
        they read the ``file:`` spelling; the candidate set itself is
        ``weld_bazel_labels_test``'s subject.
        """
        return {
            e["to"] for e in res.edges
            if e["from"] == from_id and e["type"] == "contains"
            and e["to"].startswith("file:")
        }

    def _extract(self, files: dict[str, str]) -> tuple:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel, text in files.items():
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
            ctx: dict = {}
            return extract(root, {"glob": "**/BUILD.bazel"}, ctx), ctx

    def test_manifest_constant_becomes_real_srcs(self) -> None:
        """bd rh3l A2: the manifest's value, not merely its name."""
        res, _ = self._extract({
            "weld/runtime_srcs.bzl": 'RUNTIME_SRCS = ["graph.py", "cli.py"]\n',
            "weld/BUILD.bazel": (
                'load(":runtime_srcs.bzl", "RUNTIME_SRCS")\n'
                'py_library(name = "runtime", srcs = RUNTIME_SRCS)\n'
            ),
        })
        self.assertEqual(
            self._contained_files(res, "build-target://weld:runtime"),
            {"file:weld/graph", "file:weld/cli"},
        )

    def test_manifest_gets_a_node_and_an_edge(self) -> None:
        """bd rh3l A1: "where do I register a new module" is a graph question."""
        res, _ = self._extract({
            "weld/runtime_srcs.bzl": 'RUNTIME_SRCS = ["graph.py"]\n',
            "weld/BUILD.bazel": (
                'load(":runtime_srcs.bzl", "RUNTIME_SRCS")\n'
                'py_library(name = "runtime", srcs = RUNTIME_SRCS)\n'
                'py_library(name = "other", srcs = ["other.py"])\n'
            ),
        })
        node = res.nodes["file:weld/runtime_srcs"]
        self.assertEqual(node["type"], "file")
        self.assertEqual(node["label"], "runtime_srcs")
        self.assertEqual(node["props"]["file"], "weld/runtime_srcs.bzl")

        pointing = {
            e["from"] for e in res.edges
            if e["to"] == "file:weld/runtime_srcs" and e["type"] == "depends_on"
        }
        # Precise, not package-wide: ``other`` never reads the manifest.
        self.assertEqual(pointing, {"build-target://weld:runtime"})

    def test_bzl_is_recorded_as_provenance(self) -> None:
        """Without this, editing a macro would not mark the graph stale."""
        res, _ = self._extract({
            "weld/runtime_srcs.bzl": 'RUNTIME_SRCS = ["graph.py"]\n',
            "weld/BUILD.bazel": (
                'load(":runtime_srcs.bzl", "RUNTIME_SRCS")\n'
                'py_library(name = "runtime", srcs = RUNTIME_SRCS)\n'
            ),
        })
        self.assertIn("weld/runtime_srcs.bzl", res.discovered_from)
        self.assertIn("weld/BUILD.bazel", res.discovered_from)

    def test_macro_emits_into_the_calling_package(self) -> None:
        """bd akwh's non-negotiable: never the .bzl's own directory."""
        res, _ = self._extract({
            "macros/tests.bzl": (
                "def tests():\n"
                '    py_test(name = "alpha_test", srcs = ["alpha_test.py"])\n'
            ),
            "weld/tests/BUILD.bazel": (
                'load("//macros:tests.bzl", "tests")\n'
                "tests()\n"
            ),
        })
        self.assertIn("test-target://weld/tests:alpha_test", res.nodes)
        self.assertNotIn("test-target://macros:alpha_test", res.nodes)
        self.assertEqual(
            self._contained_files(res, "test-target://weld/tests:alpha_test"),
            {"file:weld/tests/alpha_test"},
        )

    def test_macro_target_names_the_bzl_that_declared_it(self) -> None:
        res, _ = self._extract({
            "macros/tests.bzl": 'def tests():\n    py_test(name = "a_test")\n',
            "weld/tests/BUILD.bazel": (
                'load("//macros:tests.bzl", "tests")\ntests()\n'
            ),
        })
        # ``to`` is the .bzl that DECLARED the target; ``provenance.file`` is
        # the BUILD file whose read PRODUCED the edge. They differ here, which
        # is ADR 0074's direction rule holding: provenance is always the
        # producer, never the endpoint, so "is this edge stale" asks about the
        # file that would be re-read to re-mint it (bd cpkp).
        self.assertIn(
            {
                "from": "test-target://weld/tests:a_test",
                "to": "file:macros/tests",
                "type": "depends_on",
                "props": {
                    "source_strategy": "bazel",
                    "confidence": "definite",
                    "provenance": {"file": "weld/tests/BUILD.bazel"},
                },
            },
            res.edges,
        )

    def test_macro_body_sees_its_own_module_constants(self) -> None:
        res, _ = self._extract({
            "m/t.bzl": (
                '_NAMES = ("a_test", "b_test")\n'
                "def t():\n"
                "    [py_test(name = _n, srcs = [_n + '.py']) for _n in _NAMES]\n"
            ),
            "p/BUILD.bazel": 'load("//m:t.bzl", "t")\nt()\n',
        })
        self.assertIn("test-target://p:a_test", res.nodes)
        self.assertIn("test-target://p:b_test", res.nodes)

    def test_macro_body_native_prefix_is_a_rule(self) -> None:
        """Inside a .bzl, ``native.filegroup`` IS ``filegroup``."""
        res, _ = self._extract({
            "m/t.bzl": (
                "def t():\n"
                '    native.filegroup(name = "fixtures", srcs = ["a.json"])\n'
            ),
            "p/BUILD.bazel": 'load("//m:t.bzl", "t")\nt()\n',
        })
        self.assertIn("build-target://p:fixtures", res.nodes)

    def test_uncalled_macro_declares_nothing(self) -> None:
        res, _ = self._extract({
            "m/t.bzl": 'def t():\n    py_test(name = "ghost_test")\n',
            "p/BUILD.bazel": 'load("//m:t.bzl", "t")\n',
        })
        self.assertNotIn("test-target://p:ghost_test", res.nodes)

    def test_macro_called_with_arguments_declares_nothing(self) -> None:
        res, _ = self._extract({
            "m/t.bzl": 'def t():\n    py_test(name = "ghost_test")\n',
            "p/BUILD.bazel": 'load("//m:t.bzl", "t")\nt("x")\n',
        })
        self.assertNotIn("test-target://p:ghost_test", res.nodes)

    def test_foreign_load_yields_nothing(self) -> None:
        res, _ = self._extract({
            "p/BUILD.bazel": (
                'load("@rules_python//python:defs.bzl", "py_test")\n'
                'py_test(name = "a_test", srcs = ["a_test.py"])\n'
            ),
        })
        self.assertIn("test-target://p:a_test", res.nodes)
        self.assertEqual(
            [n for n in res.nodes if n.startswith("file:") and "defs" in n], []
        )

    def test_build_file_local_constant_resolves(self) -> None:
        """A pre-existing miss the same binding work closes."""
        res, _ = self._extract({
            "p/BUILD.bazel": 'SRCS = ["a.py"]\npy_library(name = "lib", srcs = SRCS)\n',
        })
        self.assertEqual(
            self._contained_files(res, "build-target://p:lib"), {"file:p/a"},
        )

    def test_data_target_label_resolves_to_the_target(self) -> None:
        """bd oj3m A4, and why the target reading must be tried first."""
        res, _ = self._extract({
            "examples/BUILD.bazel": 'filegroup(name = "example_files", srcs = ["a.md"])\n',
            "weld/tests/BUILD.bazel": (
                'py_test(name = "examples_test", srcs = ["examples_test.py"],'
                ' data = ["//examples:example_files"])\n'
            ),
        })
        deps = {
            e["to"] for e in res.edges
            if e["from"] == "test-target://weld/tests:examples_test"
            and e["type"] == "depends_on"
        }
        self.assertIn("build-target://examples:example_files", deps)
        self.assertNotIn("file:examples/example_files", deps)

    def test_data_file_label_resolves_to_the_file(self) -> None:
        """And to the ID class the file's own strategy would have minted.

        The fixture's ``data`` entry is a *shell* script, which reaches the
        graph as ``tool:`` -- ``tool_script`` mints it, nothing mints
        ``file:p/helper``. This asserted the ``file:`` spelling, which is
        how the wrong guess went unnoticed: an edge to a node that does not
        exist is dropped by the dangling sweep in silence, so the test
        passed on output the real graph then threw away (bd i7ny, ADR
        0111). ``file:`` must be *absent* as well as ``tool:`` present --
        ``file_id`` strips the extension, so offering it would let this
        edge land on an unrelated ``helper.py``.
        """
        res, _ = self._extract({
            "p/BUILD.bazel": (
                'py_test(name = "a_test", srcs = ["a_test.py"],'
                ' data = ["helper.sh"])\n'
            ),
        })
        deps = {
            e["to"] for e in res.edges
            if e["from"] == "test-target://p:a_test" and e["type"] == "depends_on"
        }
        self.assertIn("tool:p/helper", deps)
        self.assertNotIn("file:p/helper", deps)


if __name__ == "__main__":
    unittest.main()
