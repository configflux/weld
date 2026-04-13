"""Regression tests for cortex's Git-visible repo boundary behavior."""

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

from cortex.discover import discover  # noqa: E402
from cortex.file_index import build_file_index  # noqa: E402
from cortex.strategies._helpers import filter_glob_results  # noqa: E402

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
        cortex_dir = self.root / ".cortex"
        cortex_dir.mkdir()
        (cortex_dir / "discover.yaml").write_text(
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
        cortex_dir = self.root / ".cortex"
        cortex_dir.mkdir(exist_ok=True)
        (cortex_dir / "discover.yaml").write_text(
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

if __name__ == "__main__":
    unittest.main()
