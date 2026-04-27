"""Bootstrap regression tests for the gitignored-children case (bd-gpt4).

The FS scan in ``weld.workspace.scan_nested_repos`` previously folded the
root ``.gitignore`` into its exclusion set (originally for the bd-5038-rkt
publish-overlay case). Polyrepos whose children dir matched a gitignore
pattern (operator-added ``services/`` rule, or anything else listed at
the top level) were silently masked, sending ``wd workspace bootstrap``
to single-service mode and breaking ``wd workspace status`` permanently.

bd-gpt4 removes the gitignore fold from ``_normalised_exclude_paths``: a
nested ``.git`` directory is a workspace child by definition, regardless
of VCS-tracking state. These tests pin both the unit-level scanner
behaviour and the end-to-end bootstrap path.
"""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


def _git(repo_root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        env={"LC_ALL": "C", "PATH": "/usr/bin:/usr/local/bin:/bin"},
        check=True,
    )
    return proc.stdout.strip()


def _init_repo(repo_root: Path) -> Path:
    repo_root.mkdir(parents=True, exist_ok=True)
    _git(repo_root, "init", "-q")
    _git(repo_root, "config", "user.email", "test@example.com")
    _git(repo_root, "config", "user.name", "Weld Test")
    (repo_root / "README.md").write_text("# fixture\n", encoding="utf-8")
    _git(repo_root, "add", "README.md")
    _git(repo_root, "commit", "-q", "-m", "initial commit")
    return repo_root


def _wipe_weld_dir(root: Path) -> None:
    weld = root / ".weld"
    if weld.is_dir():
        shutil.rmtree(weld)


class GitignoreMaskTest(unittest.TestCase):
    """The gitignore-fold regression and its symmetry partners."""

    def test_scan_finds_children_when_gitignored(self) -> None:
        from weld.workspace import scan_nested_repos

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            (root / ".gitignore").write_text("services/\n", encoding="utf-8")
            _init_repo(root / "services" / "api")
            _init_repo(root / "services" / "auth")

            children = scan_nested_repos(root, max_depth=4)
            paths = sorted(c.path for c in children)
            self.assertEqual(paths, ["services/api", "services/auth"])

    def test_scan_finds_children_when_not_gitignored(self) -> None:
        from weld.workspace import scan_nested_repos

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            _init_repo(root / "services" / "api")
            _init_repo(root / "services" / "auth")

            children = scan_nested_repos(root, max_depth=4)
            paths = sorted(c.path for c in children)
            self.assertEqual(paths, ["services/api", "services/auth"])

    def test_full_wipe_bootstrap_regenerates_yaml_with_gitignored_children(
        self,
    ) -> None:
        """End-to-end: the user's exact v0.11.3 reproduction scenario.

        Before the fix, FS scan returned empty (gitignore-folded), bootstrap
        went single-service, yaml was never regenerated. After the fix all
        children are detected and workspace-state.json materialises.
        """
        from weld._workspace_bootstrap import bootstrap_workspace

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            (root / ".gitignore").write_text("services/\n", encoding="utf-8")
            _init_repo(root / "services" / "api")
            _init_repo(root / "services" / "auth")

            _wipe_weld_dir(root)
            self.assertFalse((root / ".weld").exists())

            result = bootstrap_workspace(root, max_depth=4)

            self.assertTrue((root / ".weld" / "workspaces.yaml").is_file())
            self.assertTrue((root / ".weld" / "workspace-state.json").is_file())
            for rel in ("services/api", "services/auth"):
                self.assertTrue(
                    (root / rel / ".weld" / "discover.yaml").is_file(),
                    f"per-child init must run inside {rel}",
                )
            self.assertEqual(
                sorted(result.children_present),
                ["services-api", "services-auth"],
            )

    def test_full_wipe_bootstrap_regenerates_yaml_without_gitignore(
        self,
    ) -> None:
        from weld._workspace_bootstrap import bootstrap_workspace

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            _init_repo(root / "services" / "api")
            _init_repo(root / "services" / "auth")

            _wipe_weld_dir(root)
            result = bootstrap_workspace(root, max_depth=4)

            self.assertTrue((root / ".weld" / "workspaces.yaml").is_file())
            self.assertTrue((root / ".weld" / "workspace-state.json").is_file())
            self.assertEqual(
                sorted(result.children_present),
                ["services-api", "services-auth"],
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
