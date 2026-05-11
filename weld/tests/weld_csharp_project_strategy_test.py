"""Tests for the ``csharp_project`` strategy (ADR 0056 Wave 1)."""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from weld.contract import CONFIDENCE_VALUES
from weld.strategies._helpers import StrategyResult
from weld.strategies.csharp_project import (
    _csproj_id,
    _directory_chain,
    _project_name_from_reference,
    extract,
)


class ProjectNameExtractionTest(unittest.TestCase):
    """Helpers that interpret ``ProjectReference`` Include attributes."""

    def test_windows_relative_path_yields_stem(self) -> None:
        self.assertEqual(
            _project_name_from_reference(r"..\Foo\Foo.csproj"),
            "Foo",
        )

    def test_posix_relative_path_yields_stem(self) -> None:
        self.assertEqual(
            _project_name_from_reference("../Foo/Foo.csproj"),
            "Foo",
        )

    def test_bare_filename_yields_stem(self) -> None:
        self.assertEqual(
            _project_name_from_reference("Foo.csproj"),
            "Foo",
        )

    def test_empty_string_yields_empty(self) -> None:
        self.assertEqual(_project_name_from_reference(""), "")

    def test_no_extension_yields_full_name(self) -> None:
        # Defensive: tools sometimes emit references without the .csproj
        # extension. Keep the bare name as-is rather than guessing.
        self.assertEqual(_project_name_from_reference("Foo"), "Foo")


class CsprojIdTest(unittest.TestCase):
    """ID-format invariants."""

    def test_preserves_casing(self) -> None:
        # The csproj id is consumed by csharp_solution via the same
        # helper shape, so case folding here would silently break
        # solution -> project edges.
        self.assertEqual(_csproj_id("Sample.Web"), "csproj://Sample.Web")


class DirectoryChainTest(unittest.TestCase):
    """``_directory_chain`` walks root-first to project directory."""

    def test_chain_returns_root_then_descent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            nested = root / "src" / "Sample.Web"
            nested.mkdir(parents=True)
            chain = _directory_chain(nested, root)
            self.assertEqual(chain[0].resolve(), root.resolve())
            self.assertEqual(chain[-1].resolve(), nested.resolve())
            self.assertEqual(len(chain), 3)

    def test_chain_outside_root_falls_back_to_start_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "root"
            elsewhere = Path(tmpdir) / "elsewhere"
            root.mkdir()
            elsewhere.mkdir()
            chain = _directory_chain(elsewhere, root)
            self.assertEqual(chain, [elsewhere])


