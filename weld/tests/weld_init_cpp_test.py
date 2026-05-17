"""Unit and acceptance tests for the C++ build-system init wiring.

Covers :mod:`weld._init_cpp`: the detector flag map fed by ``wd init``
and the YAML source-entry generator that consumes it. Includes the
acceptance check that ``wd init`` on the bundled C++ fixtures
(``cpp_clang``, ``cpp_cmake_project``) produces a discover.yaml that
wires the bundled :mod:`weld.strategies.cpp_buildsystem_detector`
strategy against the recursive build-system file globs (CMakeLists.txt,
BUILD, BUILD.bazel, meson.build) plus a root-level singleton entry via
:mod:`weld.strategies.config_file` for the canonical CMakeLists.txt at
the repository root. Also includes the negative case that non-C++
fixtures do not get the C++ build-system stack wired.

The wiring closes the gap surfaced by the cpp-extraction-quality
investigation: ``wd init`` previously emitted only the C++
source globs (``**/*.cpp`` etc.) and Makefile root entry, so a
nlohmann/json-style header-only library produced zero CMakeLists graph
nodes and the bench's ``njson-xrepo-01`` cross-repo question scored
F1=0.00 even when extraction was otherwise correct.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld._init_cpp import (  # noqa: E402
    cpp_buildsystem_source_entries,
    detect_cpp_buildsystem,
)
from weld._yaml import parse_yaml  # noqa: E402
from weld.init import _YAML_HEADER, init as init_run  # noqa: E402
from weld.init_detect import scan_files  # noqa: E402

_FIXTURES = Path(__file__).resolve().parent / "fixtures"

_EXPECTED_FLAG_KEYS = {
    "has_cmake",
    "has_bazel",
    "has_meson",
    "has_root_cmake",
    # Root-level config files extending the C++ wiring. These are NOT
    # buildsystem roots; they are formatter, linter, and Bazel-workspace
    # configuration files routed through ``config_file`` instead of
    # ``cpp_buildsystem_detector``. Each flag fires only when the file
    # appears at the repository root -- these are canonical-singleton
    # configs, not recursive globs.
    "has_clang_format",
    "has_clang_tidy",
    "has_workspace",
    "has_workspace_bazel",
}


class DetectCppBuildsystemContractTest(unittest.TestCase):
    """The detector returns the exact flag set documented in the spec."""

    def test_returns_documented_flag_keys(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            flags = detect_cpp_buildsystem(scan_files(root), root=root)
        self.assertEqual(set(flags.keys()), _EXPECTED_FLAG_KEYS)
        for v in flags.values():
            self.assertIsInstance(v, bool)

    def test_empty_repo_returns_all_false(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            flags = detect_cpp_buildsystem(scan_files(root), root=root)
        for k, v in flags.items():
            self.assertFalse(v, f"empty repo should not fire {k}")


class DetectCppBuildsystemPerFlagTest(unittest.TestCase):
    """Each flag fires on its dedicated signal and nothing else."""

    def test_has_cmake_fires_on_root_cmakelists(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "CMakeLists.txt").write_text(
                "project(demo)\nadd_executable(d main.cpp)\n",
            )
            flags = detect_cpp_buildsystem(scan_files(root), root=root)
        self.assertTrue(flags["has_cmake"])
        self.assertTrue(flags["has_root_cmake"])
        self.assertFalse(flags["has_bazel"])
        self.assertFalse(flags["has_meson"])

    def test_has_cmake_fires_on_nested_cmakelists(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sub = root / "lib"
            sub.mkdir()
            (sub / "CMakeLists.txt").write_text("add_library(x x.cpp)\n")
            flags = detect_cpp_buildsystem(scan_files(root), root=root)
        self.assertTrue(flags["has_cmake"])
        # Nested-only does not set has_root_cmake.
        self.assertFalse(flags["has_root_cmake"])

    def test_has_bazel_fires_on_build_bazel(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "BUILD.bazel").write_text("# bazel\n")
            flags = detect_cpp_buildsystem(scan_files(root), root=root)
        self.assertTrue(flags["has_bazel"])
        self.assertFalse(flags["has_cmake"])

    def test_has_bazel_fires_on_bare_build_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "BUILD").write_text("# bazel\n")
            flags = detect_cpp_buildsystem(scan_files(root), root=root)
        self.assertTrue(flags["has_bazel"])

    def test_has_meson_fires_on_meson_build(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "meson.build").write_text("project('x', 'cpp')\n")
            flags = detect_cpp_buildsystem(scan_files(root), root=root)
        self.assertTrue(flags["has_meson"])
        self.assertFalse(flags["has_cmake"])
        self.assertFalse(flags["has_bazel"])

    def test_unrelated_files_do_not_fire(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "main.cpp").write_text("int main(){}\n")
            (root / "README.md").write_text("# x\n")
            flags = detect_cpp_buildsystem(scan_files(root), root=root)
        for k, v in flags.items():
            self.assertFalse(v, f"unrelated files should not fire {k}")


class DetectCppBuildsystemOnFixturesTest(unittest.TestCase):
    """The bundled C++ fixtures fire exactly the flags they should."""

    def test_cpp_clang_fixture_fires_cmake_root(self) -> None:
        root = _FIXTURES / "cpp_clang"
        flags = detect_cpp_buildsystem(scan_files(root), root=root)
        self.assertTrue(flags["has_cmake"])
        self.assertTrue(flags["has_root_cmake"])
        self.assertFalse(flags["has_bazel"])
        self.assertFalse(flags["has_meson"])

    def test_cpp_cmake_project_fixture_fires_cmake_root(self) -> None:
        root = _FIXTURES / "cpp_cmake_project"
        flags = detect_cpp_buildsystem(scan_files(root), root=root)
        self.assertTrue(flags["has_cmake"])
        self.assertTrue(flags["has_root_cmake"])
        self.assertFalse(flags["has_bazel"])
        self.assertFalse(flags["has_meson"])


class CppBuildsystemSourceEntriesTest(unittest.TestCase):
    """Source-entry generator gates strategies on the flag dict."""

    def test_no_flags_returns_empty(self) -> None:
        flags = {k: False for k in _EXPECTED_FLAG_KEYS}
        self.assertEqual(cpp_buildsystem_source_entries(flags), [])

    def test_cmake_flag_emits_recursive_glob_with_detector(self) -> None:
        flags = {k: False for k in _EXPECTED_FLAG_KEYS}
        flags["has_cmake"] = True
        joined = "\n".join(cpp_buildsystem_source_entries(flags))
        self.assertIn("**/CMakeLists.txt", joined)
        self.assertIn("strategy: cpp_buildsystem_detector", joined)

    def test_root_cmake_flag_emits_files_singleton_with_config_file(self) -> None:
        flags = {k: False for k in _EXPECTED_FLAG_KEYS}
        flags["has_cmake"] = True
        flags["has_root_cmake"] = True
        joined = "\n".join(cpp_buildsystem_source_entries(flags))
        # Root singleton entry uses files: ['CMakeLists.txt'] so the
        # config_file strategy (which only consumes ``files``, not
        # ``glob``) actually mints a config: node for the canonical
        # root build script. This guarantees at least one node
        # references CMakeLists.txt even on repos where every recursive
        # glob entry is filtered out by excludes.
        self.assertIn('files: ["CMakeLists.txt"]', joined)
        self.assertIn("strategy: config_file", joined)

    def test_root_cmake_without_has_cmake_does_not_double_emit(self) -> None:
        # has_root_cmake is a sub-signal of has_cmake by construction;
        # the entry-generator must not crash when callers pass it on
        # its own (defensive) and must never emit a root-singleton when
        # has_cmake is false.
        flags = {k: False for k in _EXPECTED_FLAG_KEYS}
        flags["has_root_cmake"] = True
        self.assertEqual(cpp_buildsystem_source_entries(flags), [])

    def test_bazel_flag_emits_build_bazel_and_bare_build_globs(self) -> None:
        flags = {k: False for k in _EXPECTED_FLAG_KEYS}
        flags["has_bazel"] = True
        joined = "\n".join(cpp_buildsystem_source_entries(flags))
        self.assertIn("**/BUILD.bazel", joined)
        self.assertIn("**/BUILD", joined)
        self.assertIn("strategy: cpp_buildsystem_detector", joined)

    def test_meson_flag_emits_meson_build_glob(self) -> None:
        flags = {k: False for k in _EXPECTED_FLAG_KEYS}
        flags["has_meson"] = True
        joined = "\n".join(cpp_buildsystem_source_entries(flags))
        self.assertIn("**/meson.build", joined)
        self.assertIn("strategy: cpp_buildsystem_detector", joined)

    def test_full_flag_set_emits_every_glob_in_documented_order(self) -> None:
        flags = {k: True for k in _EXPECTED_FLAG_KEYS}
        joined = "\n".join(cpp_buildsystem_source_entries(flags))
        # Order matches the docstring contract on
        # cpp_buildsystem_source_entries: recursive cmake first, then
        # root-singleton, then bazel, then meson, then root-config
        # singletons (clang-format, clang-tidy, WORKSPACE,
        # WORKSPACE.bazel). Asserting positions rather than substring
        # counts keeps the test robust against whitespace tweaks while
        # still pinning the documented order.
        positions = [
            joined.find('glob: "**/CMakeLists.txt"'),
            joined.find('files: ["CMakeLists.txt"]'),
            joined.find('glob: "**/BUILD.bazel"'),
            joined.find('glob: "**/BUILD"'),
            joined.find('glob: "**/meson.build"'),
            joined.find('files: [".clang-format"]'),
            joined.find('files: [".clang-tidy"]'),
            joined.find('files: ["WORKSPACE"]'),
            joined.find('files: ["WORKSPACE.bazel"]'),
        ]
        for p in positions:
            self.assertGreater(p, -1, f"missing entry: positions={positions}")
        self.assertEqual(
            positions, sorted(positions),
            f"entries emitted out of documented order: {positions}",
        )

    # Root-config singleton entry tests (.clang-format, .clang-tidy,
    # WORKSPACE, WORKSPACE.bazel) live in
    # :mod:`weld.tests.weld_init_cpp_root_configs_test` so this test
    # module stays under the 400-line cap.


def _init_fixture(name: str) -> tuple[str, dict]:
    """Run wd init on a fixture repo and return (yaml_text, parsed_data)."""
    fixture_dir = _FIXTURES / name
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / ".weld" / "discover.yaml"
        assert init_run(fixture_dir, out, force=True), f"wd init failed for {name}"
        text = out.read_text(encoding="utf-8")
        return text, parse_yaml(text)


class CppInitAcceptanceTest(unittest.TestCase):
    """End-to-end: ``wd init`` on C++ fixtures wires the build-system stack."""

    def test_cpp_clang_emits_buildsystem_detector_for_cmake(self) -> None:
        _, data = _init_fixture("cpp_clang")
        sources = data.get("sources", [])
        # Recursive glob via cpp_buildsystem_detector is what makes the
        # graph contain CMakeLists nodes for nested files (e.g.
        # nlohmann/json-style ``cmake/`` and ``docs/.../`` trees).
        recursive = [
            s for s in sources
            if s.get("strategy") == "cpp_buildsystem_detector"
            and s.get("glob") == "**/CMakeLists.txt"
        ]
        self.assertTrue(
            recursive,
            f"cpp_clang must wire **/CMakeLists.txt -> cpp_buildsystem_detector, "
            f"got: {[(s.get('strategy'), s.get('glob')) for s in sources]}",
        )
        # Root singleton via config_file ensures at least one config:
        # node exists for the canonical root build script even if every
        # recursive glob is excluded.
        root_singleton = [
            s for s in sources
            if s.get("strategy") == "config_file"
            and "CMakeLists.txt" in (s.get("files") or [])
        ]
        self.assertTrue(
            root_singleton,
            "cpp_clang must wire files: ['CMakeLists.txt'] -> config_file",
        )

    def test_cpp_cmake_project_emits_buildsystem_detector(self) -> None:
        _, data = _init_fixture("cpp_cmake_project")
        strategies = {s.get("strategy") for s in data.get("sources", [])}
        self.assertIn("cpp_buildsystem_detector", strategies)
        # Existing cpp tree-sitter wiring must still emit alongside the
        # new build-system entries -- adding build-system globs must
        # not regress the source globs that drive symbol extraction.
        ts_cpp = [
            s for s in data.get("sources", [])
            if s.get("strategy") == "tree_sitter"
            and s.get("language") == "cpp"
        ]
        self.assertTrue(
            ts_cpp,
            "cpp_cmake_project must still emit tree_sitter cpp source entries",
        )

    # End-to-end ``wd init`` acceptance for the new ``.clang-format`` /
    # ``.clang-tidy`` / ``WORKSPACE`` / ``WORKSPACE.bazel`` singletons
    # lives in :mod:`weld.tests.weld_init_cpp_root_configs_test`.

    def test_non_cpp_fixtures_do_not_get_buildsystem_detector(self) -> None:
        for name in ("python_bazel", "typescript_node", "csharp_project"):
            if not (_FIXTURES / name).is_dir():
                continue
            _, data = _init_fixture(name)
            strategies = {s.get("strategy") for s in data.get("sources", [])}
            self.assertNotIn(
                "cpp_buildsystem_detector",
                strategies,
                f"{name} must not wire cpp_buildsystem_detector "
                f"(C++ language not detected): got {strategies}",
            )


class CppBuildsystemDiscoveryIntegrationTest(unittest.TestCase):
    """End-to-end: wired cpp_buildsystem_detector mints CMakeLists nodes.

    Closes the bench acceptance criterion verbatim: after ``wd discover``
    on a fixture C++ repo containing a CMakeLists.txt, the graph
    contains a node referencing CMakeLists.txt. Run via the strategy's
    ``extract`` function with the same source dict the init helper
    emits, so a regression in either piece is caught here.
    """

    def test_discover_produces_node_referencing_cmakelists(self) -> None:
        # Lazy import keeps this test independent of the wider
        # weld.discover surface; we only need the strategy entry point.
        from weld.strategies import cpp_buildsystem_detector

        root = _FIXTURES / "cpp_cmake_project"
        result = cpp_buildsystem_detector.extract(
            root, {"glob": "**/CMakeLists.txt"}, {},
        )
        # At least one node must reference a CMakeLists.txt path via
        # props.file. Without the new init wiring no such node exists
        # because the legacy template only emitted **/*.cpp etc.
        cmake_nodes = [
            (nid, node) for nid, node in result.nodes.items()
            if str(node.get("props", {}).get("file", "")).endswith("CMakeLists.txt")
        ]
        self.assertTrue(
            cmake_nodes,
            f"cpp_buildsystem_detector must mint at least one CMakeLists "
            f"node on cpp_cmake_project; got nodes: "
            f"{list(result.nodes.keys())}",
        )

    def test_root_singleton_config_node_minted(self) -> None:
        # The root-level files: ['CMakeLists.txt'] entry routed through
        # config_file is what guarantees a config: node always exists
        # for the canonical root build script. Verify the strategy
        # wires correctly with the source dict the init helper emits.
        from weld.strategies import config_file

        root = _FIXTURES / "cpp_cmake_project"
        result = config_file.extract(
            root, {"files": ["CMakeLists.txt"]}, {},
        )
        self.assertIn(
            "config:CMakeLists_txt",
            result.nodes,
            f"config_file must mint a config: node for CMakeLists.txt; "
            f"got: {list(result.nodes.keys())}",
        )
        node = result.nodes["config:CMakeLists_txt"]
        self.assertEqual(node["props"]["file"], "CMakeLists.txt")


class CppBuildsystemYamlHeaderTest(unittest.TestCase):
    """Generated discover.yaml header lists every wired strategy.

    The C++ build-system wiring went in via _init_cpp.py but the
    documentation header in :mod:`weld.init` was not extended at the
    time. This regression test ensures the strategy stays listed under
    "Available strategies" so users see the same surface in the header
    that they see per-entry in the YAML body.
    """

    def test_yaml_header_lists_cpp_buildsystem_detector(self) -> None:
        self.assertIn("cpp_buildsystem_detector", _YAML_HEADER)


if __name__ == "__main__":
    unittest.main()
