"""Tests for the ``csharp_solution`` strategy (ADR 0056 Wave 1)."""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from weld.contract import CONFIDENCE_VALUES
from weld.strategies._helpers import StrategyResult
from weld.strategies.csharp_solution import (
    _parse_solution,
    _project_name_from_path,
    _solution_id,
    extract,
)


_MINIMAL_SLN = textwrap.dedent("""\
    Microsoft Visual Studio Solution File, Format Version 12.00
    # Visual Studio Version 17
    VisualStudioVersion = 17.0.31903.59
    MinimumVisualStudioVersion = 10.0.40219.1
    Project("{FAE04EC0-301F-11D3-BF4B-00C04F79EFBC}") = "Sample.Web", "src\\Sample.Web\\Sample.Web.csproj", "{11111111-1111-1111-1111-111111111111}"
    EndProject
    Project("{FAE04EC0-301F-11D3-BF4B-00C04F79EFBC}") = "Sample.Dal", "src\\Sample.Dal\\Sample.Dal.csproj", "{22222222-2222-2222-2222-222222222222}"
    EndProject
    Global
    \tGlobalSection(SolutionConfigurationPlatforms) = preSolution
    \t\tDebug|Any CPU = Debug|Any CPU
    \t\tRelease|Any CPU = Release|Any CPU
    \tEndGlobalSection
    EndGlobal
""")


class ProjectPathTest(unittest.TestCase):
    """Path-to-stem extraction handles Windows and POSIX shapes."""

    def test_windows_backslash_path(self) -> None:
        self.assertEqual(
            _project_name_from_path(r"src\Sample.Web\Sample.Web.csproj"),
            "Sample.Web",
        )

    def test_posix_forward_slash_path(self) -> None:
        self.assertEqual(
            _project_name_from_path("src/Sample.Web/Sample.Web.csproj"),
            "Sample.Web",
        )

    def test_non_csproj_entry_is_filtered(self) -> None:
        # Solution folders (special GUID) have a name path with no
        # extension and must not produce a csproj edge.
        self.assertEqual(_project_name_from_path("Solution Items"), "")
        self.assertEqual(
            _project_name_from_path(r"src\Other\Other.vbproj"), "",
        )
        self.assertEqual(
            _project_name_from_path(r"src\Other\Other.fsproj"), "",
        )

    def test_empty_path_yields_empty(self) -> None:
        self.assertEqual(_project_name_from_path(""), "")


class SolutionIdTest(unittest.TestCase):
    """Solution IDs preserve the original casing of the .sln stem."""

    def test_id_shape(self) -> None:
        self.assertEqual(_solution_id("Sample"), "solution://Sample")


class ParseSolutionTest(unittest.TestCase):
    """Pure parser unit tests over the .sln body string."""

    def test_extracts_projects_and_configurations(self) -> None:
        projects, configurations = _parse_solution(_MINIMAL_SLN)
        self.assertEqual(projects, ["Sample.Web", "Sample.Dal"])
        self.assertEqual(
            sorted(configurations),
            ["Debug|Any CPU", "Release|Any CPU"],
        )

    def test_ignores_non_csproj_projects(self) -> None:
        body = textwrap.dedent("""\
            Microsoft Visual Studio Solution File, Format Version 12.00
            Project("{2150E333-8FDC-42A3-9474-1A3956D46DE8}") = "Solution Items", "Solution Items", "{ABC}"
            EndProject
            Project("{FAE04EC0-301F-11D3-BF4B-00C04F79EFBC}") = "Sample.Web", "src/Sample.Web/Sample.Web.csproj", "{11111111-1111-1111-1111-111111111111}"
            EndProject
        """)
        projects, _ = _parse_solution(body)
        self.assertEqual(projects, ["Sample.Web"])

    def test_empty_file_yields_empty_lists(self) -> None:
        projects, configurations = _parse_solution("")
        self.assertEqual(projects, [])
        self.assertEqual(configurations, [])

    def test_handles_missing_global_section(self) -> None:
        body = textwrap.dedent("""\
            Microsoft Visual Studio Solution File, Format Version 12.00
            Project("{FAE04EC0-301F-11D3-BF4B-00C04F79EFBC}") = "Sample.Web", "Sample.Web.csproj", "{11111111-1111-1111-1111-111111111111}"
            EndProject
        """)
        projects, configurations = _parse_solution(body)
        self.assertEqual(projects, ["Sample.Web"])
        self.assertEqual(configurations, [])


