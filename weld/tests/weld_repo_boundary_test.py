"""Regression tests for weld's Git-visible repo boundary behavior."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.discover import discover  # noqa: E402
from weld.file_index import build_file_index  # noqa: E402
from weld.glob_match import matches_exclude  # noqa: E402
from weld.strategies._helpers import filter_glob_results  # noqa: E402

def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=check,
        capture_output=True,
        text=True,
    )

class GitVisibleBoundaryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.root = Path(self.tmpdir) / "repo"
        self.root.mkdir(parents=True)
        _git(self.root, "init", "-q")

        (self.root / ".gitignore").write_text("ignored/\n", encoding="utf-8")

        src_dir = self.root / "src"
        src_dir.mkdir()
        (src_dir / "app.py").write_text("def app():\n    return True\n", encoding="utf-8")

        ignored_dir = self.root / "ignored"
        ignored_dir.mkdir()
        (ignored_dir / "tracked.py").write_text(
            "def tracked_visible():\n    return True\n",
            encoding="utf-8",
        )
        (ignored_dir / "untracked.py").write_text(
            "def ignored_untracked():\n    return True\n",
            encoding="utf-8",
        )

        _git(self.root, "add", ".gitignore", "src/app.py")
        _git(self.root, "add", "-f", "ignored/tracked.py")

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_filter_glob_results_uses_git_visible_files(self) -> None:
        all_py = sorted(self.root.glob("**/*.py"))
        filtered = filter_glob_results(self.root, all_py)
        paths = {str(path.relative_to(self.root)) for path in filtered}

        self.assertIn("src/app.py", paths)
        self.assertIn("ignored/tracked.py", paths)
        self.assertNotIn("ignored/untracked.py", paths)

    def test_build_file_index_uses_git_visible_files(self) -> None:
        index = build_file_index(self.root)

        self.assertIn("src/app.py", index)
        self.assertIn("ignored/tracked.py", index)
        self.assertNotIn("ignored/untracked.py", index)
        self.assertIn("tracked_visible", index["ignored/tracked.py"])

    def test_discover_glob_skips_ignored_untracked_files(self) -> None:
        weld_dir = self.root / ".weld"
        weld_dir.mkdir()
        (weld_dir / "discover.yaml").write_text(
            textwrap.dedent(
                """\
                sources:
                  - glob: "**/*.py"
                    type: file
                    strategy: python_module
                """
            ),
            encoding="utf-8",
        )

        data = discover(self.root)
        discovered_files = {
            node["props"]["file"]
            for node in data["nodes"].values()
            if node["type"] == "file"
        }

        self.assertIn("src/app.py", discovered_files)
        self.assertIn("ignored/tracked.py", discovered_files)
        self.assertNotIn("ignored/untracked.py", discovered_files)

    def test_discover_explicit_files_respect_git_boundary(self) -> None:
        weld_dir = self.root / ".weld"
        weld_dir.mkdir(exist_ok=True)
        (weld_dir / "discover.yaml").write_text(
            textwrap.dedent(
                """\
                sources:
                  - files: ["ignored/tracked.py", "ignored/untracked.py"]
                    type: config
                    strategy: config_file
                """
            ),
            encoding="utf-8",
        )

        data = discover(self.root)
        config_files = {
            node["props"]["file"]
            for node in data["nodes"].values()
            if node["type"] == "config"
        }

        self.assertEqual(config_files, {"ignored/tracked.py"})

class NonGitFallbackBoundaryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.root = Path(self.tmpdir) / "project"

        (self.root / "src").mkdir(parents=True)
        (self.root / "src" / "app.py").write_text(
            "def fallback_visible():\n    return True\n",
            encoding="utf-8",
        )

        (self.root / "node_modules" / "pkg").mkdir(parents=True)
        (self.root / "node_modules" / "pkg" / "shadow.py").write_text(
            "def fallback_hidden():\n    return True\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_non_git_repo_uses_static_exclusions(self) -> None:
        all_py = sorted(self.root.glob("**/*.py"))
        filtered = filter_glob_results(self.root, all_py)
        paths = {str(path.relative_to(self.root)) for path in filtered}

        self.assertEqual(paths, {"src/app.py"})

        index = build_file_index(self.root)
        self.assertIn("src/app.py", index)
        self.assertNotIn("node_modules/pkg/shadow.py", index)


class SymlinkIntoExcludedTreeTest(unittest.TestCase):
    """Bazel-runfiles-style symlinks into excluded trees must not leak.

    Before this fix, ``path_within_repo_boundary`` resolved the symlink
    before checking EXCLUDED_DIR_NAMES, so a symlink under
    ``.cache/bazel/runfiles/`` that pointed at ``src/app.py`` passed the
    boundary check and ended up in ``discovered_from``.
    """

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.root = Path(self.tmpdir) / "repo"
        self.root.mkdir(parents=True)
        _git(self.root, "init", "-q")

        src = self.root / "src"
        src.mkdir()
        (src / "app.py").write_text("def app():\n    return True\n", encoding="utf-8")

        cache_runfiles = self.root / ".cache" / "bazel" / "runfiles"
        cache_runfiles.mkdir(parents=True)
        # Symlink pointing back into the repo's tracked source.
        (cache_runfiles / "app.py").symlink_to(src / "app.py")

        _git(self.root, "add", "src/app.py")
        # .cache is excluded both via EXCLUDED_DIR_NAMES and because its
        # contents are untracked; this test exercises the "tracked target
        # under an excluded path" case.

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_filter_glob_results_drops_symlinks_into_excluded_tree(self) -> None:
        all_py = sorted(self.root.glob("**/*.py"))
        filtered = filter_glob_results(self.root, all_py)
        paths = {str(p.relative_to(self.root)) for p in filtered}
        self.assertIn("src/app.py", paths)
        self.assertNotIn(".cache/bazel/runfiles/app.py", paths)

    def test_discover_does_not_record_cache_in_discovered_from(self) -> None:
        weld_dir = self.root / ".weld"
        weld_dir.mkdir()
        (weld_dir / "discover.yaml").write_text(
            textwrap.dedent(
                """\
                sources:
                  - glob: "**/*.py"
                    type: file
                    strategy: python_module
                """
            ),
            encoding="utf-8",
        )

        data = discover(self.root)
        discovered_from = data.get("meta", {}).get("discovered_from", [])
        leaked = [p for p in discovered_from if p.startswith(".cache")]
        self.assertFalse(
            leaked,
            f"discovered_from must not contain .cache/ entries; got: {leaked}",
        )

        node_files = [
            node.get("props", {}).get("file", "")
            for node in data.get("nodes", {}).values()
        ]
        leaked_nodes = [f for f in node_files if f.startswith(".cache")]
        self.assertFalse(
            leaked_nodes,
            "No file node should live under .cache/ (symlink leak). "
            f"Offending nodes: {leaked_nodes}",
        )


class WalkGlobPrunesExcludedDirsTest(unittest.TestCase):
    """walk_glob must not descend into excluded directories."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.root = Path(self.tmpdir) / "repo"
        self.root.mkdir(parents=True)
        _git(self.root, "init", "-q")

        src = self.root / "src"
        src.mkdir()
        (src / "a.py").write_text("pass\n", encoding="utf-8")

        # Excluded subtree. A post-fix walker never visits these paths.
        cache = self.root / ".cache" / "bazel" / "runfiles"
        cache.mkdir(parents=True)
        for i in range(5):
            (cache / f"file{i}.py").write_text("pass\n", encoding="utf-8")

        # Another excluded subtree (node_modules).
        nm = self.root / "node_modules" / "pkg"
        nm.mkdir(parents=True)
        (nm / "index.py").write_text("pass\n", encoding="utf-8")

        _git(self.root, "add", "src/a.py")

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_walk_glob_does_not_descend_into_excluded_dirs(self) -> None:
        import os as _os

        import weld.glob_match as gm
        from weld.glob_match import walk_glob

        original = _os.walk
        visited: list[str] = []

        def tracking_walk(*args, **kwargs):  # type: ignore[no-untyped-def]
            for dirpath, dirnames, filenames in original(*args, **kwargs):
                visited.append(str(dirpath))
                yield dirpath, dirnames, filenames

        original_gm_walk = gm.os.walk
        try:
            gm.os.walk = tracking_walk  # type: ignore[assignment]
            results = walk_glob(self.root, "**/*.py")
        finally:
            gm.os.walk = original_gm_walk  # type: ignore[assignment]

        result_rels = {str(p.relative_to(self.root)) for p in results}
        self.assertEqual(result_rels, {"src/a.py"})

        offending = [
            d for d in visited
            if "/.cache/" in d or d.endswith("/.cache")
            or "/node_modules/" in d or d.endswith("/node_modules")
        ]
        self.assertFalse(
            offending,
            f"os.walk descended into excluded subtree: {offending}",
        )


