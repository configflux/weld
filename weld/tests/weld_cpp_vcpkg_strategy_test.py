"""Tests for the ``cpp_vcpkg`` strategy (ADR 0057)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


from weld.contract import CONFIDENCE_VALUES  # noqa: E402
from weld.strategies import cpp_vcpkg  # noqa: E402


_FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "cpp_vcpkg_project"
)


class VcpkgFixtureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.result = cpp_vcpkg.extract(
            _FIXTURE.parent, {"glob": "cpp_vcpkg_project/vcpkg.json"}, {},
        )

    def test_project_node_minted(self) -> None:
        self.assertIn("package:cpp:example-vcpkg", self.result.nodes)

    def test_bare_string_dep_emitted(self) -> None:
        edges_by_to = {e["to"] for e in self.result.edges}
        self.assertIn("package://vcpkg/fmt", edges_by_to)

    def test_object_dep_emitted_with_version(self) -> None:
        boost = [
            e for e in self.result.edges
            if e["to"] == "package://vcpkg/boost-system"
        ]
        self.assertEqual(len(boost), 1)
        self.assertEqual(
            boost[0]["props"]["version_constraint"], ">=1.81.0",
        )

    def test_object_dep_without_version_no_constraint(self) -> None:
        spd = [
            e for e in self.result.edges
            if e["to"] == "package://vcpkg/spdlog"
        ]
        self.assertEqual(len(spd), 1)
        self.assertNotIn("version_constraint", spd[0]["props"])

    def test_every_edge_definite(self) -> None:
        for edge in self.result.edges:
            self.assertEqual(edge["props"]["confidence"], "definite")
            self.assertEqual(edge["props"]["source_strategy"], "cpp_vcpkg")
            self.assertIn(edge["props"]["confidence"], CONFIDENCE_VALUES)


class VcpkgMalformedJsonTest(unittest.TestCase):
    def test_invalid_json_skipped(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        proj = tmp / "bad_pkg"
        proj.mkdir()
        (proj / "vcpkg.json").write_text("{ not json", encoding="utf-8")
        result = cpp_vcpkg.extract(
            tmp, {"glob": "**/vcpkg.json"}, {},
        )
        self.assertEqual(result.edges, [])
        self.assertEqual(result.discovered_from, [])

    def test_non_object_root_skipped(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        proj = tmp / "list_pkg"
        proj.mkdir()
        (proj / "vcpkg.json").write_text(
            json.dumps([{"name": "fmt"}]), encoding="utf-8",
        )
        result = cpp_vcpkg.extract(
            tmp, {"glob": "**/vcpkg.json"}, {},
        )
        self.assertEqual(result.edges, [])

    def test_fallback_project_name_from_directory(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        proj = tmp / "fallback_dir"
        proj.mkdir()
        (proj / "vcpkg.json").write_text(
            json.dumps({"dependencies": ["fmt"]}), encoding="utf-8",
        )
        result = cpp_vcpkg.extract(
            tmp, {"glob": "**/vcpkg.json"}, {},
        )
        self.assertIn("package:cpp:fallback_dir", result.nodes)


if __name__ == "__main__":
    unittest.main()
