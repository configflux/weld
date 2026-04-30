"""Tests for workspace scan gitignore and glob exclusion behavior."""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from weld._yaml import parse_yaml
from weld.discover import discover
from weld.init_workspace import merge_yaml_and_scan_children
from weld.workspace import (
    WorkspaceConfigError,
    load_workspaces_yaml,
    scan_nested_repos,
    scan_nested_repos_with_diagnostics,
)
from weld.workspace_state import main as workspace_main


def _git(repo_root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        env={"LC_ALL": "C", "PATH": "/usr/bin:/usr/local/bin:/bin"},
        check=True,
    )


def _init_git(repo_root: Path) -> None:
    repo_root.mkdir(parents=True, exist_ok=True)
    _git(repo_root, "init", "-q")
    _git(repo_root, "config", "user.email", "test@example.com")
    _git(repo_root, "config", "user.name", "Weld Test")


def _init_repo(repo_root: Path) -> None:
    _init_git(repo_root)
    (repo_root / "README.md").write_text("# fixture\n", encoding="utf-8")
    _git(repo_root, "add", "README.md")
    _git(repo_root, "commit", "-q", "-m", "init")


class WorkspaceScanGitignoreTest(unittest.TestCase):
    def test_default_scan_keeps_gitignored_child_repo(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_git(root)
            (root / ".gitignore").write_text("services/\n", encoding="utf-8")
            _init_repo(root / "services" / "api")

            children = scan_nested_repos(root)

        self.assertEqual([child.path for child in children], ["services/api"])

    def test_respect_gitignore_skips_scan_only_child_repo(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_git(root)
            (root / ".gitignore").write_text("services/\n", encoding="utf-8")
            _init_repo(root / "services" / "api")

            result = scan_nested_repos_with_diagnostics(
                root, respect_gitignore=True,
            )

        self.assertEqual(result.children, [])
        self.assertEqual(result.skipped_by_gitignore, ["services/api"])

    def test_yaml_listed_gitignored_child_still_wins(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_git(root)
            (root / ".gitignore").write_text("services/\n", encoding="utf-8")
            _init_repo(root / "services" / "api")
            (root / ".weld").mkdir()
            (root / ".weld" / "workspaces.yaml").write_text(
                textwrap.dedent(
                    """\
                    version: 1
                    scan:
                      max_depth: 4
                      respect_gitignore: true
                      exclude_paths: []
                    children:
                      - name: services-api
                        path: services/api
                    cross_repo_strategies: []
                    """
                ),
                encoding="utf-8",
            )

            merged = merge_yaml_and_scan_children(root)

        self.assertEqual([child.path for child in merged.children], ["services/api"])
        self.assertEqual(merged.excluded_by_gitignore, ["services-api"])
        self.assertEqual(merged.skipped_by_gitignore, ["services/api"])


class WorkspaceScanExcludeGlobTest(unittest.TestCase):
    def test_scan_exclude_paths_support_names_paths_star_and_globstar(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel in (
                "services/api",
                "scratch/experiment",
                "ops/archive/stale",
                "generated/x/cache.tmp",
                "generated/x/keep",
            ):
                _init_repo(root / rel)

            children = scan_nested_repos(
                root,
                max_depth=4,
                exclude_paths=[
                    "scratch/experiment",
                    "ops/**",
                    "generated/**/*.tmp",
                ],
            )

        self.assertEqual(
            [child.path for child in children],
            ["generated/x/keep", "services/api"],
        )


class WorkspaceConfigRespectGitignoreTest(unittest.TestCase):
    def test_load_respect_gitignore_flag(self) -> None:
        text = textwrap.dedent(
            """\
            version: 1
            scan:
              max_depth: 4
              respect_gitignore: true
              exclude_paths: []
            children: []
            cross_repo_strategies: []
            """
        )
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / "workspaces.yaml"
            f.write_text(text, encoding="utf-8")
            cfg = load_workspaces_yaml(f)
        self.assertTrue(cfg.scan.respect_gitignore)

    def test_rejects_non_boolean_respect_gitignore(self) -> None:
        text = textwrap.dedent(
            """\
            version: 1
            scan:
              max_depth: 4
              respect_gitignore: "yes"
              exclude_paths: []
            children: []
            cross_repo_strategies: []
            """
        )
        with TemporaryDirectory() as tmp:
            f = Path(tmp) / "workspaces.yaml"
            f.write_text(text, encoding="utf-8")
            with self.assertRaises(WorkspaceConfigError) as cm:
                load_workspaces_yaml(f)
        self.assertIn("respect_gitignore", str(cm.exception))


class WorkspaceScanCliTest(unittest.TestCase):
    def test_wd_init_respect_gitignore_writes_flag_and_filters_children(self) -> None:
        from weld.init import main as init_main

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_git(root)
            (root / ".gitignore").write_text("services/\n", encoding="utf-8")
            _init_repo(root / "services" / "api")
            _init_repo(root / "public")

            init_main([str(root), "--force", "--respect-gitignore"])

            data = parse_yaml(
                (root / ".weld" / "workspaces.yaml").read_text(
                    encoding="utf-8",
                ),
            )

        self.assertTrue(data["scan"]["respect_gitignore"])
        self.assertEqual([child["path"] for child in data["children"]], ["public"])

    def test_bootstrap_json_reports_gitignore_skipped_paths(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_git(root)
            (root / ".gitignore").write_text("services/\n", encoding="utf-8")
            _init_repo(root / "services" / "api")
            _init_repo(root / "public")

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = workspace_main([
                    "bootstrap",
                    "--root",
                    str(root),
                    "--respect-gitignore",
                    "--json",
                ])

            payload = json.loads(stdout.getvalue())
            data = parse_yaml(
                (root / ".weld" / "workspaces.yaml").read_text(
                    encoding="utf-8",
                ),
            )

        self.assertEqual(code, 0)
        self.assertEqual(payload["children_discovered"], ["public"])
        self.assertEqual(payload["skipped_by_gitignore"], ["services/api"])
        self.assertTrue(data["scan"]["respect_gitignore"])


class SourceExcludeGlobRegressionTest(unittest.TestCase):
    def test_source_exclude_matches_folder_and_extension_globs(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_git(root)
            for rel in (
                "src/app.py",
                "src/vendor/skip.py",
                "src/generated/model.tmp.py",
                "src/generated/deep/model.tmp.py",
                "src/generated/keep.py",
            ):
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("def marker():\n    return True\n", encoding="utf-8")
            (root / ".weld").mkdir()
            (root / ".weld" / "discover.yaml").write_text(
                textwrap.dedent(
                    """\
                    sources:
                      - glob: "src/**/*.py"
                        type: file
                        strategy: python_module
                        exclude:
                          - "src/vendor/**"
                          - "src/generated/*.tmp.py"
                          - "src/generated/**/*.tmp.py"
                    """
                ),
                encoding="utf-8",
            )

            graph = discover(root)

        files = {
            node["props"]["file"]
            for node in graph["nodes"].values()
            if node["type"] == "file"
        }
        self.assertEqual(files, {"src/app.py", "src/generated/keep.py"})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
