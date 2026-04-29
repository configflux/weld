"""Tests for ``wd workspace bootstrap`` scan-exclusion plumbing.

A polyrepo workspace may legitimately contain nested git repos under
operational directories (quarantine / archive / temp recovery) that
should not enter the workspace child registry. Bootstrap honors the
exclusion surface configured in ``workspaces.yaml`` (``scan.exclude_paths``)
and the ``--exclude-path`` CLI flag, persists those exclusions across
rewrites, and as a defense-in-depth filters scan-only entries whose
auto-derived child name would fail the workspace name validator
(``^[A-Za-z0-9_-]+$``).
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld._workspace_bootstrap import bootstrap_workspace  # noqa: E402
from weld._yaml import parse_yaml  # noqa: E402
from weld.init_workspace import merge_yaml_and_scan_children  # noqa: E402


def _git(repo_root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        env={"LC_ALL": "C", "PATH": "/usr/bin:/usr/local/bin:/bin"},
        check=True,
    )


def _init_repo(repo_root: Path) -> None:
    repo_root.mkdir(parents=True, exist_ok=True)
    _git(repo_root, "init", "-q")
    _git(repo_root, "config", "user.email", "test@example.com")
    _git(repo_root, "config", "user.name", "Weld Test")
    (repo_root / "README.md").write_text("# fixture\n", encoding="utf-8")
    _git(repo_root, "add", "README.md")
    _git(repo_root, "commit", "-q", "-m", "init")


class MergeHonorsYamlExcludePathsTest(unittest.TestCase):
    """`merge_yaml_and_scan_children` defaults exclusions from yaml."""

    def test_yaml_scan_exclude_paths_filters_fs_scan(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root / "good")
            _init_repo(root / ".repo-quarantine" / "stale" / "repo")
            (root / ".weld").mkdir(parents=True, exist_ok=True)
            (root / ".weld" / "workspaces.yaml").write_text(
                "version: 1\n"
                "scan:\n"
                "  max_depth: 4\n"
                "  exclude_paths:\n"
                "    - .repo-quarantine\n"
                "children:\n"
                "  - name: good\n"
                "    path: good\n"
                "cross_repo_strategies: []\n",
                encoding="utf-8",
            )

            merged = merge_yaml_and_scan_children(root)

            paths = sorted(c.path for c in merged.children)
            self.assertEqual(paths, ["good"])
            self.assertEqual(merged.excluded_by_invalid_name, [])

    def test_invalid_scan_name_filtered_and_surfaced(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root / "good")
            _init_repo(root / ".repo-quarantine" / "x.y" / "repo")

            merged = merge_yaml_and_scan_children(root)

            paths = sorted(c.path for c in merged.children)
            self.assertEqual(paths, ["good"])
            self.assertEqual(
                merged.excluded_by_invalid_name,
                [".repo-quarantine/x.y/repo"],
            )

    def test_caller_excludes_unioned_with_yaml(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root / "good")
            _init_repo(root / "vendor-mirror" / "repo")
            _init_repo(root / "ops" / "repo")
            (root / ".weld").mkdir(parents=True, exist_ok=True)
            (root / ".weld" / "workspaces.yaml").write_text(
                "version: 1\n"
                "scan:\n"
                "  max_depth: 4\n"
                "  exclude_paths:\n"
                "    - vendor-mirror\n"
                "children:\n"
                "  - name: good\n"
                "    path: good\n"
                "cross_repo_strategies: []\n",
                encoding="utf-8",
            )

            merged = merge_yaml_and_scan_children(root, exclude_paths=["ops"])

            paths = sorted(c.path for c in merged.children)
            self.assertEqual(paths, ["good"])


class BootstrapExcludePersistenceTest(unittest.TestCase):
    """`bootstrap_workspace` honors and persists scan exclusions."""

    def test_bootstrap_exclude_path_persists_into_yaml(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root / "good")
            _init_repo(root / ".repo-quarantine" / "x.y" / "repo")

            result = bootstrap_workspace(
                root, exclude_paths=[".repo-quarantine"],
            )

            self.assertEqual(result.children_discovered, ["good"])
            self.assertEqual(result.children_present, ["good"])
            yaml_text = (root / ".weld" / "workspaces.yaml").read_text(
                encoding="utf-8",
            )
            data = parse_yaml(yaml_text)
            self.assertIn(
                ".repo-quarantine",
                data["scan"]["exclude_paths"],
                "CLI-supplied exclusion must persist into workspaces.yaml",
            )

    def test_second_run_without_flag_uses_persisted_exclusion(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root / "good")
            _init_repo(root / ".repo-quarantine" / "x.y" / "repo")

            bootstrap_workspace(root, exclude_paths=[".repo-quarantine"])
            result = bootstrap_workspace(root)

            self.assertEqual(result.children_discovered, ["good"])
            self.assertEqual(
                result.excluded_by_invalid_name,
                [],
                "yaml-persisted exclusion must keep .repo-quarantine "
                "out of the scan, so no invalid-name diagnostic fires",
            )

    def test_bootstrap_skips_invalid_scan_name_without_aborting(self) -> None:
        """Defense-in-depth: bootstrap completes when no exclusion is set."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root / "good")
            _init_repo(root / ".repo-quarantine" / "x.y" / "repo")

            result = bootstrap_workspace(root)

            self.assertEqual(result.children_discovered, ["good"])
            self.assertEqual(result.children_present, ["good"])
            self.assertEqual(
                result.excluded_by_invalid_name,
                [".repo-quarantine/x.y/repo"],
            )

    def test_bootstrap_preserves_cross_repo_strategies_on_rewrite(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root / "good")
            (root / ".weld").mkdir(parents=True, exist_ok=True)
            (root / ".weld" / "workspaces.yaml").write_text(
                "version: 1\n"
                "scan:\n"
                "  max_depth: 4\n"
                "  exclude_paths: []\n"
                "children:\n"
                "  - name: good\n"
                "    path: good\n"
                "cross_repo_strategies:\n"
                "  - grpc_service_binding\n",
                encoding="utf-8",
            )

            bootstrap_workspace(root, exclude_paths=["ops"])

            data = parse_yaml(
                (root / ".weld" / "workspaces.yaml").read_text(
                    encoding="utf-8",
                ),
            )
            self.assertEqual(
                data["cross_repo_strategies"],
                ["grpc_service_binding"],
                "rewrite must not silently drop cross_repo_strategies",
            )


if __name__ == "__main__":  # pragma: no cover -- manual invocation only
    unittest.main()
