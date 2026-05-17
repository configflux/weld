"""Unit tests for the C# init detector and source-entry wiring.

Covers :mod:`weld._init_csharp`: the detector flag map fed by
``wd init`` and the YAML source-entry generator that consumes it.
Includes the acceptance check that ``wd init`` on the
``csharp_project`` fixture produces a discover.yaml wiring every Wave
1-3 C# strategy and lists them in the header, plus the negative case
that non-C# fixtures do not get the C# stack wired.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld._init_csharp import (  # noqa: E402
    csharp_source_entries,
    detect_csharp_artifacts,
)
from weld._yaml import parse_yaml  # noqa: E402
from weld.init import init as init_run  # noqa: E402
from weld.init_detect import scan_files  # noqa: E402

_FIXTURES = Path(__file__).resolve().parent / "fixtures"

_EXPECTED_FLAG_KEYS = {
    "has_sln",
    "has_csproj",
    "has_directory_build",
    "has_aspnet",
    "has_efcore",
    "has_test_project",
}


class DetectCsharpArtifactsContractTest(unittest.TestCase):
    """The detector returns the exact flag set documented in the spec."""

    def test_returns_documented_flag_keys(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            flags = detect_csharp_artifacts(scan_files(Path(td)))
        self.assertEqual(set(flags.keys()), _EXPECTED_FLAG_KEYS)
        for v in flags.values():
            self.assertIsInstance(v, bool)

    def test_empty_repo_returns_all_false(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            flags = detect_csharp_artifacts(scan_files(Path(td)))
        for k, v in flags.items():
            self.assertFalse(v, f"empty repo should not fire {k}")


class DetectCsharpArtifactsPerFlagTest(unittest.TestCase):
    """Each flag fires on its dedicated signal and nothing else."""

    def test_has_sln_fires_on_dotnet_solution_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "App.sln").write_text(
                "Microsoft Visual Studio Solution File, Format Version 12.00\n",
            )
            flags = detect_csharp_artifacts(scan_files(root))
        self.assertTrue(flags["has_sln"])
        self.assertFalse(flags["has_csproj"])

    def test_has_csproj_fires_on_minimal_csproj(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "Lib.csproj").write_text(
                "<Project Sdk=\"Microsoft.NET.Sdk\"></Project>\n",
            )
            flags = detect_csharp_artifacts(scan_files(root))
        self.assertTrue(flags["has_csproj"])
        self.assertFalse(flags["has_sln"])
        self.assertFalse(flags["has_aspnet"])
        self.assertFalse(flags["has_efcore"])
        self.assertFalse(flags["has_test_project"])

    def test_has_directory_build_fires_on_props(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "Directory.Build.props").write_text(
                "<Project></Project>\n",
            )
            flags = detect_csharp_artifacts(scan_files(root))
        self.assertTrue(flags["has_directory_build"])

    def test_has_directory_build_fires_on_targets(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "Directory.Build.targets").write_text(
                "<Project></Project>\n",
            )
            flags = detect_csharp_artifacts(scan_files(root))
        self.assertTrue(flags["has_directory_build"])

    def test_has_aspnet_fires_on_aspnetcore_package_reference(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "Api.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk">'
                '<ItemGroup><PackageReference Include="Microsoft.AspNetCore.OpenApi"'
                ' Version="8.0.0" /></ItemGroup></Project>\n',
            )
            flags = detect_csharp_artifacts(scan_files(root))
        self.assertTrue(flags["has_aspnet"])

    def test_has_aspnet_fires_on_web_sdk_attribute(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "Web.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk.Web"></Project>\n',
            )
            flags = detect_csharp_artifacts(scan_files(root))
        self.assertTrue(flags["has_aspnet"])

    def test_has_aspnet_fires_on_controllers_directory(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ctrl = root / "src" / "Controllers"
            ctrl.mkdir(parents=True)
            (ctrl / "HomeController.cs").write_text("class C {}\n")
            flags = detect_csharp_artifacts(scan_files(root))
        self.assertTrue(flags["has_aspnet"])

    def test_has_efcore_fires_on_entity_framework_package(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "Dal.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk">'
                '<ItemGroup><PackageReference Include='
                '"Microsoft.EntityFrameworkCore" Version="8.0.0"/>'
                "</ItemGroup></Project>\n",
            )
            flags = detect_csharp_artifacts(scan_files(root))
        self.assertTrue(flags["has_efcore"])
        self.assertFalse(flags["has_aspnet"])

    def test_has_test_project_fires_on_xunit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "Tests.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk">'
                '<ItemGroup><PackageReference Include="xunit" Version="2.6.0"/>'
                "</ItemGroup></Project>\n",
            )
            flags = detect_csharp_artifacts(scan_files(root))
        self.assertTrue(flags["has_test_project"])

    def test_has_test_project_fires_on_nunit(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "Tests.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk">'
                '<ItemGroup><PackageReference Include="nunit" Version="3.13.0"/>'
                "</ItemGroup></Project>\n",
            )
            flags = detect_csharp_artifacts(scan_files(root))
        self.assertTrue(flags["has_test_project"])

    def test_has_test_project_fires_on_mstest(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "Tests.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk">'
                '<ItemGroup><PackageReference Include="MSTest.TestFramework"'
                ' Version="3.0.0"/></ItemGroup></Project>\n',
            )
            flags = detect_csharp_artifacts(scan_files(root))
        self.assertTrue(flags["has_test_project"])

    def test_pure_library_does_not_fire_framework_flags(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "Lib.csproj").write_text(
                '<Project Sdk="Microsoft.NET.Sdk"></Project>\n',
            )
            flags = detect_csharp_artifacts(scan_files(root))
        self.assertTrue(flags["has_csproj"])
        self.assertFalse(flags["has_aspnet"])
        self.assertFalse(flags["has_efcore"])
        self.assertFalse(flags["has_test_project"])


class DetectCsharpArtifactsOnFixtureTest(unittest.TestCase):
    """The repository's csharp_project fixture fires every flag."""

    def test_all_six_flags_fire_on_reference_fixture(self) -> None:
        fixture = _FIXTURES / "csharp_project"
        if not fixture.is_dir():
            self.skipTest(f"Fixture not present at {fixture}")
        flags = detect_csharp_artifacts(scan_files(fixture))
        for key in _EXPECTED_FLAG_KEYS:
            self.assertTrue(flags[key], f"fixture should fire {key}: {flags}")


