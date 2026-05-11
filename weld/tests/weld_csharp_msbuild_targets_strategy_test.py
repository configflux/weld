"""Tests for the ``csharp_msbuild_targets`` strategy (ADR 0056 Wave 3).

Covers two surfaces:

1. ``<Target Name="...">`` declarations -- each becomes a
   ``build-target://<csproj-stem>:<target-name>`` node and ordering
   attributes (``BeforeTargets`` / ``AfterTargets``) become
   ``depends_on`` edges.
2. ``<ItemGroup>`` declarations -- the per-target item list is summed
   into a ``itemgroup_count`` prop on the target node so consumers can
   detect target-scoped item declarations without re-parsing the XML.

All emitted edges must carry ``confidence="definite"`` per ADR 0050:
the XML is deterministic.
"""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from weld.contract import CONFIDENCE_VALUES, VALID_EDGE_TYPES, VALID_NODE_TYPES
from weld.strategies._helpers import StrategyResult
from weld.strategies.csharp_msbuild_targets import (
    _msbuild_target_id,
    _split_targets_attribute,
    extract,
)


class SplitTargetsAttributeTest(unittest.TestCase):
    """The ``BeforeTargets`` / ``AfterTargets`` attribute is a CSV list."""

    def test_single_target_yields_one_name(self) -> None:
        self.assertEqual(
            _split_targets_attribute("Build"),
            ["Build"],
        )

    def test_multiple_targets_split_on_semicolon(self) -> None:
        self.assertEqual(
            _split_targets_attribute("Build;PrepareForBuild"),
            ["Build", "PrepareForBuild"],
        )

    def test_whitespace_around_separators_trimmed(self) -> None:
        self.assertEqual(
            _split_targets_attribute(" Build ; PrepareForBuild "),
            ["Build", "PrepareForBuild"],
        )

    def test_empty_string_yields_empty_list(self) -> None:
        self.assertEqual(_split_targets_attribute(""), [])

    def test_empty_entries_are_filtered_out(self) -> None:
        # Defensive: ``Build;;Pack`` is malformed but tolerated. Trailing
        # / leading semicolons are common in hand-edited project files.
        self.assertEqual(
            _split_targets_attribute("Build;;Pack;"),
            ["Build", "Pack"],
        )


class MsbuildTargetIdTest(unittest.TestCase):
    """Node-id format invariants."""

    def test_id_combines_csproj_stem_and_target_name(self) -> None:
        self.assertEqual(
            _msbuild_target_id("Sample.Web", "CustomBuild"),
            "build-target://Sample.Web:CustomBuild",
        )

    def test_id_preserves_casing(self) -> None:
        # csproj IDs preserve case; MSBuild target IDs follow suit so
        # downstream joins on ``build-target://<stem>`` retain shape.
        self.assertEqual(
            _msbuild_target_id("MixedCase.Web", "PreBuild"),
            "build-target://MixedCase.Web:PreBuild",
        )


