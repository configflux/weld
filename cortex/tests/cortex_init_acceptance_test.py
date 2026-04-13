"""Acceptance tests for cortex init — whole-repo onboarding via fixture repos.

Validates that cortex init produces a discover.yaml that:
  - covers all 7 artifact classes (code, docs, policy, infra, build, tests, operations)
  - detects the correct strategies for each fixture shape
  - generates parseable YAML with the documented section structure
  - respects the --force / no-overwrite guard

Uses the polyglot fixture repos under cortex/tests/fixtures/.

"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from cortex._yaml import parse_yaml  # noqa: E402
from cortex.init import init  # noqa: E402
from cortex.init_detect import (  # noqa: E402
    detect_ci,
    detect_claude,
    detect_compose,
    detect_dockerfiles,
    detect_docs,
    detect_frameworks,
    detect_languages,
    detect_root_configs,
    detect_ros2,
    find_python_glob_roots,
    scan_files,
)

_FIXTURES = Path(__file__).resolve().parent / "fixtures"

# All 7 documented artifact classes that must appear in every discover.yaml.
_ALL_ARTIFACT_CLASSES = [
    "code", "docs", "policy", "infra", "build", "tests", "operations",
]

def _init_fixture(name: str) -> tuple[str, dict]:
    """Run cortex init on a fixture repo and return (yaml_text, parsed_data)."""
    fixture_dir = _FIXTURES / name
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / ".cortex" / "discover.yaml"
        success = init(fixture_dir, out, force=True)
        assert success, f"cortex init failed for {name}"
        text = out.read_text(encoding="utf-8")
        data = parse_yaml(text)
        return text, data

def _detect_all(root: Path) -> dict:
    """Run all detection phases and return generate_yaml kwargs."""
    files = scan_files(root)
    languages = detect_languages(files)
    frameworks = detect_frameworks(root, files)
    dockerfiles = detect_dockerfiles(root, files)
    compose_files = detect_compose(root, files)
    ci_files = detect_ci(root, files)
    claude_agents, claude_commands = detect_claude(root, files)
    doc_dirs = detect_docs(root, files)
    python_globs = find_python_glob_roots(root, files) if "python" in languages else []
    root_configs = detect_root_configs(root, files)
    return dict(
        languages=languages,
        frameworks=frameworks,
        dockerfiles=dockerfiles,
        compose_files=compose_files,
        ci_files=ci_files,
        claude_agents=claude_agents,
        claude_commands=claude_commands,
        doc_dirs=doc_dirs,
        python_globs=python_globs,
        root_configs=root_configs,
    )

class InitArtifactClassCoverageTest(unittest.TestCase):
    """Every fixture's discover.yaml must contain all 7 artifact-class sections."""

    def _assert_all_sections(self, yaml_text: str, fixture_name: str) -> None:
        for cls in _ALL_ARTIFACT_CLASSES:
            self.assertIn(
                f"# ===== {cls}",
                yaml_text,
                f"{fixture_name}: missing artifact-class section for '{cls}'",
            )

    def test_python_bazel_has_all_artifact_classes(self) -> None:
        text, _ = _init_fixture("python_bazel")
        self._assert_all_sections(text, "python_bazel")

    def test_typescript_node_has_all_artifact_classes(self) -> None:
        text, _ = _init_fixture("typescript_node")
        self._assert_all_sections(text, "typescript_node")

    def test_cpp_clang_has_all_artifact_classes(self) -> None:
        text, _ = _init_fixture("cpp_clang")
        self._assert_all_sections(text, "cpp_clang")

    def test_legacy_onboarding_has_all_artifact_classes(self) -> None:
        text, _ = _init_fixture("legacy_onboarding")
        self._assert_all_sections(text, "legacy_onboarding")