class MatchesExcludeTest(unittest.TestCase):
    """Unit tests for the rel-path exclude matcher."""

    def test_globstar_subtree_patterns_match(self) -> None:
        # The three report cases that returned False under the old
        # basename-only matcher must now return True.
        self.assertTrue(matches_exclude(".cache/bazel/foo.rs", [".cache/**"]))
        self.assertTrue(matches_exclude("compiler/src/lib.rs", ["compiler/**"]))
        self.assertTrue(
            matches_exclude("pkg/build/generated/api.gen.py", ["**/*.gen.py"])
        )

    def test_basename_patterns_still_match(self) -> None:
        # Backward compat: bare filename and extension patterns that worked
        # under the old matcher still work.
        self.assertTrue(matches_exclude("foo.py", ["*.py"]))
        self.assertTrue(matches_exclude("pkg/a/b/foo.pyc", ["*.pyc"]))
        self.assertTrue(matches_exclude("pkg/a/README.md", ["README.md"]))

    def test_non_matching_patterns_return_false(self) -> None:
        self.assertFalse(matches_exclude("src/app.py", [".cache/**"]))
        self.assertFalse(matches_exclude("src/app.py", ["compiler/**"]))
        self.assertFalse(matches_exclude("src/app.py", []))

    def test_empty_patterns_are_ignored(self) -> None:
        # A config with an empty-string entry must not match everything.
        self.assertFalse(matches_exclude("foo.py", [""]))


