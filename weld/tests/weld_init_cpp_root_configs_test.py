"""Tests for the C++ root-config wiring extensions.

Sibling of :mod:`weld.tests.weld_init_cpp_test`. Lives in its own file
so the original test module stays under the 400-line cap. Covers the
detector flags (``has_clang_format``, ``has_clang_tidy``,
``has_workspace``, ``has_workspace_bazel``) and the YAML
``config_file`` source-entry emission for each.

Scope rationale: ``.clang-format`` / ``.clang-tidy`` / ``WORKSPACE`` /
``WORKSPACE.bazel`` are formatter, linter, and Bazel-workspace
configuration files. They are *not* build-system roots that warrant
``build-target`` nodes, so they route through
:mod:`weld.strategies.config_file` (which mints a ``config:`` node)
rather than :mod:`weld.strategies.cpp_buildsystem_detector`. ``MODULE.bazel``
is already wired by :data:`weld.init_detect_constants.ROOT_CONFIG_NAMES`
and is not duplicated here. ``Package.swift`` is intentionally out of
scope -- weld has no Swift parser and ``Package.swift`` belongs to
Swift PM, not the C++ stack.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


from weld._init_cpp import (  # noqa: E402
    cpp_buildsystem_source_entries,
    detect_cpp_buildsystem,
)
from weld._yaml import parse_yaml  # noqa: E402
from weld.init import init as init_run  # noqa: E402
from weld.init_detect import scan_files  # noqa: E402

# Mirrors :data:`weld.tests.weld_init_cpp_test._EXPECTED_FLAG_KEYS`.
# Duplicated rather than imported so a regression in one file does not
# silently mask a regression in the other; the canonical contract test
# lives in the original test module.
_FLAG_KEYS = {
    "has_cmake",
    "has_bazel",
    "has_meson",
    "has_root_cmake",
    "has_clang_format",
    "has_clang_tidy",
    "has_workspace",
    "has_workspace_bazel",
}


class DetectCppRootConfigsPerFlagTest(unittest.TestCase):
    """Per-flag tests for the root-config additions.

    Each new flag is a root-only signal -- nested-only occurrences must
    not fire it because these are canonical-singleton configs (one per
    repo, not recursive). Mirrors the ``has_root_cmake`` semantics.
    """

    def test_has_clang_format_fires_on_root(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".clang-format").write_text("BasedOnStyle: Google\n")
            flags = detect_cpp_buildsystem(scan_files(root), root=root)
        self.assertTrue(flags["has_clang_format"])
        self.assertFalse(flags["has_clang_tidy"])
        self.assertFalse(flags["has_workspace"])
        self.assertFalse(flags["has_workspace_bazel"])

    def test_has_clang_format_does_not_fire_on_nested(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sub = root / "subproject"
            sub.mkdir()
            (sub / ".clang-format").write_text("BasedOnStyle: Google\n")
            flags = detect_cpp_buildsystem(scan_files(root), root=root)
        self.assertFalse(flags["has_clang_format"])

    def test_has_clang_tidy_fires_on_root(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".clang-tidy").write_text("Checks: '-*,clang-diagnostic-*'\n")
            flags = detect_cpp_buildsystem(scan_files(root), root=root)
        self.assertTrue(flags["has_clang_tidy"])
        self.assertFalse(flags["has_clang_format"])

    def test_has_clang_tidy_does_not_fire_on_nested(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sub = root / "subproject"
            sub.mkdir()
            (sub / ".clang-tidy").write_text("Checks: 'modernize-*'\n")
            flags = detect_cpp_buildsystem(scan_files(root), root=root)
        self.assertFalse(flags["has_clang_tidy"])

    def test_has_workspace_fires_on_root(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "WORKSPACE").write_text("# bazel workspace\n")
            flags = detect_cpp_buildsystem(scan_files(root), root=root)
        self.assertTrue(flags["has_workspace"])
        self.assertFalse(flags["has_workspace_bazel"])

    def test_has_workspace_bazel_fires_on_root(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "WORKSPACE.bazel").write_text("# bazel workspace\n")
            flags = detect_cpp_buildsystem(scan_files(root), root=root)
        self.assertTrue(flags["has_workspace_bazel"])
        self.assertFalse(flags["has_workspace"])

    def test_has_workspace_does_not_fire_on_nested(self) -> None:
        # Nested WORKSPACE files are diagnostic-only at most -- treat
        # them like the rest of the singletons. Same rationale as
        # ``has_root_cmake``: a recursive glob would create false
        # positives for vendored Bazel subprojects.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sub = root / "vendored"
            sub.mkdir()
            (sub / "WORKSPACE").write_text("# vendored\n")
            flags = detect_cpp_buildsystem(scan_files(root), root=root)
        self.assertFalse(flags["has_workspace"])


class CppRootConfigSourceEntriesTest(unittest.TestCase):
    """Source-entry generator emits ``config_file`` entries per flag."""

    def _flags(self, set_key: str) -> dict[str, bool]:
        flags = {k: False for k in _FLAG_KEYS}
        flags[set_key] = True
        return flags

    def test_clang_format_flag_emits_config_file_singleton(self) -> None:
        joined = "\n".join(
            cpp_buildsystem_source_entries(self._flags("has_clang_format"))
        )
        # Routed through ``config_file`` (not ``cpp_buildsystem_detector``)
        # because ``.clang-format`` is a formatter config file, not a
        # build-system root. The leading dot is preserved verbatim in
        # the YAML; ``config_file`` strips it when minting the node id.
        self.assertIn('files: [".clang-format"]', joined)
        self.assertIn("strategy: config_file", joined)

    def test_clang_tidy_flag_emits_config_file_singleton(self) -> None:
        joined = "\n".join(
            cpp_buildsystem_source_entries(self._flags("has_clang_tidy"))
        )
        self.assertIn('files: [".clang-tidy"]', joined)
        self.assertIn("strategy: config_file", joined)

    def test_workspace_flag_emits_config_file_singleton(self) -> None:
        joined = "\n".join(
            cpp_buildsystem_source_entries(self._flags("has_workspace"))
        )
        self.assertIn('files: ["WORKSPACE"]', joined)
        self.assertIn("strategy: config_file", joined)

    def test_workspace_bazel_flag_emits_config_file_singleton(self) -> None:
        joined = "\n".join(
            cpp_buildsystem_source_entries(self._flags("has_workspace_bazel"))
        )
        self.assertIn('files: ["WORKSPACE.bazel"]', joined)
        self.assertIn("strategy: config_file", joined)

    def test_root_configs_independent_of_buildsystem_globs(self) -> None:
        # The new root-config singletons must emit even when no
        # CMakeLists / BUILD / meson is present. Use case: a header-only
        # C++ library that ships only ``.clang-format`` for downstream
        # consumers and no build files of its own.
        entries = cpp_buildsystem_source_entries(
            self._flags("has_clang_format"),
        )
        self.assertEqual(len(entries), 1)
        self.assertIn('files: [".clang-format"]', entries[0])


class CppRootConfigInitAcceptanceTest(unittest.TestCase):
    """End-to-end ``wd init`` produces the new ``config_file`` entries."""

    def test_cpp_repo_with_clang_configs_wires_them_via_config_file(self) -> None:
        # Acceptance: synthesize a minimal C++ repo with
        # .clang-format / .clang-tidy / WORKSPACE / WORKSPACE.bazel at
        # root. ``wd init`` must emit a ``config_file`` source entry
        # for each, alongside the existing CMake wiring. The synthetic
        # repo also includes a CMakeLists.txt + a .cpp file so the
        # C++ wiring path is taken at all (existing behaviour: the new
        # entries piggyback on detect_cpp_buildsystem only when C++ is
        # detected via languages).
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "main.cpp").write_text("int main(){}\n")
            (root / "CMakeLists.txt").write_text(
                "project(demo)\nadd_executable(d main.cpp)\n",
            )
            (root / ".clang-format").write_text("BasedOnStyle: Google\n")
            (root / ".clang-tidy").write_text(
                "Checks: '-*,clang-diagnostic-*'\n",
            )
            (root / "WORKSPACE").write_text("# bazel workspace\n")
            (root / "WORKSPACE.bazel").write_text("# bazel workspace\n")
            out = root / ".weld" / "discover.yaml"
            self.assertTrue(init_run(root, out, force=True))
            data = parse_yaml(out.read_text(encoding="utf-8"))
        sources = data.get("sources", [])
        # Each new singleton must appear as a ``config_file`` entry
        # whose ``files`` list contains the canonical name.
        for fname in (
            ".clang-format", ".clang-tidy", "WORKSPACE", "WORKSPACE.bazel",
        ):
            matched = [
                s for s in sources
                if s.get("strategy") == "config_file"
                and fname in (s.get("files") or [])
            ]
            self.assertTrue(
                matched,
                f"missing config_file entry for {fname}; got: "
                f"{[(s.get('strategy'), s.get('files'), s.get('glob')) for s in sources]}",
            )


if __name__ == "__main__":
    unittest.main()
