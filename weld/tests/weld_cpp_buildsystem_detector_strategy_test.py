"""Tests for the ``cpp_buildsystem_detector`` strategy (ADR 0057)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.contract import CONFIDENCE_VALUES  # noqa: E402
from weld.strategies import cpp_buildsystem_detector  # noqa: E402


def _make_tree(files: dict[str, str]) -> Path:
    tmp = Path(tempfile.mkdtemp())
    for rel, body in files.items():
        target = tmp / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return tmp


class BuildsystemDetectorTest(unittest.TestCase):
    def test_cmake_root_detected(self) -> None:
        tmp = _make_tree(
            {"my_app/CMakeLists.txt": "project(my_app)\n"},
        )
        result = cpp_buildsystem_detector.extract(
            tmp, {"glob": "**/CMakeLists.txt"}, {},
        )
        self.assertIn("package:cpp:my_app", result.nodes)
        project = result.nodes["package:cpp:my_app"]
        self.assertEqual(project["props"]["build_system"], "cmake")
        target_nid = "build-target:cmake:my_app:CMakeLists"
        self.assertIn(target_nid, result.nodes)
        self.assertNotIn(
            "unsupported_build_system",
            result.nodes[target_nid]["props"],
        )

    def test_makefile_flagged_unsupported(self) -> None:
        tmp = _make_tree({"legacy/Makefile": "all:\n\techo hi\n"})
        result = cpp_buildsystem_detector.extract(
            tmp, {"glob": "**/Makefile"}, {},
        )
        target_nid = "build-target:make:legacy:Makefile"
        self.assertIn(target_nid, result.nodes)
        self.assertEqual(
            result.nodes[target_nid]["props"]["unsupported_build_system"],
            "make",
        )

    def test_meson_flagged_unsupported(self) -> None:
        tmp = _make_tree({"m/meson.build": "project('m', 'cpp')\n"})
        result = cpp_buildsystem_detector.extract(
            tmp, {"glob": "**/meson.build"}, {},
        )
        target_nid = "build-target:meson:m:meson"
        self.assertIn(target_nid, result.nodes)
        self.assertEqual(
            result.nodes[target_nid]["props"]["unsupported_build_system"],
            "meson",
        )

    def test_bazel_root_detected(self) -> None:
        tmp = _make_tree(
            {"bzl_pkg/BUILD.bazel": 'py_library(name = "x")\n'},
        )
        result = cpp_buildsystem_detector.extract(
            tmp, {"glob": "**/BUILD.bazel"}, {},
        )
        target_nid = "build-target:bazel:bzl_pkg:BUILD"
        self.assertIn(target_nid, result.nodes)
        self.assertNotIn(
            "unsupported_build_system",
            result.nodes[target_nid]["props"],
        )

    def test_every_edge_has_confidence(self) -> None:
        tmp = _make_tree(
            {
                "pkg_a/CMakeLists.txt": "project(a)\n",
                "pkg_b/Makefile": "all:\n",
            },
        )
        result = cpp_buildsystem_detector.extract(
            tmp,
            {"glob": "**/CMakeLists.txt"},
            {},
        )
        result_make = cpp_buildsystem_detector.extract(
            tmp,
            {"glob": "**/Makefile"},
            {},
        )
        for edge in [*result.edges, *result_make.edges]:
            self.assertIn(edge["props"]["confidence"], CONFIDENCE_VALUES)
            self.assertEqual(
                edge["props"]["source_strategy"],
                "cpp_buildsystem_detector",
            )

    def test_unknown_file_ignored(self) -> None:
        tmp = _make_tree({"x/random.txt": "noop\n"})
        result = cpp_buildsystem_detector.extract(
            tmp, {"glob": "**/random.txt"}, {},
        )
        self.assertEqual(result.discovered_from, [])
        self.assertEqual(result.edges, [])


if __name__ == "__main__":
    unittest.main()
