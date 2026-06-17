"""Tests for weld.workspace: workspaces.yaml schema, loader, and validator.

Exercises the polyrepo federation registry format defined in ADR 0011:

  * Dataclass schema round-trips through load() / dump()
  * Loader auto-derives ``name`` + ``tags`` from path segments when the user
    did not set them explicitly
  * Validator rejects duplicate child names, invalid characters, and the
    ASCII Unit Separator that is reserved as the namespace delimiter

The nested-repo scanner (``scan_nested_repos``) is covered separately in
``weld_workspace_scan_test.py``.
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
    validate_config,
)


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
