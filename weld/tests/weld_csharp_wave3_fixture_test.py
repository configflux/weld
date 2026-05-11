"""Integration test: Wave 3 C# strategies on the reference fixture.

Covers two surfaces:

1. ``csharp_msbuild_targets`` against the
   ``src/Sample.Web/Sample.Web.csproj`` custom MSBuild target
   ``GenerateClientCode`` (declared with both ``BeforeTargets="Build"``
   and ``AfterTargets="Restore"``). The fixture asserts that the
   strategy emits the build-target node and both ordering edges.
2. ADR 0050 confidence coverage on every emitted edge / node from the
   Wave 3 strategy.

Partial-class merging is covered separately by
``weld_csharp_partial_class_test`` (unit-level, tree-sitter mocked).
The fixture carries the partial-class declarations so the surface is
ready for graph-integrity tests but does not need to be re-asserted
here.

The fixture upgrade (custom Target + partial-class .cs files) does
not regress any Wave 1 / Wave 2 assertion; see
``weld_csharp_wave2_fixture_test`` and ``weld_csharp_project_fixture_test``
for the older invariants.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

# Ensure the in-source weld package is on sys.path when run outside
# Bazel.
_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.contract import CONFIDENCE_VALUES  # noqa: E402
from weld.strategies.csharp_msbuild_targets import (  # noqa: E402
    extract as msbuild_extract,
)


def _fixture_root() -> Path:
    """Return the fixture path, preferring Bazel runfiles when present."""
    here = Path(__file__).resolve().parent
    candidate = here / "fixtures" / "csharp_project"
    if candidate.is_dir():
        return candidate
    runfiles = os.environ.get("RUNFILES_DIR")
    if runfiles:
        rf_candidate = (
            Path(runfiles)
            / "_main"
            / "weld"
            / "tests"
            / "fixtures"
            / "csharp_project"
        )
        if rf_candidate.is_dir():
            return rf_candidate
    return candidate


class CsharpMsbuildTargetsFixtureTest(unittest.TestCase):
    """MSBuild target extraction on the Wave 3 fixture upgrade."""

    def setUp(self) -> None:
        self.root = _fixture_root()
        if not self.root.is_dir():
            self.skipTest(f"Fixture not reachable at {self.root}")
        self.result = msbuild_extract(
            self.root, {"glob": "**/*.csproj"}, {},
        )

    def test_generate_client_code_target_emitted(self) -> None:
        target_id = "build-target://Sample.Web:GenerateClientCode"
        self.assertIn(target_id, self.result.nodes)
        node = self.result.nodes[target_id]
        self.assertEqual(node["type"], "build-target")
        self.assertEqual(
            node["props"]["source_strategy"], "csharp_msbuild_targets",
        )
        self.assertEqual(node["props"]["confidence"], "definite")
        self.assertEqual(node["props"]["target_name"], "GenerateClientCode")
        self.assertEqual(node["props"]["owner"], "Sample.Web")
        # The fixture target contains one ItemGroup with one item
        # (``GeneratedFile Include="generated\Client.cs"``).
        self.assertEqual(node["props"]["itemgroup_count"], 1)
        self.assertEqual(node["props"]["item_count"], 1)

    def test_before_and_after_ordering_edges_emitted(self) -> None:
        # ``BeforeTargets="Build" AfterTargets="Restore"`` -> two edges:
        # GenerateClientCode -[depends_on, before]-> Build
        # GenerateClientCode -[depends_on, after]->  Restore
        target_id = "build-target://Sample.Web:GenerateClientCode"
        ordering_edges = {
            (edge["to"], edge["props"]["ordering"])
            for edge in self.result.edges
            if edge["type"] == "depends_on"
            and edge["from"] == target_id
        }
        self.assertEqual(
            ordering_edges,
            {
                ("build-target://Sample.Web:Build", "before"),
                ("build-target://Sample.Web:Restore", "after"),
            },
        )

    def test_every_emitted_edge_is_definite(self) -> None:
        # ADR 0050 invariant: every edge carries a confidence value.
        # csharp_msbuild_targets is XML-parsed, so every edge is
        # ``definite``.
        self.assertTrue(self.result.edges)
        for edge in self.result.edges:
            self.assertEqual(edge["props"]["confidence"], "definite")
            self.assertIn(edge["props"]["confidence"], CONFIDENCE_VALUES)

    def test_every_emitted_node_is_definite(self) -> None:
        self.assertTrue(self.result.nodes)
        for node in self.result.nodes.values():
            self.assertEqual(node["props"]["confidence"], "definite")
            self.assertIn(node["props"]["confidence"], CONFIDENCE_VALUES)


class CsharpWave3PartialFixtureFilesPresentTest(unittest.TestCase):
    """The fixture ships the partial-class halves used by Wave 3."""

    def setUp(self) -> None:
        self.root = _fixture_root()
        if not self.root.is_dir():
            self.skipTest(f"Fixture not reachable at {self.root}")

    def test_validation_half_present(self) -> None:
        path = (
            self.root
            / "src"
            / "Sample.Web"
            / "OrderProcessor.Validation.cs"
        )
        self.assertTrue(
            path.is_file(),
            f"Wave 3 fixture missing partial-class half: {path}",
        )
        text = path.read_text(encoding="utf-8")
        self.assertIn("partial class OrderProcessor", text)
        self.assertIn("namespace Sample.Web", text)

    def test_persistence_half_present(self) -> None:
        path = (
            self.root
            / "src"
            / "Sample.Web"
            / "OrderProcessor.Persistence.cs"
        )
        self.assertTrue(
            path.is_file(),
            f"Wave 3 fixture missing partial-class half: {path}",
        )
        text = path.read_text(encoding="utf-8")
        self.assertIn("partial class OrderProcessor", text)
        self.assertIn("namespace Sample.Web", text)


if __name__ == "__main__":
    unittest.main()
