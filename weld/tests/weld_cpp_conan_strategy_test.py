"""Tests for the ``cpp_conan`` strategy (ADR 0057).

Covers the ``conanfile.txt`` INI parser, the ``conanfile.py`` AST walker,
the ``definite`` vs ``speculative`` confidence rules, and the fixture
integration.
"""

from __future__ import annotations

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.contract import CONFIDENCE_VALUES  # noqa: E402
from weld.strategies import cpp_conan  # noqa: E402


_FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "cpp_conan_project"
)


class ConanfileTxtTest(unittest.TestCase):
    def setUp(self) -> None:
        self.result = cpp_conan.extract(
            _FIXTURE.parent, {"glob": "cpp_conan_project/conanfile.txt"}, {},
        )

    def test_requires_emitted_as_definite(self) -> None:
        fmt_edges = [
            e for e in self.result.edges
            if e["to"] == "package://conan/fmt/9.1.0"
        ]
        self.assertTrue(fmt_edges, "fmt edge missing")
        for edge in fmt_edges:
            self.assertEqual(edge["props"]["confidence"], "definite")
            self.assertEqual(edge["props"]["kind"], "requires")

    def test_build_requires_emitted(self) -> None:
        cmake_edges = [
            e for e in self.result.edges
            if e["to"] == "package://conan/cmake/3.27.0"
        ]
        self.assertEqual(len(cmake_edges), 1)
        self.assertEqual(cmake_edges[0]["props"]["kind"], "build_requires")

    def test_project_node_minted(self) -> None:
        self.assertIn("package:cpp:cpp_conan_project", self.result.nodes)

    def test_every_edge_has_confidence(self) -> None:
        for edge in self.result.edges:
            self.assertIn(edge["props"]["confidence"], CONFIDENCE_VALUES)


class ConanfilePyTest(unittest.TestCase):
    def _run(self, source: str) -> object:
        tmp = Path(tempfile.mkdtemp())
        proj = tmp / "my_conan_proj"
        proj.mkdir()
        (proj / "conanfile.py").write_text(source, encoding="utf-8")
        return cpp_conan.extract(
            tmp, {"glob": "**/conanfile.py"}, {},
        )

    def test_class_attribute_requires_definite(self) -> None:
        result = self._run(
            textwrap.dedent(
                """\
                from conan import ConanFile

                class MyRecipe(ConanFile):
                    requires = ("fmt/9.1.0", "spdlog/1.11.0")
                    build_requires = ("cmake/3.27.0",)
                """,
            ),
        )
        edges_by_to = {e["to"]: e for e in result.edges}
        self.assertIn("package://conan/fmt/9.1.0", edges_by_to)
        self.assertEqual(
            edges_by_to["package://conan/fmt/9.1.0"]["props"]["confidence"],
            "definite",
        )
        self.assertIn("package://conan/cmake/3.27.0", edges_by_to)

    def test_self_requires_call_definite(self) -> None:
        result = self._run(
            textwrap.dedent(
                """\
                from conan import ConanFile

                class MyRecipe(ConanFile):
                    def requirements(self):
                        self.requires("fmt/9.1.0")
                        self.build_requires("cmake/3.27.0")
                """,
            ),
        )
        edges_by_to = {e["to"]: e for e in result.edges}
        self.assertIn("package://conan/fmt/9.1.0", edges_by_to)
        self.assertEqual(
            edges_by_to["package://conan/fmt/9.1.0"]["props"]["confidence"],
            "definite",
        )

    def test_variable_requires_speculative(self) -> None:
        result = self._run(
            textwrap.dedent(
                """\
                from conan import ConanFile

                FMT_DEP = "fmt/9.1.0"

                class MyRecipe(ConanFile):
                    requires = (FMT_DEP, "spdlog/1.11.0")
                """,
            ),
        )
        # Only the literal spdlog edge is emitted, marked speculative
        # because the tuple was mixed.
        spd_edges = [
            e for e in result.edges
            if e["to"] == "package://conan/spdlog/1.11.0"
        ]
        self.assertEqual(len(spd_edges), 1)
        self.assertEqual(spd_edges[0]["props"]["confidence"], "speculative")

    def test_syntax_error_safe(self) -> None:
        # Should not raise -- malformed conanfile.py is skipped.
        result = self._run("def broken(:\n    pass\n")
        self.assertEqual(result.edges, [])

    def test_every_edge_has_source_strategy_and_confidence(self) -> None:
        result = self._run(
            textwrap.dedent(
                """\
                class R:
                    requires = ("fmt/9.1.0",)
                """,
            ),
        )
        for edge in result.edges:
            self.assertEqual(
                edge["props"]["source_strategy"], "cpp_conan",
            )
            self.assertIn(edge["props"]["confidence"], CONFIDENCE_VALUES)


if __name__ == "__main__":
    unittest.main()