class CsharpSolutionExtractTest(unittest.TestCase):
    """Integration tests for the ``extract()`` entrypoint."""

    def _write(self, path: Path, body: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    def test_emits_solution_node_with_definite_confidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write(root / "Sample.sln", _MINIMAL_SLN)
            result = extract(root, {"glob": "**/*.sln"}, {})

            self.assertIsInstance(result, StrategyResult)
            sln_id = "solution://Sample"
            self.assertIn(sln_id, result.nodes)
            sln = result.nodes[sln_id]
            self.assertEqual(sln["type"], "build-target")
            self.assertEqual(sln["props"]["source_strategy"], "csharp_solution")
            self.assertEqual(sln["props"]["confidence"], "definite")
            self.assertEqual(sln["props"]["project_count"], 2)
            self.assertEqual(
                sln["props"]["configurations"],
                ["Debug|Any CPU", "Release|Any CPU"],
            )

    def test_emits_contains_edges_to_each_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write(root / "Sample.sln", _MINIMAL_SLN)
            result = extract(root, {"glob": "**/*.sln"}, {})

            edges_by_to = {edge["to"] for edge in result.edges
                           if edge["type"] == "contains"}
            self.assertEqual(
                edges_by_to,
                {"csproj://Sample.Web", "csproj://Sample.Dal"},
            )

    def test_every_emitted_edge_has_definite_confidence(self) -> None:
        # ADR 0050 contract: every emitted edge must carry a value
        # drawn from CONFIDENCE_VALUES. Asserted here directly so the
        # dogfood canary does not have to.
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write(root / "Sample.sln", _MINIMAL_SLN)
            result = extract(root, {"glob": "**/*.sln"}, {})

            for edge in result.edges:
                self.assertEqual(edge["props"]["confidence"], "definite")
                self.assertIn(edge["props"]["confidence"], CONFIDENCE_VALUES)
                self.assertEqual(
                    edge["props"]["source_strategy"], "csharp_solution",
                )

    def test_handles_utf8_bom(self) -> None:
        # Visual Studio writes .sln files with a UTF-8 BOM. Decoding
        # via utf-8 strict would leave the BOM in the first line and
        # break the header match (which we no longer assert against
        # but other consumers might). utf-8-sig handles both BOM and
        # non-BOM inputs.
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            sln_path = root / "Sample.sln"
            sln_path.write_bytes(b"\xef\xbb\xbf" + _MINIMAL_SLN.encode("utf-8"))
            result = extract(root, {"glob": "**/*.sln"}, {})
            self.assertIn("solution://Sample", result.nodes)

    def test_deterministic_edge_order(self) -> None:
        # Solution lists "Sample.Web" first, then "Sample.Dal"; the
        # emitted contains edges must be sorted alphabetically so two
        # consecutive discoveries produce identical JSON.
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write(root / "Sample.sln", _MINIMAL_SLN)
            first = extract(root, {"glob": "**/*.sln"}, {})
            second = extract(root, {"glob": "**/*.sln"}, {})
            self.assertEqual(first.edges, second.edges)
            tos = [edge["to"] for edge in first.edges]
            self.assertEqual(tos, ["csproj://Sample.Dal", "csproj://Sample.Web"])

    def test_empty_directory_returns_empty_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            result = extract(root, {"glob": "**/*.sln"}, {})
            self.assertEqual(result.nodes, {})
            self.assertEqual(result.edges, [])


if __name__ == "__main__":
    unittest.main()