class CsharpSourceEntriesTest(unittest.TestCase):
    """Source-entry generator gates strategies on the flag dict."""

    def test_only_csharp_package_when_no_other_flags_fire(self) -> None:
        """ADR 0060: ``csharp_package`` always fires when the function is
        invoked (i.e. ``.cs`` files were detected in the workspace), even
        if no .sln / .csproj / framework markers are present. The
        Layer 3 ``file-anchor-symmetry`` violation exists regardless of
        project structure, so the namespace anchor must always be wired."""
        flags = {k: False for k in _EXPECTED_FLAG_KEYS}
        entries = csharp_source_entries(flags)
        joined = "\n".join(entries)
        self.assertIn("strategy: csharp_package", joined)
        self.assertNotIn("strategy: csharp_solution", joined)
        self.assertNotIn("strategy: csharp_project", joined)
        self.assertNotIn("strategy: csharp_msbuild_targets", joined)

    def test_sln_flag_emits_csharp_solution_and_package(self) -> None:
        flags = {k: False for k in _EXPECTED_FLAG_KEYS}
        flags["has_sln"] = True
        entries = csharp_source_entries(flags)
        joined = "\n".join(entries)
        self.assertIn("strategy: csharp_solution", joined)
        # ADR 0060: csharp_package always fires regardless of other flags.
        self.assertIn("strategy: csharp_package", joined)
        self.assertNotIn("strategy: csharp_project", joined)
        self.assertNotIn("strategy: csharp_msbuild_targets", joined)

    def test_csproj_flag_emits_project_and_msbuild_targets(self) -> None:
        flags = {k: False for k in _EXPECTED_FLAG_KEYS}
        flags["has_csproj"] = True
        joined = "\n".join(csharp_source_entries(flags))
        self.assertIn("strategy: csharp_project", joined)
        self.assertIn("strategy: csharp_msbuild_targets", joined)

    def test_aspnet_flag_emits_route_strategy(self) -> None:
        flags = {k: False for k in _EXPECTED_FLAG_KEYS}
        flags["has_aspnet"] = True
        joined = "\n".join(csharp_source_entries(flags))
        self.assertIn("strategy: csharp_aspnet_routes", joined)

    def test_efcore_flag_emits_entity_strategy(self) -> None:
        flags = {k: False for k in _EXPECTED_FLAG_KEYS}
        flags["has_efcore"] = True
        joined = "\n".join(csharp_source_entries(flags))
        self.assertIn("strategy: csharp_efcore", joined)

    def test_test_project_flag_emits_test_framework_strategy(self) -> None:
        flags = {k: False for k in _EXPECTED_FLAG_KEYS}
        flags["has_test_project"] = True
        joined = "\n".join(csharp_source_entries(flags))
        self.assertIn("strategy: csharp_test_framework", joined)

    def test_full_flag_set_emits_all_strategies_in_order(self) -> None:
        flags = {k: True for k in _EXPECTED_FLAG_KEYS}
        joined = "\n".join(csharp_source_entries(flags))
        order = [
            "csharp_solution",
            "csharp_project",
            "csharp_msbuild_targets",
            "csharp_test_framework",
            "csharp_aspnet_routes",
            "csharp_efcore",
            # ADR 0060: csharp_package emitted last so the namespace
            # anchor follows the framework- and project-specific entries.
            "csharp_package",
        ]
        positions = [joined.find(f"strategy: {s}") for s in order]
        for s, pos in zip(order, positions):
            self.assertGreater(pos, -1, f"missing {s} in entries: {joined}")
        self.assertEqual(
            positions,
            sorted(positions),
            f"entries emitted out of documented order: {positions}",
        )