class CsharpProjectExtractTest(unittest.TestCase):
    """Integration tests for the ``extract()`` entrypoint."""

    def _write_project(self, path: Path, body: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body), encoding="utf-8")

    def test_emits_project_node_with_definite_confidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_project(
                root / "src" / "Sample.Web" / "Sample.Web.csproj",
                """\
                <Project Sdk="Microsoft.NET.Sdk.Web">
                  <PropertyGroup>
                    <TargetFramework>net8.0</TargetFramework>
                    <RootNamespace>Sample.Web</RootNamespace>
                  </PropertyGroup>
                </Project>
                """,
            )
            result = extract(root, {"glob": "**/*.csproj"}, {})

            self.assertIsInstance(result, StrategyResult)
            nid = "csproj://Sample.Web"
            self.assertIn(nid, result.nodes)
            node = result.nodes[nid]
            self.assertEqual(node["type"], "build-target")
            self.assertEqual(node["props"]["source_strategy"], "csharp_project")
            self.assertEqual(node["props"]["confidence"], "definite")
            self.assertEqual(node["props"]["targetframework"], "net8.0")
            self.assertEqual(node["props"]["rootnamespace"], "Sample.Web")
            self.assertIn("build", node["props"]["roles"])

    def test_project_reference_yields_depends_on_edge(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_project(
                root / "src" / "Sample.Web" / "Sample.Web.csproj",
                r"""
                <Project Sdk="Microsoft.NET.Sdk.Web">
                  <ItemGroup>
                    <ProjectReference Include="..\Sample.Dal\Sample.Dal.csproj" />
                  </ItemGroup>
                </Project>
                """,
            )
            self._write_project(
                root / "src" / "Sample.Dal" / "Sample.Dal.csproj",
                """\
                <Project Sdk="Microsoft.NET.Sdk">
                  <PropertyGroup>
                    <TargetFramework>net8.0</TargetFramework>
                  </PropertyGroup>
                </Project>
                """,
            )

            result = extract(root, {"glob": "**/*.csproj"}, {})

            depends_edges = [
                edge for edge in result.edges if edge["type"] == "depends_on"
            ]
            self.assertEqual(len(depends_edges), 1)
            edge = depends_edges[0]
            self.assertEqual(edge["from"], "csproj://Sample.Web")
            self.assertEqual(edge["to"], "csproj://Sample.Dal")
            self.assertEqual(edge["props"]["source_strategy"], "csharp_project")
            self.assertEqual(edge["props"]["confidence"], "definite")

    def test_directory_build_props_are_inherited(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_project(
                root / "Directory.Build.props",
                """\
                <Project>
                  <PropertyGroup>
                    <TargetFramework>net8.0</TargetFramework>
                    <Nullable>enable</Nullable>
                  </PropertyGroup>
                </Project>
                """,
            )
            self._write_project(
                root / "src" / "Sample.Web" / "Sample.Web.csproj",
                """\
                <Project Sdk="Microsoft.NET.Sdk.Web">
                  <PropertyGroup>
                    <RootNamespace>Sample.Web</RootNamespace>
                  </PropertyGroup>
                </Project>
                """,
            )

            result = extract(root, {"glob": "**/*.csproj"}, {})
            props = result.nodes["csproj://Sample.Web"]["props"]
            self.assertEqual(props["targetframework"], "net8.0")
            self.assertEqual(props["nullable"], "enable")
            self.assertEqual(props["rootnamespace"], "Sample.Web")

    def test_directory_build_targets_override_props(self) -> None:
        # Directory.Build.targets runs after .props per MSBuild
        # evaluation order; we mirror that "last write wins" semantic.
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_project(
                root / "Directory.Build.props",
                """\
                <Project>
                  <PropertyGroup>
                    <TargetFramework>net7.0</TargetFramework>
                  </PropertyGroup>
                </Project>
                """,
            )
            self._write_project(
                root / "Directory.Build.targets",
                """\
                <Project>
                  <PropertyGroup>
                    <TargetFramework>net8.0</TargetFramework>
                  </PropertyGroup>
                </Project>
                """,
            )
            self._write_project(
                root / "Sample.Web.csproj",
                """\
                <Project Sdk="Microsoft.NET.Sdk.Web">
                </Project>
                """,
            )
            result = extract(root, {"glob": "**/*.csproj"}, {})
            props = result.nodes["csproj://Sample.Web"]["props"]
            self.assertEqual(props["targetframework"], "net8.0")

    def test_csproj_own_property_overrides_directory_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_project(
                root / "Directory.Build.props",
                """\
                <Project>
                  <PropertyGroup>
                    <TargetFramework>net7.0</TargetFramework>
                  </PropertyGroup>
                </Project>
                """,
            )
            self._write_project(
                root / "Sample.Web.csproj",
                """\
                <Project Sdk="Microsoft.NET.Sdk.Web">
                  <PropertyGroup>
                    <TargetFramework>net8.0</TargetFramework>
                  </PropertyGroup>
                </Project>
                """,
            )
            result = extract(root, {"glob": "**/*.csproj"}, {})
            props = result.nodes["csproj://Sample.Web"]["props"]
            self.assertEqual(props["targetframework"], "net8.0")

    def test_malformed_xml_does_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_project(
                root / "Sample.Web.csproj",
                """\
                <Project Sdk="Microsoft.NET.Sdk.Web">
                  <PropertyGroup>
                    <TargetFramework>net8.0
                """,
            )
            result = extract(root, {"glob": "**/*.csproj"}, {})
            # Malformed XML yields an empty project: node still emitted,
            # but with no inherited or own properties beyond defaults.
            node = result.nodes["csproj://Sample.Web"]
            self.assertNotIn("targetframework", node["props"])

    def test_empty_directory_returns_empty_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            result = extract(root, {"glob": "**/*.csproj"}, {})
            self.assertEqual(result.nodes, {})
            self.assertEqual(result.edges, [])

    def test_every_emitted_edge_has_definite_confidence(self) -> None:
        # ADR 0050 mandates that every edge a strategy emits carries a
        # confidence value drawn from CONFIDENCE_VALUES. We assert this
        # at the strategy level so a future refactor cannot drop the
        # stamp on one branch and pass the dogfood canary by accident.
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_project(
                root / "src" / "Sample.Web" / "Sample.Web.csproj",
                r"""
                <Project Sdk="Microsoft.NET.Sdk.Web">
                  <ItemGroup>
                    <ProjectReference Include="..\Sample.Dal\Sample.Dal.csproj" />
                  </ItemGroup>
                </Project>
                """,
            )
            result = extract(root, {"glob": "**/*.csproj"}, {})
            for edge in result.edges:
                self.assertEqual(edge["props"]["confidence"], "definite")
                self.assertIn(edge["props"]["confidence"], CONFIDENCE_VALUES)

    def test_deterministic_output_across_runs(self) -> None:
        # Sorting the reference list before emit is the contract: two
        # consecutive runs on the same tree must produce byte-identical
        # JSON.
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write_project(
                root / "src" / "Sample.Web" / "Sample.Web.csproj",
                r"""
                <Project Sdk="Microsoft.NET.Sdk.Web">
                  <ItemGroup>
                    <ProjectReference Include="..\Z\Z.csproj" />
                    <ProjectReference Include="..\A\A.csproj" />
                  </ItemGroup>
                </Project>
                """,
            )
            first = extract(root, {"glob": "**/*.csproj"}, {})
            second = extract(root, {"glob": "**/*.csproj"}, {})
            self.assertEqual(first.edges, second.edges)
            # ProjectReference order in XML is "Z then A"; emit order
            # must be "A then Z" after the deterministic sort.
            tos = [edge["to"] for edge in first.edges]
            self.assertEqual(tos, ["csproj://A", "csproj://Z"])


if __name__ == "__main__":
    unittest.main()