class InitStrategyDetectionTest(unittest.TestCase):
    """Fixture repos produce the correct strategies for their stack."""

    def test_python_bazel_detects_framework_strategies(self) -> None:
        _, data = _init_fixture("python_bazel")
        strategies = {s.get("strategy") for s in data.get("sources", [])}
        self.assertIn("python_module", strategies)
        self.assertIn("fastapi", strategies)
        self.assertIn("sqlalchemy", strategies)
        self.assertIn("markdown", strategies)
        self.assertIn("yaml_meta", strategies)
        self.assertIn("config_file", strategies)

    def test_typescript_node_avoids_python_strategies(self) -> None:
        _, data = _init_fixture("typescript_node")
        strategies = {s.get("strategy") for s in data.get("sources", [])}
        for unwanted in ("fastapi", "sqlalchemy", "pydantic"):
            self.assertNotIn(unwanted, strategies,
                             f"TypeScript project should not have {unwanted}")

    def test_cpp_clang_avoids_python_strategies(self) -> None:
        _, data = _init_fixture("cpp_clang")
        strategies = {s.get("strategy") for s in data.get("sources", [])}
        for unwanted in ("python_module", "fastapi", "sqlalchemy", "pydantic"):
            self.assertNotIn(unwanted, strategies,
                             f"C++ project should not have {unwanted}")

    def test_cpp_clang_emits_tree_sitter_cpp_with_emit_calls(self) -> None:
        """cpp_clang must produce a tree_sitter source entry with
        ``language: cpp`` and ``emit_calls: true``."""
        _, data = _init_fixture("cpp_clang")
        cpp_sources = [
            s for s in data.get("sources", [])
            if s.get("strategy") == "tree_sitter"
            and s.get("language") == "cpp"
        ]
        self.assertTrue(
            cpp_sources,
            "cpp_clang should produce at least one tree_sitter cpp source",
        )
        for src in cpp_sources:
            self.assertTrue(
                src.get("emit_calls") in (True, "true"),
                f"cpp tree_sitter source missing emit_calls: {src}",
            )

    def test_legacy_detects_python_but_not_frameworks(self) -> None:
        _, data = _init_fixture("legacy_onboarding")
        strategies = {s.get("strategy") for s in data.get("sources", [])}
        self.assertIn("python_module", strategies)
        for unwanted in ("fastapi", "sqlalchemy", "pydantic"):
            self.assertNotIn(unwanted, strategies,
                             f"Legacy project should not have {unwanted}")

    def test_cross_fixture_profiles_differ(self) -> None:
        profiles = {}
        for name in ("python_bazel", "typescript_node", "cpp_clang",
                      "legacy_onboarding"):
            _, data = _init_fixture(name)
            strats = frozenset(
                s.get("strategy") for s in data.get("sources", []))
            profiles[name] = strats
        unique = len(set(profiles.values()))
        self.assertGreaterEqual(unique, 3,
                                f"Expected >= 3 unique profiles, got {unique}")

class InitRos2DetectionTest(unittest.TestCase):
    """ROS2 workspace detection wires every ros2_* strategy."""

    _ROS2_STRATEGIES = (
        "ros2_package",
        "ros2_cmake",
        "ros2_interfaces",
        "ros2_topology",
        "ros2_launch",
    )

    def test_detect_ros2_finds_ament_cmake_package(self) -> None:
        fixture = _FIXTURES / "ros2_workspace"
        files = scan_files(fixture)
        roots = detect_ros2(fixture, files)
        self.assertEqual(
            sorted(roots),
            ["src/demo_pkg"],
            f"Expected to detect src/demo_pkg, got {roots}",
        )

    def test_detect_ros2_ignores_non_ros2_cpp_fixture(self) -> None:
        fixture = _FIXTURES / "cpp_clang"
        files = scan_files(fixture)
        self.assertEqual(detect_ros2(fixture, files), [])

    def test_ros2_workspace_emits_all_ros2_strategies(self) -> None:
        _, data = _init_fixture("ros2_workspace")
        strategies = {s.get("strategy") for s in data.get("sources", [])}
        for expected in self._ROS2_STRATEGIES:
            self.assertIn(
                expected,
                strategies,
                f"ros2_workspace should wire {expected}, got {strategies}",
            )

    def test_ros2_workspace_wires_cpp_tree_sitter_with_emit_calls(self) -> None:
        _, data = _init_fixture("ros2_workspace")
        cpp_sources = [
            s for s in data.get("sources", [])
            if s.get("strategy") == "tree_sitter"
            and s.get("language") == "cpp"
        ]
        self.assertTrue(
            cpp_sources,
            "ros2_workspace should include a tree_sitter cpp source entry",
        )
        for src in cpp_sources:
            self.assertTrue(
                src.get("emit_calls") in (True, "true"),
                f"tree_sitter cpp source missing emit_calls: {src}",
            )

    def test_ros2_workspace_interface_globs_cover_msg_srv_action(self) -> None:
        _, data = _init_fixture("ros2_workspace")
        iface_globs = [
            s.get("glob", "") for s in data.get("sources", [])
            if s.get("strategy") == "ros2_interfaces"
        ]
        joined = " ".join(iface_globs)
        self.assertIn(".msg", joined, f"no .msg glob in {iface_globs}")
        self.assertIn(".srv", joined, f"no .srv glob in {iface_globs}")
        self.assertIn(".action", joined, f"no .action glob in {iface_globs}")

    def test_ros2_workspace_launch_globs_cover_py_xml_yaml(self) -> None:
        _, data = _init_fixture("ros2_workspace")
        launch_globs = [
            s.get("glob", "") for s in data.get("sources", [])
            if s.get("strategy") == "ros2_launch"
        ]
        joined = " ".join(launch_globs)
        self.assertIn(".launch.py", joined, f"no .launch.py glob in {launch_globs}")
        self.assertIn(".launch.xml", joined, f"no .launch.xml glob in {launch_globs}")
        self.assertIn(
            ".launch.yaml", joined, f"no .launch.yaml glob in {launch_globs}",
        )

    def test_ros2_workspace_package_glob_matches_package_xml(self) -> None:
        _, data = _init_fixture("ros2_workspace")
        pkg_sources = [
            s for s in data.get("sources", [])
            if s.get("strategy") == "ros2_package"
        ]
        self.assertTrue(pkg_sources, "no ros2_package source entry emitted")
        self.assertTrue(
            any("package.xml" in s.get("glob", "") for s in pkg_sources),
            f"ros2_package glob should match package.xml, got {pkg_sources}",
        )

    def test_cpp_clang_fixture_emits_no_ros2_strategies(self) -> None:
        _, data = _init_fixture("cpp_clang")
        strategies = {s.get("strategy") for s in data.get("sources", [])}
        for unwanted in self._ROS2_STRATEGIES:
            self.assertNotIn(
                unwanted,
                strategies,
                f"cpp_clang must not emit {unwanted}: got {strategies}",
            )

    def test_ros2_workspace_emits_all_artifact_classes(self) -> None:
        text, _ = _init_fixture("ros2_workspace")
        for cls in _ALL_ARTIFACT_CLASSES:
            self.assertIn(
                f"# ===== {cls}", text,
                f"ros2_workspace: missing artifact-class section for '{cls}'",
            )