class CsharpMsbuildTargetsExtractTest(unittest.TestCase):
    """Integration tests for the ``extract()`` entrypoint."""

    def _write(self, path: Path, body: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body), encoding="utf-8")

    def test_emits_target_node_with_definite_confidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write(
                root / "Sample.Web.csproj",
                """\
                <Project Sdk="Microsoft.NET.Sdk.Web">
                  <Target Name="CustomBuild">
                    <Message Text="Hello from CustomBuild" />
                  </Target>
                </Project>
                """,
            )

            result = extract(root, {"glob": "**/*.csproj"}, {})

            self.assertIsInstance(result, StrategyResult)
            target_id = "build-target://Sample.Web:CustomBuild"
            self.assertIn(target_id, result.nodes)
            node = result.nodes[target_id]
            self.assertEqual(node["type"], "build-target")
            self.assertIn(node["type"], VALID_NODE_TYPES)
            self.assertEqual(node["label"], "Sample.Web:CustomBuild")
            self.assertEqual(
                node["props"]["source_strategy"], "csharp_msbuild_targets",
            )
            self.assertEqual(node["props"]["confidence"], "definite")
            self.assertEqual(node["props"]["target_name"], "CustomBuild")
            self.assertIn("build", node["props"]["roles"])

    def test_before_targets_yields_depends_on_edge(self) -> None:
        # ``BeforeTargets="Build"`` means "this target runs before
        # Build" -- i.e. Build depends on this target's completion.
        # Strategy encodes it as ``custom -> depends_on -> Build`` so
        # the ordering is explicit: custom is upstream of Build.
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write(
                root / "Sample.Web.csproj",
                """\
                <Project Sdk="Microsoft.NET.Sdk.Web">
                  <Target Name="MyPreBuild" BeforeTargets="Build">
                    <Message Text="pre" />
                  </Target>
                </Project>
                """,
            )

            result = extract(root, {"glob": "**/*.csproj"}, {})

            depends = [
                edge for edge in result.edges if edge["type"] == "depends_on"
            ]
            self.assertEqual(len(depends), 1)
            edge = depends[0]
            self.assertEqual(edge["from"], "build-target://Sample.Web:MyPreBuild")
            self.assertEqual(edge["to"], "build-target://Sample.Web:Build")
            self.assertEqual(
                edge["props"]["source_strategy"], "csharp_msbuild_targets",
            )
            self.assertEqual(edge["props"]["confidence"], "definite")
            self.assertEqual(edge["props"]["ordering"], "before")

    def test_after_targets_yields_depends_on_edge(self) -> None:
        # ``AfterTargets="Build"`` means "this target runs after
        # Build" -- i.e. this target depends on Build's completion.
        # Strategy encodes it as ``custom -> depends_on -> Build``.
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write(
                root / "Sample.Web.csproj",
                """\
                <Project Sdk="Microsoft.NET.Sdk.Web">
                  <Target Name="MyPostBuild" AfterTargets="Build">
                    <Message Text="post" />
                  </Target>
                </Project>
                """,
            )

            result = extract(root, {"glob": "**/*.csproj"}, {})

            depends = [
                edge for edge in result.edges if edge["type"] == "depends_on"
            ]
            self.assertEqual(len(depends), 1)
            edge = depends[0]
            self.assertEqual(
                edge["from"], "build-target://Sample.Web:MyPostBuild",
            )
            self.assertEqual(edge["to"], "build-target://Sample.Web:Build")
            self.assertEqual(edge["props"]["ordering"], "after")

    def test_multiple_targets_split_into_distinct_edges(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write(
                root / "Sample.Web.csproj",
                """\
                <Project Sdk="Microsoft.NET.Sdk.Web">
                  <Target Name="Pre"
                          BeforeTargets="Build;Pack"
                          AfterTargets="Restore">
                    <Message Text="x" />
                  </Target>
                </Project>
                """,
            )

            result = extract(root, {"glob": "**/*.csproj"}, {})

            depends = [
                edge for edge in result.edges if edge["type"] == "depends_on"
            ]
            self.assertEqual(len(depends), 3)
            tos = sorted(edge["to"] for edge in depends)
            self.assertEqual(
                tos,
                [
                    "build-target://Sample.Web:Build",
                    "build-target://Sample.Web:Pack",
                    "build-target://Sample.Web:Restore",
                ],
            )

    def test_itemgroup_count_recorded_on_target_node(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write(
                root / "Sample.Web.csproj",
                """\
                <Project Sdk="Microsoft.NET.Sdk.Web">
                  <Target Name="GenerateAssets">
                    <ItemGroup>
                      <Asset Include="logo.png" />
                      <Asset Include="logo.svg" />
                    </ItemGroup>
                  </Target>
                </Project>
                """,
            )

            result = extract(root, {"glob": "**/*.csproj"}, {})
            target = result.nodes["build-target://Sample.Web:GenerateAssets"]
            self.assertEqual(target["props"]["itemgroup_count"], 1)
            self.assertEqual(target["props"]["item_count"], 2)

    def test_props_and_targets_files_are_parsed(self) -> None:
        # ``Directory.Build.props`` and ``Directory.Build.targets`` are
        # MSBuild's customisation hooks and may carry custom ``<Target>``
        # definitions inherited by every project. The strategy reads
        # them in addition to ``*.csproj`` when the glob covers them.
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write(
                root / "Directory.Build.targets",
                """\
                <Project>
                  <Target Name="SharedPrep" BeforeTargets="Build">
                    <Message Text="shared" />
                  </Target>
                </Project>
                """,
            )

            result = extract(
                root,
                {"glob": "**/*.{csproj,props,targets}"},
                {},
            )

            target_id = "build-target://Directory.Build:SharedPrep"
            self.assertIn(target_id, result.nodes)

    def test_no_targets_yields_no_nodes(self) -> None:
        # A project with PropertyGroup/ItemGroup but no <Target>
        # declarations contributes nothing to the build-target graph.
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write(
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
            self.assertEqual(result.nodes, {})
            self.assertEqual(result.edges, [])

    def test_unnamed_target_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write(
                root / "Sample.Web.csproj",
                """\
                <Project Sdk="Microsoft.NET.Sdk.Web">
                  <Target>
                    <Message Text="nameless" />
                  </Target>
                </Project>
                """,
            )
            result = extract(root, {"glob": "**/*.csproj"}, {})
            self.assertEqual(result.nodes, {})

    def test_malformed_xml_does_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write(
                root / "Sample.Web.csproj",
                """\
                <Project Sdk="Microsoft.NET.Sdk.Web">
                  <Target Name="Bad
                """,
            )
            result = extract(root, {"glob": "**/*.csproj"}, {})
            self.assertEqual(result.nodes, {})
            self.assertEqual(result.edges, [])

    def test_empty_directory_returns_empty_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            result = extract(root, {"glob": "**/*.csproj"}, {})
            self.assertEqual(result.nodes, {})
            self.assertEqual(result.edges, [])

    def test_every_emitted_edge_has_definite_confidence(self) -> None:
        # ADR 0050 invariant: every edge ships with a CONFIDENCE_VALUES
        # value. csharp_msbuild_targets parses XML, so every edge is
        # ``definite``.
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write(
                root / "Sample.Web.csproj",
                """\
                <Project Sdk="Microsoft.NET.Sdk.Web">
                  <Target Name="Pre" BeforeTargets="Build">
                    <Message Text="x" />
                  </Target>
                  <Target Name="Post" AfterTargets="Build">
                    <Message Text="y" />
                  </Target>
                </Project>
                """,
            )
            result = extract(root, {"glob": "**/*.csproj"}, {})
            self.assertTrue(result.edges)
            for edge in result.edges:
                self.assertEqual(edge["props"]["confidence"], "definite")
                self.assertIn(edge["props"]["confidence"], CONFIDENCE_VALUES)
                self.assertIn(edge["type"], VALID_EDGE_TYPES)

    def test_deterministic_output_across_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write(
                root / "Sample.Web.csproj",
                """\
                <Project Sdk="Microsoft.NET.Sdk.Web">
                  <Target Name="Pre" BeforeTargets="Z;A">
                    <Message Text="x" />
                  </Target>
                </Project>
                """,
            )
            first = extract(root, {"glob": "**/*.csproj"}, {})
            second = extract(root, {"glob": "**/*.csproj"}, {})
            self.assertEqual(first.edges, second.edges)
            # Edges are sorted by destination so "Z;A" emits as
            # A then Z, not Z then A.
            tos = [edge["to"] for edge in first.edges]
            self.assertEqual(
                tos,
                [
                    "build-target://Sample.Web:A",
                    "build-target://Sample.Web:Z",
                ],
            )


if __name__ == "__main__":
    unittest.main()
