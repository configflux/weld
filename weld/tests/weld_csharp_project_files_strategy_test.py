"""Tests for ``csproj -> contains -> file`` edges (ADR 0056 addendum).

Hosts the file-ownership assertions for the ``csharp_project`` strategy
in their own module so the original
``weld_csharp_project_strategy_test.py`` stays under the repo's 400-line
cap. Tests here exercise every branch of the hybrid resolver added by
the 2026-05-15 addendum:

- SDK-style implicit ``**/*.cs`` glob;
- ``bin/`` / ``obj/`` implicit exclusion;
- explicit ``<Compile Remove>`` subtraction;
- explicit ``<Compile Include>`` pointing outside the project directory;
- non-SDK projects using explicit-Include-only;
- nested-csproj tie-breaking (deepest wins);
- ``<Import Sdk=>`` recognition as SDK-style;
- ADR 0050 confidence stamp on contains edges;
- MSBuild document-order Remove-then-Include re-add;
- determinism across two runs.
"""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from weld.contract import CONFIDENCE_VALUES
from weld.strategies._helpers import StrategyResult
from weld.strategies.csharp_project import extract


class CsharpProjectContainsFileEdgesTest(unittest.TestCase):
    """``csproj -> contains -> file`` edges (ADR 0056 addendum, 2026-05-15)."""

    def _write(self, path: Path, body: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body), encoding="utf-8")

    def _contains_pairs(self, result: StrategyResult) -> set[tuple[str, str]]:
        return {
            (edge["from"], edge["to"])
            for edge in result.edges
            if edge["type"] == "contains"
        }

    def test_sdk_style_implicit_glob_owns_every_cs_file(self) -> None:
        # SDK-style projects compile ``**/*.cs`` under the project
        # directory without needing an explicit ItemGroup.
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write(
                root / "src" / "Sample.Web" / "Sample.Web.csproj",
                """\
                <Project Sdk="Microsoft.NET.Sdk.Web">
                  <PropertyGroup>
                    <TargetFramework>net8.0</TargetFramework>
                  </PropertyGroup>
                </Project>
                """,
            )
            self._write(
                root / "src" / "Sample.Web" / "Program.cs",
                "namespace Sample.Web; public class Program {}\n",
            )
            self._write(
                root / "src" / "Sample.Web" / "Controllers" / "OrdersController.cs",
                "namespace Sample.Web.Controllers; public class OrdersController {}\n",
            )

            result = extract(root, {"glob": "**/*.csproj"}, {})

            self.assertIn(
                ("csproj://Sample.Web", "file:src/Sample.Web/Program"),
                self._contains_pairs(result),
            )
            self.assertIn(
                (
                    "csproj://Sample.Web",
                    "file:src/Sample.Web/Controllers/OrdersController",
                ),
                self._contains_pairs(result),
            )

    def test_implicit_glob_excludes_bin_obj_subtrees(self) -> None:
        # MSBuild's default item excludes hide ``bin/`` and ``obj/`` from
        # the implicit Compile glob; a generated stub there must not get
        # a contains edge unless explicitly Included.
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write(
                root / "src" / "Web" / "Web.csproj",
                """\
                <Project Sdk="Microsoft.NET.Sdk.Web">
                  <PropertyGroup>
                    <TargetFramework>net8.0</TargetFramework>
                  </PropertyGroup>
                </Project>
                """,
            )
            self._write(
                root / "src" / "Web" / "Program.cs",
                "namespace Web; public class Program {}\n",
            )
            self._write(
                root / "src" / "Web" / "obj" / "Generated" / "Stub.cs",
                "namespace Web.Generated; public class Stub {}\n",
            )

            result = extract(root, {"glob": "**/*.csproj"}, {})

            pairs = self._contains_pairs(result)
            self.assertIn(("csproj://Web", "file:src/Web/Program"), pairs)
            self.assertNotIn(
                ("csproj://Web", "file:src/Web/obj/Generated/Stub"),
                pairs,
                "obj/ should be excluded from the implicit SDK glob",
            )

    def test_explicit_compile_remove_subtracts_from_implicit_glob(self) -> None:
        # ``<Compile Remove="Excluded.cs"/>`` must subtract a single file
        # from the otherwise-implicit set.
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write(
                root / "src" / "Dal" / "Dal.csproj",
                """\
                <Project Sdk="Microsoft.NET.Sdk">
                  <PropertyGroup>
                    <TargetFramework>net8.0</TargetFramework>
                  </PropertyGroup>
                  <ItemGroup>
                    <Compile Remove="Excluded.cs" />
                  </ItemGroup>
                </Project>
                """,
            )
            self._write(
                root / "src" / "Dal" / "Kept.cs",
                "namespace Dal; public class Kept {}\n",
            )
            self._write(
                root / "src" / "Dal" / "Excluded.cs",
                "namespace Dal; public class Excluded {}\n",
            )

            result = extract(root, {"glob": "**/*.csproj"}, {})
            pairs = self._contains_pairs(result)

            self.assertIn(("csproj://Dal", "file:src/Dal/Kept"), pairs)
            self.assertNotIn(("csproj://Dal", "file:src/Dal/Excluded"), pairs)

    def test_explicit_compile_include_outside_project_directory(self) -> None:
        # Explicit Include with a ``..\\`` prefix pulls a file from a
        # sibling directory tree into the project.
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write(
                root / "src" / "Web" / "Web.csproj",
                r"""
                <Project Sdk="Microsoft.NET.Sdk.Web">
                  <PropertyGroup>
                    <TargetFramework>net8.0</TargetFramework>
                  </PropertyGroup>
                  <ItemGroup>
                    <Compile Include="..\..\_generated\Stub.cs" />
                  </ItemGroup>
                </Project>
                """,
            )
            self._write(
                root / "src" / "Web" / "Program.cs",
                "namespace Web; public class Program {}\n",
            )
            self._write(
                root / "_generated" / "Stub.cs",
                "namespace Generated; public class Stub {}\n",
            )

            result = extract(root, {"glob": "**/*.csproj"}, {})
            pairs = self._contains_pairs(result)

            self.assertIn(("csproj://Web", "file:_generated/Stub"), pairs)

    def test_non_sdk_project_uses_explicit_includes_only(self) -> None:
        # Legacy non-SDK projects (no ``Sdk=`` attribute, no
        # ``<Import Sdk=>`` child) own *only* the files they list
        # explicitly. Files sitting next to the csproj must not slip in
        # via an implicit glob -- there is no implicit glob for them.
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write(
                root / "Legacy" / "Legacy.csproj",
                r"""
                <Project>
                  <PropertyGroup>
                    <TargetFrameworkVersion>v4.8</TargetFrameworkVersion>
                  </PropertyGroup>
                  <ItemGroup>
                    <Compile Include="Listed.cs" />
                  </ItemGroup>
                </Project>
                """,
            )
            self._write(
                root / "Legacy" / "Listed.cs",
                "public class Listed {}\n",
            )
            self._write(
                root / "Legacy" / "NotListed.cs",
                "public class NotListed {}\n",
            )

            result = extract(root, {"glob": "**/*.csproj"}, {})
            pairs = self._contains_pairs(result)

            self.assertIn(("csproj://Legacy", "file:Legacy/Listed"), pairs)
            self.assertNotIn(("csproj://Legacy", "file:Legacy/NotListed"), pairs)

    def test_nested_csproj_wins_over_outer_project(self) -> None:
        # When projects are co-located the deepest csproj must claim
        # files in its subtree; the outer project cannot poach them.
        # Mirrors a Tests/ csproj nested under its parent project.
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write(
                root / "Outer" / "Outer.csproj",
                """\
                <Project Sdk="Microsoft.NET.Sdk">
                  <PropertyGroup>
                    <TargetFramework>net8.0</TargetFramework>
                  </PropertyGroup>
                </Project>
                """,
            )
            self._write(
                root / "Outer" / "OuterOnly.cs",
                "namespace Outer; public class OuterOnly {}\n",
            )
            self._write(
                root / "Outer" / "Tests" / "Tests.csproj",
                """\
                <Project Sdk="Microsoft.NET.Sdk">
                  <PropertyGroup>
                    <TargetFramework>net8.0</TargetFramework>
                  </PropertyGroup>
                </Project>
                """,
            )
            self._write(
                root / "Outer" / "Tests" / "TestOnly.cs",
                "namespace Outer.Tests; public class TestOnly {}\n",
            )

            result = extract(root, {"glob": "**/*.csproj"}, {})
            pairs = self._contains_pairs(result)

            self.assertIn(
                ("csproj://Outer", "file:Outer/OuterOnly"), pairs,
            )
            self.assertIn(
                ("csproj://Tests", "file:Outer/Tests/TestOnly"), pairs,
            )
            # The deepest-wins rule: Outer.csproj must NOT also claim
            # the nested csproj's file even though it sits inside the
            # outer project's directory tree.
            self.assertNotIn(
                ("csproj://Outer", "file:Outer/Tests/TestOnly"), pairs,
            )

    def test_sdk_style_import_element_is_recognised(self) -> None:
        # The multi-line SDK-import shape (no Sdk= on <Project>, but an
        # <Import Sdk="..." /> child) must still be treated as SDK-style.
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write(
                root / "src" / "Web" / "Web.csproj",
                """\
                <Project>
                  <Import Project="Sdk.props" Sdk="Microsoft.NET.Sdk" />
                  <PropertyGroup>
                    <TargetFramework>net8.0</TargetFramework>
                  </PropertyGroup>
                </Project>
                """,
            )
            self._write(
                root / "src" / "Web" / "Program.cs",
                "namespace Web; public class Program {}\n",
            )

            result = extract(root, {"glob": "**/*.csproj"}, {})
            pairs = self._contains_pairs(result)
            self.assertIn(
                ("csproj://Web", "file:src/Web/Program"), pairs,
                "Import Sdk= child should mark the project as SDK-style",
            )

    def test_contains_edges_carry_definite_confidence(self) -> None:
        # ADR 0050 contract: every emitted edge ships with a
        # CONFIDENCE_VALUES-drawn confidence. The contains edge family
        # joins depends_on under the same rule.
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write(
                root / "src" / "Web" / "Web.csproj",
                """\
                <Project Sdk="Microsoft.NET.Sdk">
                  <PropertyGroup>
                    <TargetFramework>net8.0</TargetFramework>
                  </PropertyGroup>
                </Project>
                """,
            )
            self._write(
                root / "src" / "Web" / "Program.cs",
                "namespace Web; public class Program {}\n",
            )

            result = extract(root, {"glob": "**/*.csproj"}, {})
            for edge in result.edges:
                if edge["type"] != "contains":
                    continue
                self.assertEqual(edge["props"]["source_strategy"], "csharp_project")
                self.assertEqual(edge["props"]["confidence"], "definite")
                self.assertIn(edge["props"]["confidence"], CONFIDENCE_VALUES)

    def test_document_order_remove_then_include_re_adds(self) -> None:
        # MSBuild evaluates Compile directives in document order: a
        # broad ``<Compile Remove="**/*.cs"/>`` followed by a specific
        # ``<Compile Include="Kept.cs"/>`` re-adds Kept.cs to the set.
        # This is the canonical "carve everything out then add back"
        # pattern source generators use.
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write(
                root / "src" / "Web" / "Web.csproj",
                """\
                <Project Sdk="Microsoft.NET.Sdk">
                  <PropertyGroup>
                    <TargetFramework>net8.0</TargetFramework>
                  </PropertyGroup>
                  <ItemGroup>
                    <Compile Remove="**/*.cs" />
                    <Compile Include="Kept.cs" />
                  </ItemGroup>
                </Project>
                """,
            )
            self._write(
                root / "src" / "Web" / "Kept.cs",
                "namespace Web; public class Kept {}\n",
            )
            self._write(
                root / "src" / "Web" / "Dropped.cs",
                "namespace Web; public class Dropped {}\n",
            )

            result = extract(root, {"glob": "**/*.csproj"}, {})
            pairs = self._contains_pairs(result)

            self.assertIn(("csproj://Web", "file:src/Web/Kept"), pairs)
            self.assertNotIn(("csproj://Web", "file:src/Web/Dropped"), pairs)

    def test_contains_edges_are_deterministic(self) -> None:
        # Two consecutive runs must produce byte-identical contains
        # edges so discovery output stays stable (ADR 0064 criterion 4).
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write(
                root / "src" / "Web" / "Web.csproj",
                """\
                <Project Sdk="Microsoft.NET.Sdk">
                  <PropertyGroup>
                    <TargetFramework>net8.0</TargetFramework>
                  </PropertyGroup>
                </Project>
                """,
            )
            for name in ("Zeta", "Alpha", "Mu"):
                self._write(
                    root / "src" / "Web" / f"{name}.cs",
                    f"namespace Web; public class {name} {{}}\n",
                )

            first = extract(root, {"glob": "**/*.csproj"}, {})
            second = extract(root, {"glob": "**/*.csproj"}, {})
            self.assertEqual(first.edges, second.edges)


if __name__ == "__main__":
    unittest.main()