class InitYamlStructureTest(unittest.TestCase):
    """The generated YAML is parseable and structurally sound."""

    def test_all_fixtures_parseable(self) -> None:
        for name in ("python_bazel", "typescript_node", "cpp_clang",
                      "legacy_onboarding"):
            text, data = _init_fixture(name)
            self.assertIn("sources", data, f"{name}: missing sources key")
            self.assertIsInstance(data["sources"], list,
                                 f"{name}: sources should be a list")

    def test_every_source_has_strategy(self) -> None:
        for name in ("python_bazel", "typescript_node", "cpp_clang",
                      "legacy_onboarding"):
            _, data = _init_fixture(name)
            for i, src in enumerate(data.get("sources", [])):
                self.assertIn("strategy", src,
                              f"{name}: source[{i}] missing strategy")

    def test_every_source_has_glob_or_files(self) -> None:
        for name in ("python_bazel", "typescript_node", "cpp_clang",
                      "legacy_onboarding"):
            _, data = _init_fixture(name)
            for i, src in enumerate(data.get("sources", [])):
                has_glob = "glob" in src
                has_files = "files" in src
                self.assertTrue(has_glob or has_files,
                                f"{name}: source[{i}] missing glob/files")

    def test_header_documents_artifact_classes(self) -> None:
        text, _ = _init_fixture("python_bazel")
        self.assertIn("Artifact classes:", text)

    def test_empty_classes_have_stubs(self) -> None:
        text, _ = _init_fixture("cpp_clang")
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if "(uncomment to enable)" in line:
                found_stub = False
                for j in range(i + 1, min(i + 5, len(lines))):
                    if lines[j].strip().startswith("# - glob:"):
                        found_stub = True
                        break
                    if lines[j].strip() and not lines[j].strip().startswith("#"):
                        break
                self.assertTrue(found_stub,
                                f"Empty section at line {i+1} missing stub")

class InitOverwriteGuardTest(unittest.TestCase):
    """The no-overwrite guard and --force flag work correctly."""

    def test_refuses_overwrite_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / ".cortex" / "discover.yaml"
            out.parent.mkdir(parents=True)
            out.write_text("# existing\n", encoding="utf-8")
            result = init(Path(td), out, force=False)
            self.assertFalse(result)
            self.assertEqual(out.read_text(), "# existing\n")

    def test_force_overwrites(self) -> None:
        fixture_dir = _FIXTURES / "python_bazel"
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / ".cortex" / "discover.yaml"
            out.parent.mkdir(parents=True)
            out.write_text("# existing\n", encoding="utf-8")
            result = init(fixture_dir, out, force=True)
            self.assertTrue(result)
            self.assertNotEqual(out.read_text(), "# existing\n")

class InitMonorepoDetectionTest(unittest.TestCase):
    """Init detects monorepo structure from services/ paths."""

    def test_python_bazel_has_services_globs(self) -> None:
        _, data = _init_fixture("python_bazel")
        globs = [s.get("glob", "") for s in data.get("sources", []) if "glob" in s]
        has_services = any("services" in g for g in globs)
        self.assertTrue(has_services,
                        f"Expected services/ in globs, got {globs}")

    def test_python_bazel_detects_docs_dir(self) -> None:
        _, data = _init_fixture("python_bazel")
        strategies = {s.get("strategy") for s in data.get("sources", [])}
        self.assertIn("markdown", strategies,
                      "Should detect markdown for docs/")

if __name__ == "__main__":
    unittest.main()
