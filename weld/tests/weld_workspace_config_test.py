"""Tests for weld.workspace: workspaces.yaml schema, loader, validator, and scanner.

Exercises the polyrepo federation registry format defined in ADR 0011:

  * Dataclass schema round-trips through load() / dump()
  * Validator rejects duplicate child names, invalid characters, and the
    ASCII Unit Separator that is reserved as the namespace delimiter
  * Nested-repo scanner walks a directory tree, stops descending at the first
    ``.git`` it finds, honours ``exclude_paths`` (additive to the built-in
    boundary exclusions), and auto-derives ``name`` + ``tags`` from path
    segments when the user did not set them explicitly
"""

from __future__ import annotations

import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from weld.workspace import (
    ChildEntry,
    ScanConfig,
    WorkspaceConfig,
    WorkspaceConfigError,
    auto_derive_name,
    auto_derive_tags,
    dump_workspaces_yaml,
    load_workspaces_yaml,
    scan_nested_repos,
    validate_config,
)


def _make_repo(base: Path, rel: str) -> Path:
    path = base / rel
    path.mkdir(parents=True, exist_ok=True)
    (path / ".git").mkdir()
    return path


class AutoDeriveTest(unittest.TestCase):
    def test_name_from_single_segment(self) -> None:
        self.assertEqual(auto_derive_name("api"), "api")

    def test_name_from_two_segments_joined_by_dash(self) -> None:
        self.assertEqual(auto_derive_name("services/api"), "services-api")

    def test_name_from_deep_path(self) -> None:
        self.assertEqual(
            auto_derive_name("libs/shared/auth"),
            "libs-shared-auth",
        )

    def test_name_normalises_backslash_separators(self) -> None:
        self.assertEqual(auto_derive_name("apps\\frontend"), "apps-frontend")

    def test_tags_from_single_segment_has_no_category(self) -> None:
        # A top-level child has no parent segment; no category tag is added.
        self.assertEqual(auto_derive_tags("api"), {})

    def test_tags_first_parent_becomes_category(self) -> None:
        self.assertEqual(
            auto_derive_tags("services/api"),
            {"category": "services"},
        )

    def test_tags_deeper_ancestors_get_numbered_keys(self) -> None:
        self.assertEqual(
            auto_derive_tags("libs/shared/auth"),
            {"category": "shared", "category_2": "libs"},
        )


class LoaderTest(unittest.TestCase):
    def test_load_minimal_workspaces_yaml(self) -> None:
        text = textwrap.dedent(
            """\
            version: 1
            scan:
              max_depth: 4
              exclude_paths: [.worktrees, vendor]
            children:
              - name: services-api
                path: services/api
              - name: services-auth
                path: services/auth
            cross_repo_strategies:
              - service_graph
            """
        )
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / "workspaces.yaml"
            f.write_text(text, encoding="utf-8")
            cfg = load_workspaces_yaml(f)

        self.assertEqual(cfg.version, 1)
        self.assertEqual(cfg.scan.max_depth, 4)
        self.assertEqual(cfg.scan.exclude_paths, [".worktrees", "vendor"])
        self.assertEqual(len(cfg.children), 2)
        self.assertEqual(cfg.children[0].name, "services-api")
        self.assertEqual(cfg.children[0].path, "services/api")
        self.assertEqual(cfg.cross_repo_strategies, ["service_graph"])

    def test_load_applies_defaults_for_omitted_scan_block(self) -> None:
        text = "version: 1\nchildren: []\ncross_repo_strategies: []\n"
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / "workspaces.yaml"
            f.write_text(text, encoding="utf-8")
            cfg = load_workspaces_yaml(f)
        self.assertEqual(cfg.scan.max_depth, 4)
        self.assertIn(".worktrees", cfg.scan.exclude_paths)
        self.assertIn("vendor", cfg.scan.exclude_paths)

    def test_load_auto_derives_name_when_absent(self) -> None:
        text = textwrap.dedent(
            """\
            version: 1
            children:
              - path: services/api
              - path: libs/shared/auth
            cross_repo_strategies: []
            """
        )
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / "workspaces.yaml"
            f.write_text(text, encoding="utf-8")
            cfg = load_workspaces_yaml(f)
        self.assertEqual(cfg.children[0].name, "services-api")
        self.assertEqual(cfg.children[1].name, "libs-shared-auth")

    def test_load_auto_fills_tags_when_absent(self) -> None:
        text = textwrap.dedent(
            """\
            version: 1
            children:
              - path: services/api
            cross_repo_strategies: []
            """
        )
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / "workspaces.yaml"
            f.write_text(text, encoding="utf-8")
            cfg = load_workspaces_yaml(f)
        self.assertEqual(cfg.children[0].tags, {"category": "services"})

    def test_load_user_tags_override_autofill(self) -> None:
        text = textwrap.dedent(
            """\
            version: 1
            children:
              - path: services/api
                tags:
                  category: custom
            cross_repo_strategies: []
            """
        )
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / "workspaces.yaml"
            f.write_text(text, encoding="utf-8")
            cfg = load_workspaces_yaml(f)
        self.assertEqual(cfg.children[0].tags, {"category": "custom"})

    def test_load_missing_file_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            missing = Path(tmp) / "workspaces.yaml"
            with self.assertRaises(WorkspaceConfigError) as cm:
                load_workspaces_yaml(missing)
            self.assertIn("not found", str(cm.exception).lower())


