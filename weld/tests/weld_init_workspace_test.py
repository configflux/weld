"""Tests for ``wd init`` workspaces.yaml scaffolding (polyrepo federation).

When ``wd init`` runs at a root that contains nested git repositories, it must
auto-scaffold ``.weld/workspaces.yaml`` using the schema from
:mod:`weld.workspace`. Idempotent: no overwrite without ``--force``.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld._yaml import parse_yaml  # noqa: E402
from weld.init_workspace import discover_children, init_workspace  # noqa: E402


def _make_git_repo(base: Path, rel: str) -> Path:
    path = base / rel
    path.mkdir(parents=True, exist_ok=True)
    (path / ".git").mkdir()
    return path


class DiscoverChildrenTest(unittest.TestCase):
    def test_discover_finds_nested_git_repos(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_git_repo(root, "services/api")
            _make_git_repo(root, "services/auth")
            _make_git_repo(root, "libs/shared")
            children = discover_children(root)
        names = sorted(c.name for c in children)
        self.assertEqual(names, ["libs-shared", "services-api", "services-auth"])

    def test_discover_honours_max_depth(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_git_repo(root, "a/b/c/deep")  # depth 4
            shallow = discover_children(root, max_depth=2)
        self.assertEqual(shallow, [])

    def test_discover_empty_when_no_children(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "just_a_dir").mkdir()
            children = discover_children(root)
        self.assertEqual(children, [])


class InitWorkspaceTest(unittest.TestCase):
    def test_init_writes_workspaces_yaml_when_children_exist(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_git_repo(root, "services/api")
            _make_git_repo(root, "services/auth")
            out = root / ".weld" / "workspaces.yaml"
            wrote = init_workspace(root, out)
            self.assertTrue(wrote, "init_workspace must return True when writing")
            self.assertTrue(out.is_file())
            data = parse_yaml(out.read_text(encoding="utf-8"))
        self.assertEqual(data["version"], 1)
        self.assertEqual(len(data["children"]), 2)
        paths = sorted(c["path"] for c in data["children"])
        self.assertEqual(paths, ["services/api", "services/auth"])

    def test_init_skips_when_no_children(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src").mkdir()  # no nested git repos
            out = root / ".weld" / "workspaces.yaml"
            wrote = init_workspace(root, out)
        self.assertFalse(wrote, "init_workspace must not write when no children")
        self.assertFalse(out.exists())

    def test_init_is_idempotent_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_git_repo(root, "services/api")
            out = root / ".weld" / "workspaces.yaml"

            self.assertTrue(init_workspace(root, out))
            original = out.read_text(encoding="utf-8")

            # Add a new nested repo, re-run without force -- file must not change.
            _make_git_repo(root, "services/auth")
            wrote = init_workspace(root, out, force=False)
            self.assertFalse(wrote, "must refuse to overwrite without --force")
            self.assertEqual(out.read_text(encoding="utf-8"), original)

    def test_init_force_overwrites(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_git_repo(root, "services/api")
            out = root / ".weld" / "workspaces.yaml"
            init_workspace(root, out)

            _make_git_repo(root, "services/auth")
            wrote = init_workspace(root, out, force=True)
            self.assertTrue(wrote)
            data = parse_yaml(out.read_text(encoding="utf-8"))
        paths = sorted(c["path"] for c in data["children"])
        self.assertEqual(paths, ["services/api", "services/auth"])

    def test_init_respects_max_depth(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_git_repo(root, "a/b/c/deep")  # depth 4
            out = root / ".weld" / "workspaces.yaml"
            wrote = init_workspace(root, out, max_depth=2)
        self.assertFalse(wrote)
        self.assertFalse(out.exists())

    def test_init_writes_scan_block_with_configured_max_depth(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_git_repo(root, "services/api")
            out = root / ".weld" / "workspaces.yaml"
            init_workspace(root, out, max_depth=3)
            data = parse_yaml(out.read_text(encoding="utf-8"))
        self.assertEqual(data["scan"]["max_depth"], 3)


class MergeYamlAndScanChildrenTest(unittest.TestCase):
    """bd-...-9slg: yaml-authoritative + scan-augmenting merge predicate."""

    def test_force_false_with_existing_yaml_reads_yaml_via_merge(self) -> None:
        """Existing yaml + scan empty -> merge surfaces yaml children.

        Before the fix, ``init_workspace`` returned False on existing
        yaml without reading it (treating presence as a no-op), and the
        bootstrap orchestrator's FS-only re-scan masked out yaml-listed
        children that the scan could not reach. The merge helper is the
        unified replacement; this test pins its read-yaml-when-present
        behaviour.
        """
        from weld.init_workspace import merge_yaml_and_scan_children

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            yaml_text = (
                "version: 1\n"
                "scan:\n"
                "  max_depth: 4\n"
                "  exclude_paths: []\n"
                "children:\n"
                "  - name: services-api\n"
                "    path: services/api\n"
                "cross_repo_strategies: []\n"
            )
            (root / ".weld").mkdir(parents=True, exist_ok=True)
            (root / ".weld" / "workspaces.yaml").write_text(
                yaml_text, encoding="utf-8",
            )

            merged = merge_yaml_and_scan_children(root)

            paths = sorted(c.path for c in merged.children)
            self.assertEqual(paths, ["services/api"])
            self.assertIsNone(merged.yaml_error)


class CliIntegrationTest(unittest.TestCase):
    """``wd init`` end-to-end: scaffolds both discover.yaml and workspaces.yaml."""

    def test_wd_init_scaffolds_workspaces_yaml_when_nested_repos_exist(self) -> None:
        from weld.init import main as init_main

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # wd init scans for python etc, but with an empty tree it will
            # still produce a stub discover.yaml. We just care about the
            # workspaces.yaml branch, so give the root a nested git repo.
            _make_git_repo(root, "services/api")
            _make_git_repo(root, "services/auth")

            init_main([str(root), "--force"])

            workspaces = root / ".weld" / "workspaces.yaml"
            self.assertTrue(
                workspaces.is_file(),
                "wd init must create .weld/workspaces.yaml at a polyrepo root",
            )
            data = parse_yaml(workspaces.read_text(encoding="utf-8"))
            self.assertEqual(len(data["children"]), 2)

    def test_wd_init_skips_workspaces_yaml_when_no_nested_repos(self) -> None:
        from weld.init import main as init_main

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src").mkdir()
            init_main([str(root), "--force"])
            self.assertFalse((root / ".weld" / "workspaces.yaml").exists())

    def test_wd_init_max_depth_flag_threaded_through(self) -> None:
        from weld.init import main as init_main

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_git_repo(root, "a/b/c/deep")
            init_main([str(root), "--force", "--max-depth", "2"])
            self.assertFalse((root / ".weld" / "workspaces.yaml").exists())


class GitignoreSkipTest(unittest.TestCase):
    """Federation auto-scan honours the root repository's .gitignore.

    Regression for bd-5038-rkt: running ``wd init`` at a repo whose root
    ``.gitignore`` excludes a nested git clone (e.g. ``/public/`` for the
    publish overlay) must NOT register that clone as a federation child.
    """

    def test_gitignored_nested_repo_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".gitignore").write_text("/public/\n", encoding="utf-8")
            _make_git_repo(root, "public")
            children = discover_children(root)
        self.assertEqual(children, [])

    def test_gitignored_deeper_nested_repo_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".gitignore").write_text(
                "tools/publish_overlays/\n", encoding="utf-8",
            )
            _make_git_repo(root, "tools/publish_overlays/pypi")
            children = discover_children(root)
        self.assertEqual(children, [])

    def test_non_gitignored_sibling_still_registered(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".gitignore").write_text("/public/\n", encoding="utf-8")
            _make_git_repo(root, "public")  # gitignored -> skipped
            _make_git_repo(root, "services/api")  # not ignored -> kept
            children = discover_children(root)
        names = sorted(c.name for c in children)
        self.assertEqual(names, ["services-api"])

    def test_absent_gitignore_falls_back_to_normal_scan(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # No .gitignore at the root -- the scanner must not invent a skip.
            _make_git_repo(root, "public")
            children = discover_children(root)
        names = sorted(c.name for c in children)
        self.assertEqual(names, ["public"])

    def test_negation_entries_do_not_skip(self) -> None:
        # Fail-safe: a negated pattern must not cause the scanner to skip.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".gitignore").write_text(
                "!/public/\n", encoding="utf-8",
            )
            _make_git_repo(root, "public")
            children = discover_children(root)
        names = sorted(c.name for c in children)
        self.assertEqual(names, ["public"])

    def test_wd_init_skips_gitignored_nested_repos(self) -> None:
        from weld.init import main as init_main

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".gitignore").write_text("/public/\n", encoding="utf-8")
            _make_git_repo(root, "public")
            init_main([str(root), "--force"])
            workspaces = root / ".weld" / "workspaces.yaml"
            self.assertFalse(
                workspaces.exists(),
                "wd init must NOT register a gitignored nested repo as a child",
            )


if __name__ == "__main__":
    unittest.main()
