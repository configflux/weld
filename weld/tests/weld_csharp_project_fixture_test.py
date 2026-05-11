"""Integration test: csharp_project + csharp_solution on the reference fixture.

Exercises the two ADR 0056 Wave 1 strategies against the upgraded
``weld/tests/fixtures/csharp_project/`` ASP.NET Core + EF Core sample.
The fixture is a small but realistic shape: one solution, three
projects (Web / DAL / Tests), shared Directory.Build.props, and the
Wave 2 seams (controller attributes, DbContext, ``[Fact]``) that
downstream strategies will consume.

The assertions here cover the *combined* graph: both strategies must
mint the right nodes and the solution's ``contains`` edges must
resolve to projects emitted by ``csharp_project``. This is the
canary that catches a regression where one strategy renames its IDs
without the other one following along.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

# Ensure the in-source weld package is on sys.path when run outside
# Bazel. The test file lives two levels under the repo root.
_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.contract import CONFIDENCE_VALUES  # noqa: E402
from weld.strategies.csharp_project import extract as csharp_project_extract  # noqa: E402
from weld.strategies.csharp_solution import extract as csharp_solution_extract  # noqa: E402


def _fixture_root() -> Path:
    """Return the fixture path, preferring the Bazel runfiles location.

    Under Bazel the fixture ships as a runfiles ``filegroup`` next to
    the test runner, so reads succeed from a sandboxed cwd. Outside
    Bazel we fall back to walking up from this file.
    """
    here = Path(__file__).resolve().parent
    candidate = here / "fixtures" / "csharp_project"
    if candidate.is_dir():
        return candidate
    # Bazel sandbox: runfiles tree exposes the fixture via the
    # source-relative location.
    runfiles = os.environ.get("RUNFILES_DIR")
    if runfiles:
        rf_candidate = Path(runfiles) / "_main" / "weld" / "tests" / "fixtures" / "csharp_project"
        if rf_candidate.is_dir():
            return rf_candidate
    return candidate  # may not exist; the test will skipTest below


class CsharpProjectFixtureTest(unittest.TestCase):
    """End-to-end shape of the upgraded csharp_project fixture."""

    def setUp(self) -> None:
        self.root = _fixture_root()
        if not self.root.is_dir():
            self.skipTest(f"Fixture not reachable at {self.root}")

    def test_csharp_project_emits_three_projects(self) -> None:
        # The fixture ships Web + DAL + Tests; the strategy must mint
        # one ``csproj://`` node for each, regardless of nesting depth.
        result = csharp_project_extract(self.root, {"glob": "**/*.csproj"}, {})
        self.assertIn("csproj://Sample.Web", result.nodes)
        self.assertIn("csproj://Sample.Dal", result.nodes)
        self.assertIn("csproj://Sample.Tests", result.nodes)
        for nid, node in result.nodes.items():
            self.assertEqual(node["type"], "build-target")
            self.assertEqual(node["props"]["source_strategy"], "csharp_project")
            self.assertEqual(node["props"]["confidence"], "definite")

    def test_csharp_project_inherits_directory_build_props(self) -> None:
        # Directory.Build.props in the fixture root declares
        # ``TargetFramework`` / ``LangVersion`` / ``Nullable``; every
        # project that does not override them inherits the values.
        result = csharp_project_extract(self.root, {"glob": "**/*.csproj"}, {})
        for nid in (
            "csproj://Sample.Web",
            "csproj://Sample.Dal",
            "csproj://Sample.Tests",
        ):
            props = result.nodes[nid]["props"]
            self.assertEqual(props["targetframework"], "net8.0")
            self.assertEqual(props["langversion"], "latest")
            self.assertEqual(props["nullable"], "enable")

    def test_csharp_project_emits_depends_on_for_project_references(self) -> None:
        # Sample.Web -> Sample.Dal; Sample.Tests -> Sample.Web + Sample.Dal.
        result = csharp_project_extract(self.root, {"glob": "**/*.csproj"}, {})
        depends_edges = {
            (edge["from"], edge["to"])
            for edge in result.edges
            if edge["type"] == "depends_on"
        }
        self.assertIn(("csproj://Sample.Web", "csproj://Sample.Dal"), depends_edges)
        self.assertIn(("csproj://Sample.Tests", "csproj://Sample.Web"), depends_edges)
        self.assertIn(("csproj://Sample.Tests", "csproj://Sample.Dal"), depends_edges)

    def test_csharp_solution_emits_solution_with_contained_projects(self) -> None:
        # The .sln lists three project entries plus the default build
        # configurations. csharp_solution must turn that into a
        # solution node with one ``contains`` edge per project.
        result = csharp_solution_extract(self.root, {"glob": "**/*.sln"}, {})
        self.assertIn("solution://Sample", result.nodes)
        sln = result.nodes["solution://Sample"]
        self.assertEqual(sln["props"]["project_count"], 3)
        self.assertEqual(
            sln["props"]["configurations"],
            ["Debug|Any CPU", "Release|Any CPU"],
        )
        contains = {
            (edge["from"], edge["to"])
            for edge in result.edges
            if edge["type"] == "contains"
        }
        self.assertEqual(
            contains,
            {
                ("solution://Sample", "csproj://Sample.Web"),
                ("solution://Sample", "csproj://Sample.Dal"),
                ("solution://Sample", "csproj://Sample.Tests"),
            },
        )

    def test_combined_graph_has_no_dangling_solution_edges(self) -> None:
        # The two strategies are independent at runtime; the integration
        # invariant is that every ``contains`` edge the solution mints
        # lands on a ``csproj://`` node that csharp_project also emits.
        project_result = csharp_project_extract(
            self.root, {"glob": "**/*.csproj"}, {},
        )
        solution_result = csharp_solution_extract(
            self.root, {"glob": "**/*.sln"}, {},
        )
        emitted_csproj_ids = set(project_result.nodes.keys())
        for edge in solution_result.edges:
            if edge["type"] != "contains":
                continue
            self.assertIn(
                edge["to"], emitted_csproj_ids,
                f"solution contains edge points at unknown project: {edge['to']!r}",
            )

    def test_every_emitted_edge_has_valid_confidence(self) -> None:
        # ADR 0050 contract: cover the fixture's combined edge set so a
        # silent drop on either strategy fails this test before it
        # reaches the dogfood canary.
        project_result = csharp_project_extract(
            self.root, {"glob": "**/*.csproj"}, {},
        )
        solution_result = csharp_solution_extract(
            self.root, {"glob": "**/*.sln"}, {},
        )
        for edge in (*project_result.edges, *solution_result.edges):
            confidence = edge["props"]["confidence"]
            self.assertIn(confidence, CONFIDENCE_VALUES)


if __name__ == "__main__":
    unittest.main()