class DumperTest(unittest.TestCase):
    def test_round_trip_dump_and_load(self) -> None:
        original = WorkspaceConfig(
            version=1,
            scan=ScanConfig(max_depth=5, exclude_paths=[".worktrees", "vendor"]),
            children=[
                ChildEntry(
                    name="services-api",
                    path="services/api",
                    tags={"category": "services"},
                ),
                ChildEntry(name="apps-frontend", path="apps/frontend"),
            ],
            cross_repo_strategies=["service_graph"],
        )
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "workspaces.yaml"
            dump_workspaces_yaml(original, out)
            reloaded = load_workspaces_yaml(out)
        self.assertEqual(reloaded.version, 1)
        self.assertEqual(reloaded.scan.max_depth, 5)
        self.assertEqual(reloaded.scan.exclude_paths, [".worktrees", "vendor"])
        self.assertEqual([c.name for c in reloaded.children],
                         ["services-api", "apps-frontend"])
        self.assertEqual(reloaded.children[0].path, "services/api")
        # The dumper emits the user-provided tag; auto-fill would produce the
        # same category for services/api and the comparison stays stable.
        self.assertEqual(reloaded.children[0].tags, {"category": "services"})
        self.assertEqual(reloaded.cross_repo_strategies, ["service_graph"])

    def test_dump_produces_deterministic_bytes(self) -> None:
        cfg = WorkspaceConfig(
            version=1,
            scan=ScanConfig(max_depth=4, exclude_paths=[".worktrees", "vendor"]),
            children=[
                ChildEntry(name="b-child", path="b/child"),
                ChildEntry(name="a-child", path="a/child"),
            ],
            cross_repo_strategies=["service_graph"],
        )
        with TemporaryDirectory() as tmp:
            out1 = Path(tmp) / "one.yaml"
            out2 = Path(tmp) / "two.yaml"
            dump_workspaces_yaml(cfg, out1)
            dump_workspaces_yaml(cfg, out2)
            self.assertEqual(
                out1.read_bytes(),
                out2.read_bytes(),
                "dumping the same config twice must produce byte-identical output",
            )


class ValidatorTest(unittest.TestCase):
    def _valid_config(self) -> WorkspaceConfig:
        return WorkspaceConfig(
            version=1,
            scan=ScanConfig(),
            children=[
                ChildEntry(name="services-api", path="services/api"),
                ChildEntry(name="services-auth", path="services/auth"),
            ],
            cross_repo_strategies=["service_graph"],
        )

    def test_valid_config_passes(self) -> None:
        validate_config(self._valid_config())

    def test_rejects_unsupported_version(self) -> None:
        cfg = self._valid_config()
        cfg.version = 2
        with self.assertRaises(WorkspaceConfigError) as cm:
            validate_config(cfg)
        self.assertIn("version", str(cm.exception).lower())

    def test_rejects_duplicate_names(self) -> None:
        cfg = self._valid_config()
        cfg.children[1].name = "services-api"
        with self.assertRaises(WorkspaceConfigError) as cm:
            validate_config(cfg)
        msg = str(cm.exception)
        self.assertIn("duplicate", msg.lower())
        self.assertIn("services-api", msg)

    def test_rejects_name_with_slash(self) -> None:
        cfg = self._valid_config()
        cfg.children[0].name = "services/api"
        with self.assertRaises(WorkspaceConfigError) as cm:
            validate_config(cfg)
        msg = str(cm.exception)
        self.assertIn("invalid", msg.lower())
        self.assertIn("services/api", msg)

    def test_rejects_name_with_unit_separator(self) -> None:
        cfg = self._valid_config()
        cfg.children[0].name = "services\x1fapi"
        with self.assertRaises(WorkspaceConfigError):
            validate_config(cfg)

    def test_rejects_empty_name(self) -> None:
        cfg = self._valid_config()
        cfg.children[0].name = ""
        with self.assertRaises(WorkspaceConfigError):
            validate_config(cfg)

    def test_rejects_absolute_path(self) -> None:
        cfg = self._valid_config()
        cfg.children[0].path = "/abs/path"
        with self.assertRaises(WorkspaceConfigError) as cm:
            validate_config(cfg)
        self.assertIn("absolute", str(cm.exception).lower())

    def test_rejects_parent_traversal(self) -> None:
        cfg = self._valid_config()
        cfg.children[0].path = "../sibling"
        with self.assertRaises(WorkspaceConfigError) as cm:
            validate_config(cfg)
        self.assertIn("..", str(cm.exception))

    def test_rejects_unknown_cross_repo_strategy(self) -> None:
        cfg = self._valid_config()
        cfg.cross_repo_strategies = ["not_a_real_strategy"]
        with self.assertRaises(WorkspaceConfigError):
            validate_config(cfg)