class FilterGlobResultsExcludesTest(unittest.TestCase):
    """filter_glob_results honours the ``excludes`` keyword argument."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.root = Path(self.tmpdir) / "repo"
        self.root.mkdir(parents=True)
        _git(self.root, "init", "-q")

        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text("pass\n", encoding="utf-8")
        (self.root / "compiler").mkdir()
        (self.root / "compiler" / "lib.py").write_text("pass\n", encoding="utf-8")
        (self.root / "build").mkdir()
        (self.root / "build" / "out.py").write_text("pass\n", encoding="utf-8")

        _git(self.root, "add", "src/app.py", "compiler/lib.py", "build/out.py")

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_excludes_kwarg_drops_matching_paths(self) -> None:
        all_py = sorted(self.root.glob("**/*.py"))
        filtered = filter_glob_results(
            self.root, all_py, excludes=["compiler/**", "build/**"]
        )
        paths = {str(p.relative_to(self.root)) for p in filtered}
        self.assertEqual(paths, {"src/app.py"})

    def test_empty_excludes_leaves_result_unchanged(self) -> None:
        all_py = sorted(self.root.glob("**/*.py"))
        baseline = filter_glob_results(self.root, all_py)
        with_excl = filter_glob_results(self.root, all_py, excludes=[])
        self.assertEqual(baseline, with_excl)

    def test_source_level_exclude_drops_subtree_in_discover(self) -> None:
        weld_dir = self.root / ".weld"
        weld_dir.mkdir()
        (weld_dir / "discover.yaml").write_text(
            textwrap.dedent(
                """\
                sources:
                  - glob: "**/*.py"
                    type: file
                    strategy: python_module
                    exclude:
                      - "compiler/**"
                """
            ),
            encoding="utf-8",
        )

        data = discover(self.root)
        discovered_files = {
            node["props"]["file"]
            for node in data["nodes"].values()
            if node["type"] == "file"
        }
        self.assertIn("src/app.py", discovered_files)
        self.assertIn("build/out.py", discovered_files)
        self.assertNotIn("compiler/lib.py", discovered_files)


if __name__ == "__main__":
    unittest.main()
