"""The npm workspace member map, and the path confinement under it (bd lrnx1.4).

ADR 0142 D3's first half: a package name the repository declares for *itself*
is not an external dependency. These cases pin what "declares for itself"
means -- which manifests are read, which globs count, which entry point a bare
import lands on -- and, just as load-bearing, what is refused. Every input here
comes from a ``package.json`` in someone else's repository, so a case that
only checked the happy path would be checking the easy half.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from weld.strategies._ts_module_files import (
    MAX_CONFIG_BYTES,
    clean_relative,
    contained_path,
    join_relative,
    resolve_module_file,
)
from weld.strategies._ts_workspace_members import (
    MAX_PATTERN_SEGMENTS,
    load_workspace_members,
    matches_pattern,
    read_manifest,
    workspace_patterns,
)


def write(root: Path, rel: str, body: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def manifest(**fields: object) -> str:
    return json.dumps(fields)


class ModulePathHygiene(unittest.TestCase):
    """:mod:`weld.strategies._ts_module_files` -- the confinement rules."""

    def test_relative_paths_are_normalised(self) -> None:
        self.assertEqual(clean_relative("./src//lib/./greeting"), "src/lib/greeting")
        self.assertEqual(clean_relative("src/lib/greeting"), "src/lib/greeting")

    def test_dot_dot_is_resolved_when_it_stays_inside(self) -> None:
        """A ``paths`` target of ``../../packages/shared`` is legitimate."""
        self.assertEqual(
            join_relative("apps/web", "../../packages/shared/src"),
            "packages/shared/src",
        )
        self.assertEqual(join_relative("apps/web", "../.."), "")

    def test_paths_that_climb_out_of_the_repository_are_refused(self) -> None:
        for spelling in ("../etc/passwd", "../../..", "a/../../b"):
            with self.subTest(spelling=spelling):
                self.assertIsNone(join_relative("", spelling))
                self.assertEqual(clean_relative(spelling), "")

    def test_absolute_and_drive_paths_are_refused(self) -> None:
        for spelling in ("/etc/passwd", "C:/Windows", "\\\\server\\share"):
            with self.subTest(spelling=spelling):
                self.assertEqual(clean_relative(spelling), "")

    def test_the_repository_root_is_not_a_refusal(self) -> None:
        """``""`` is an answer (the root); ``None`` is a refusal."""
        self.assertEqual(join_relative("apps", ".."), "")
        self.assertIsNone(join_relative("apps", "../.."))

    def test_a_symlink_leaving_the_repository_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp) / "outside"
            outside.mkdir()
            (outside / "secret.ts").write_text("export const x = 1;\n")
            root = Path(tmp) / "repo"
            root.mkdir()
            try:
                (root / "escape").symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):  # pragma: no cover
                self.skipTest("symlinks unavailable on this filesystem")
            self.assertIsNone(contained_path(root, "escape/secret.ts"))
            self.assertEqual(resolve_module_file(root, "escape/secret"), "")

    def test_module_paths_acquire_an_extension_or_an_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "src/lib/greeting.ts", "export const g = 1;\n")
            write(root, "packages/shared/index.ts", "export * from './money';\n")
            write(root, "packages/shared/money.tsx", "export const m = 1;\n")
            self.assertEqual(
                resolve_module_file(root, "src/lib/greeting"),
                "src/lib/greeting.ts",
            )
            self.assertEqual(
                resolve_module_file(root, "packages/shared"),
                "packages/shared/index.ts",
            )
            self.assertEqual(
                resolve_module_file(root, "packages/shared/money"),
                "packages/shared/money.tsx",
            )
            self.assertEqual(resolve_module_file(root, "packages/shared/absent"), "")

    def test_a_directory_never_answers_as_a_module_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src" / "lib").mkdir(parents=True)
            self.assertEqual(resolve_module_file(root, "src/lib"), "")


class PatternMatching(unittest.TestCase):
    """``packages/*`` names children, not descendants."""

    def test_star_matches_one_segment_only(self) -> None:
        self.assertTrue(matches_pattern("packages/shared", "packages/*"))
        self.assertFalse(matches_pattern("packages/shared/nested", "packages/*"))
        self.assertFalse(matches_pattern("apps/web", "packages/*"))

    def test_double_star_matches_any_depth(self) -> None:
        self.assertTrue(matches_pattern("packages/shared", "packages/**"))
        self.assertTrue(matches_pattern("packages/a/b/c", "packages/**"))
        self.assertFalse(matches_pattern("apps/web", "packages/**"))

    def test_a_literal_pattern_matches_only_itself(self) -> None:
        self.assertTrue(matches_pattern("packages/shared", "packages/shared"))
        self.assertFalse(matches_pattern("packages/shared2", "packages/shared"))


class ManifestReading(unittest.TestCase):
    """``read_manifest`` is total on everything a repository can present."""

    def test_missing_malformed_and_non_object_manifests_answer_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(read_manifest(root / "absent.json"), {})
            self.assertEqual(read_manifest(write(root, "bad.json", "{oops")), {})
            self.assertEqual(read_manifest(write(root, "list.json", "[1, 2]")), {})
            self.assertEqual(read_manifest(root), {})  # a directory

    def test_an_oversized_manifest_is_not_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            body = '{"name": "' + "x" * (MAX_CONFIG_BYTES + 16) + '"}'
            self.assertEqual(read_manifest(write(root, "big.json", body)), {})


class WorkspacePatterns(unittest.TestCase):
    def test_both_declared_shapes_are_read(self) -> None:
        self.assertEqual(
            workspace_patterns({"workspaces": ["packages/*", "apps/*"]}),
            ["packages/*", "apps/*"],
        )
        self.assertEqual(
            workspace_patterns({"workspaces": {"packages": ["libs/*"]}}),
            ["libs/*"],
        )

    def test_patterns_that_leave_the_repository_are_dropped(self) -> None:
        self.assertEqual(
            workspace_patterns({"workspaces": ["../evil/*", "/etc/*", "ok/*"]}),
            ["ok/*"],
        )

    def test_a_repository_declaring_no_workspaces_asks_for_nothing(self) -> None:
        self.assertEqual(workspace_patterns({}), [])
        self.assertEqual(workspace_patterns({"workspaces": "packages/*"}), [])

    def test_a_pattern_that_would_cost_exponential_matching_is_refused(self) -> None:
        """One ``**`` is a workspace layout; six is a manifest asking for work."""
        self.assertEqual(
            workspace_patterns({"workspaces": [
                "**/**/**/**/**/**/*", "packages/**", "ok/*",
            ]}),
            ["packages/**", "ok/*"],
        )

    def test_an_absurdly_deep_pattern_is_refused(self) -> None:
        deep = "/".join(["a"] * (MAX_PATTERN_SEGMENTS + 1))
        self.assertEqual(workspace_patterns({"workspaces": [deep, "ok/*"]}), ["ok/*"])


class MemberMap(unittest.TestCase):
    def _monorepo(self, root: Path) -> None:
        write(root, "package.json", manifest(
            name="acme", private=True, workspaces=["apps/*", "packages/*"]))
        write(root, "apps/web/package.json", manifest(name="@acme/web"))
        write(root, "apps/web/src/index.ts", "export const app = 1;\n")
        write(root, "packages/shared/package.json", manifest(
            name="@acme/shared", main="index.ts"))
        write(root, "packages/shared/index.ts", "export * from './money';\n")
        write(root, "packages/shared/money.ts", "export const m = 1;\n")

    def test_members_map_to_their_directory_and_entry_point(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._monorepo(root)
            members = load_workspace_members(root)
            self.assertEqual(
                sorted(members), ["@acme/shared", "@acme/web"],
            )
            self.assertEqual(
                members["@acme/shared"].entry, "packages/shared/index.ts"
            )
            self.assertEqual(members["@acme/shared"].directory, "packages/shared")
            # ``apps/web`` declares no entry point; the conventional
            # ``src/index`` fallback is what a source checkout actually holds.
            self.assertEqual(members["@acme/web"].entry, "apps/web/src/index.ts")

    def test_a_wildcard_in_a_directory_segment_still_finds_members(self) -> None:
        """The reason this map does not glob each pattern on its own.

        ``weld.glob_match._walk_one`` answers nothing for ``apps/*/x`` -- the
        parent it stats is the literal path ``apps/*``. A member map built by
        walking each declared pattern would therefore be empty for every
        monorepo in existence, silently.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._monorepo(root)
            self.assertIn("@acme/web", load_workspace_members(root))

    def test_a_nested_package_below_a_member_is_not_itself_a_member(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._monorepo(root)
            write(root, "packages/shared/vendor/inner/package.json",
                  manifest(name="@acme/inner"))
            self.assertNotIn("@acme/inner", load_workspace_members(root))

    def test_a_declared_entry_absent_from_the_checkout_falls_back(self) -> None:
        """``"main": "dist/index.js"`` before anything has been built."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "package.json", manifest(workspaces=["packages/*"]))
            write(root, "packages/ui/package.json", manifest(
                name="@acme/ui", main="dist/index.js"))
            write(root, "packages/ui/src/index.ts", "export const ui = 1;\n")
            members = load_workspace_members(root)
            self.assertEqual(members["@acme/ui"].entry, "packages/ui/src/index.ts")

    def test_the_exports_map_outranks_main(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "package.json", manifest(workspaces=["packages/*"]))
            write(root, "packages/ui/package.json", json.dumps({
                "name": "@acme/ui",
                "main": "legacy.ts",
                "exports": {".": {"types": "./src/entry.ts"}},
            }))
            write(root, "packages/ui/legacy.ts", "export const old = 1;\n")
            write(root, "packages/ui/src/entry.ts", "export const ui = 1;\n")
            members = load_workspace_members(root)
            self.assertEqual(members["@acme/ui"].entry, "packages/ui/src/entry.ts")

    def test_a_member_with_no_name_and_a_broken_manifest_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "package.json", manifest(workspaces=["packages/*"]))
            write(root, "packages/anon/package.json", manifest(version="1.0.0"))
            write(root, "packages/broken/package.json", "{not json")
            write(root, "packages/good/package.json", manifest(name="@acme/good"))
            self.assertEqual(sorted(load_workspace_members(root)), ["@acme/good"])

    def test_a_repository_that_is_not_a_workspace_costs_one_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "package.json", manifest(name="plain"))
            write(root, "packages/shared/package.json", manifest(name="@acme/x"))
            self.assertEqual(load_workspace_members(root), {})

    def test_a_symlinked_member_directory_is_not_followed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp) / "outside" / "evil"
            outside.mkdir(parents=True)
            (outside / "package.json").write_text(
                manifest(name="@acme/evil"), encoding="utf-8")
            root = Path(tmp) / "repo"
            root.mkdir()
            write(root, "package.json", manifest(workspaces=["packages/*"]))
            (root / "packages").mkdir()
            try:
                (root / "packages" / "evil").symlink_to(
                    outside, target_is_directory=True)
            except (OSError, NotImplementedError):  # pragma: no cover
                self.skipTest("symlinks unavailable on this filesystem")
            self.assertNotIn("@acme/evil", load_workspace_members(root))

    def test_node_modules_never_contributes_members(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root, "package.json", manifest(workspaces=["**"]))
            write(root, "node_modules/left-pad/package.json",
                  manifest(name="left-pad"))
            self.assertEqual(load_workspace_members(root), {})

    def test_an_unreadable_manifest_answers_empty(self) -> None:
        if os.geteuid() == 0:  # pragma: no cover - root ignores the mode bits
            self.skipTest("running as root; permission bits are not enforced")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = write(root, "package.json", manifest(workspaces=["packages/*"]))
            path.chmod(0o000)
            try:
                self.assertEqual(load_workspace_members(root), {})
            finally:
                path.chmod(0o644)


if __name__ == "__main__":
    unittest.main()
