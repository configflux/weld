"""Tests for weld.workspace nested-repo scanning (``scan_nested_repos``).

Exercises the polyrepo federation scanner defined in ADR 0011: it walks a
directory tree, stops descending at the first ``.git`` it finds, honours
``exclude_paths`` (additive to the built-in boundary exclusions), and
auto-derives ``name`` + ``tags`` from path segments. The config-format
schema/loader/dumper/validator tests live in
``weld_workspace_config_test.py``.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from weld.workspace import scan_nested_repos


def _make_repo(base: Path, rel: str) -> Path:
    path = base / rel
    path.mkdir(parents=True, exist_ok=True)
    (path / ".git").mkdir()
    return path


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


if __name__ == "__main__":
    unittest.main()