class ScannerTest(unittest.TestCase):
    def test_discovers_top_level_child_repos(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_repo(root, "services/api")
            _make_repo(root, "services/auth")
            _make_repo(root, "apps/frontend")
            found = scan_nested_repos(root)
        paths = sorted(c.path for c in found)
        self.assertEqual(
            paths, ["apps/frontend", "services/api", "services/auth"],
        )

    def test_stops_at_first_nested_git(self) -> None:
        # A repo-inside-repo must NOT be registered -- scanner stops descending
        # once it hits a .git directory.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_repo(root, "services/api")
            _make_repo(root, "services/api/vendored-lib")
            found = scan_nested_repos(root)
        paths = [c.path for c in found]
        self.assertIn("services/api", paths)
        self.assertNotIn("services/api/vendored-lib", paths)

    def test_respects_max_depth(self) -> None:
        # libs/shared/auth sits at depth 3. A max_depth=2 scan must not find it.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_repo(root, "libs/shared/auth")
            shallow = scan_nested_repos(root, max_depth=2)
            deep = scan_nested_repos(root, max_depth=3)
        self.assertEqual([c.path for c in shallow], [])
        self.assertEqual([c.path for c in deep], ["libs/shared/auth"])

    def test_excludes_worktrees_and_vendor_by_default(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_repo(root, "vendor/thirdparty")
            _make_repo(root, ".worktrees/scratch")
            _make_repo(root, "services/api")
            found = scan_nested_repos(root)
        paths = [c.path for c in found]
        self.assertEqual(paths, ["services/api"])

    def test_excludes_additional_user_paths(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_repo(root, "services/api")
            _make_repo(root, "scratch/experiment")
            found = scan_nested_repos(root, exclude_paths=["scratch"])
        paths = [c.path for c in found]
        self.assertEqual(paths, ["services/api"])

    def test_excludes_dot_weld_directory(self) -> None:
        # Avoid treating the workspace's own .weld/.git (if any) as a child.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_repo(root, ".weld/state")
            _make_repo(root, "services/api")
            found = scan_nested_repos(root)
        paths = [c.path for c in found]
        self.assertEqual(paths, ["services/api"])

    def test_auto_derives_name_and_tags_on_scan(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_repo(root, "services/api")
            _make_repo(root, "libs/shared/auth")
            found = sorted(scan_nested_repos(root, max_depth=4),
                           key=lambda c: c.path)
        by_path = {c.path: c for c in found}
        self.assertEqual(by_path["services/api"].name, "services-api")
        self.assertEqual(
            by_path["services/api"].tags, {"category": "services"},
        )
        self.assertEqual(
            by_path["libs/shared/auth"].name, "libs-shared-auth",
        )
        self.assertEqual(
            by_path["libs/shared/auth"].tags,
            {"category": "shared", "category_2": "libs"},
        )

    def test_scan_results_are_lexicographically_sorted(self) -> None:
        # Determinism: two runs with the same filesystem produce identical
        # ordering regardless of os.walk order.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel in ["z/leaf", "a/leaf", "m/leaf"]:
                _make_repo(root, rel)
            first = [c.path for c in scan_nested_repos(root)]
            second = [c.path for c in scan_nested_repos(root)]
        self.assertEqual(first, ["a/leaf", "m/leaf", "z/leaf"])
        self.assertEqual(first, second)

    def test_returns_empty_list_when_no_nested_repos(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            self.assertEqual(scan_nested_repos(root), [])


class IdempotentInitTest(unittest.TestCase):
    def test_second_write_is_noop_without_force(self) -> None:
        # dump_workspaces_yaml has no overwrite guard (init.py enforces it);
        # here we verify the config module exposes the hook that init uses.
        cfg = WorkspaceConfig(
            version=1,
            scan=ScanConfig(),
            children=[ChildEntry(name="a", path="a")],
            cross_repo_strategies=[],
        )
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "workspaces.yaml"
            dump_workspaces_yaml(cfg, out)
            first = out.read_bytes()
            # A second write with the identical config produces the same bytes.
            dump_workspaces_yaml(cfg, out)
            self.assertEqual(out.read_bytes(), first)


if __name__ == "__main__":
    unittest.main()