_CSHARP_WAVES = (
    "csharp_solution", "csharp_project", "csharp_msbuild_targets",
    "csharp_test_framework", "csharp_aspnet_routes", "csharp_efcore",
    # ADR 0060: csharp_package is wired alongside the Wave 1-3 stack
    # whenever .cs files exist. Acceptance suite must verify it appears
    # in C# fixtures and does not leak into non-C# fixtures.
    "csharp_package",
)


def _init_fixture(name: str) -> tuple[str, dict]:
    """Run wd init on a fixture repo and return (yaml_text, parsed_data)."""
    fixture_dir = _FIXTURES / name
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / ".weld" / "discover.yaml"
        assert init_run(fixture_dir, out, force=True), f"wd init failed for {name}"
        text = out.read_text(encoding="utf-8")
        return text, parse_yaml(text)


class CsharpInitAcceptanceTest(unittest.TestCase):
    """End-to-end: ``wd init`` on the csharp_project fixture wires Wave 1-3."""

    def test_csharp_project_emits_wave_1_3_strategy_stack(self) -> None:
        fixture = _FIXTURES / "csharp_project"
        if not fixture.is_dir():
            self.skipTest(f"Fixture not present at {fixture}")
        text, data = _init_fixture("csharp_project")
        strategies = {s.get("strategy") for s in data.get("sources", [])}
        for expected in _CSHARP_WAVES:
            self.assertIn(expected, strategies, f"sources missing {expected}")
            self.assertIn(expected, text, f"header missing {expected}")

    def test_non_csharp_fixtures_do_not_emit_csharp_strategies(self) -> None:
        csharp = set(_CSHARP_WAVES)
        for name in ("python_bazel", "typescript_node", "legacy_onboarding"):
            if not (_FIXTURES / name).is_dir():
                self.skipTest(f"Fixture not present at {_FIXTURES / name}")
            _, data = _init_fixture(name)
            strategies = {s.get("strategy") for s in data.get("sources", [])}
            self.assertFalse(strategies & csharp, f"{name}: {strategies & csharp}")


if __name__ == "__main__":
    unittest.main()
