"""Regression tests for cortex source hygiene — worktree exclusion (project-ac5.2 / project-ac5.3).

Ensures that the shared exclusion policy in ``cortex.strategies._helpers`` and the
file-index builder in ``cortex/file_index.py`` correctly:
- exclude ``.claude/worktrees`` paths from discovery and indexing
- exclude standard build/cache directories (``.git``, ``node_modules``, etc.)
- preserve canonical (non-worktree) files through filtering
- handle edge cases like partial segment matches without false positives

These are regression guards: if the exclusion lists or matching logic are
changed, any reintroduction of worktree contamination will cause a failure here.
"""

from __future__ import annotations

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from cortex.strategies._helpers import (  # noqa: E402
    EXCLUDED_DIR_NAMES,
    EXCLUDED_NESTED_REPO_SEGMENTS,
    filter_glob_results,
    is_excluded_dir_name,
    is_nested_repo_copy,
)
from cortex.file_index import build_file_index  # noqa: E402

class IsExcludedDirNameTest(unittest.TestCase):
    """Tests for is_excluded_dir_name()."""

    def test_known_excluded_dirs(self) -> None:
        for name in [".git", "node_modules", "__pycache__", ".cortex",
                     "bazel-bin", "bazel-out", "bazel-testlogs",
                     "bazel-project", ".worktrees"]:
            with self.subTest(name=name):
                self.assertTrue(
                    is_excluded_dir_name(name),
                    f"{name} should be excluded",
                )

    def test_arbitrary_bazel_dirs(self) -> None:
        """Any directory starting with 'bazel-' should be excluded."""
        for name in ["bazel-foo", "bazel-genfiles", "bazel-123"]:
            with self.subTest(name=name):
                self.assertTrue(
                    is_excluded_dir_name(name),
                    f"{name} should be excluded (bazel-* pattern)",
                )

    def test_normal_dirs_not_excluded(self) -> None:
        for name in ["src", "tools", "services", "docs", "libs",
                      ".claude", "apps", "cortex", "tests"]:
            with self.subTest(name=name):
                self.assertFalse(
                    is_excluded_dir_name(name),
                    f"{name} should NOT be excluded",
                )

    def test_bazel_without_hyphen_not_excluded(self) -> None:
        """'bazel' alone (no hyphen) should not be excluded."""
        self.assertFalse(is_excluded_dir_name("bazel"))

class IsNestedRepoCopyTest(unittest.TestCase):
    """Tests for is_nested_repo_copy()."""

    def test_claude_worktrees_paths_excluded(self) -> None:
        """Paths under .claude/worktrees/ should be detected as nested copies."""
        excluded_paths = [
            (".claude", "worktrees"),
            (".claude", "worktrees", "agent-abc123"),
            (".claude", "worktrees", "agent-abc123", "src", "foo.py"),
            (".claude", "worktrees", "branch-xyz", "cortex", "graph.py"),
        ]
        for parts in excluded_paths:
            with self.subTest(parts=parts):
                self.assertTrue(
                    is_nested_repo_copy(parts),
                    f"Path {'/'.join(parts)} should be detected as nested copy",
                )

    def test_non_worktree_claude_paths_not_excluded(self) -> None:
        """Other .claude/ subdirectories should NOT be flagged."""
        safe_paths = [
            (".claude",),
            (".claude", "agents", "tdd.md"),
            (".claude", "plans", "my-plan.md"),
            (".claude", "settings.json"),
        ]
        for parts in safe_paths:
            with self.subTest(parts=parts):
                self.assertFalse(
                    is_nested_repo_copy(parts),
                    f"Path {'/'.join(parts)} should NOT be flagged as nested copy",
                )

    def test_unrelated_paths_not_excluded(self) -> None:
        safe_paths = [
            ("src", "services", "api"),
            ("cortex", "graph.py"),
            ("apps", "web", "components"),
            ("worktrees",),  # bare 'worktrees' without .claude prefix
        ]
        for parts in safe_paths:
            with self.subTest(parts=parts):
                self.assertFalse(is_nested_repo_copy(parts))

class FilterGlobResultsTest(unittest.TestCase):
    """Tests for filter_glob_results()."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.root = Path(self.tmpdir) / "project"
        # Create canonical file
        (self.root / "src").mkdir(parents=True)
        (self.root / "src" / "app.py").write_text("canonical")
        # Create worktree copy
        wt = self.root / ".claude" / "worktrees" / "agent-xyz" / "src"
        wt.mkdir(parents=True)
        (wt / "app.py").write_text("shadow")
        # Create node_modules file
        nm = self.root / "node_modules" / "pkg"
        nm.mkdir(parents=True)
        (nm / "index.py").write_text("dep")
        # Create __pycache__ file
        pc = self.root / "__pycache__"
        pc.mkdir(parents=True)
        (pc / "cached.py").write_text("cached")

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_filters_worktree_copies(self) -> None:
        all_py = sorted(self.root.glob("**/*.py"))
        filtered = filter_glob_results(self.root, all_py)
        paths = [str(p.relative_to(self.root)) for p in filtered]
        self.assertIn("src/app.py", paths, "canonical file must survive filtering")
        for p in paths:
            self.assertNotIn(
                ".claude/worktrees", p,
                f"worktree copy should be filtered out: {p}",
            )

    def test_filters_node_modules(self) -> None:
        all_py = sorted(self.root.glob("**/*.py"))
        filtered = filter_glob_results(self.root, all_py)
        paths = [str(p.relative_to(self.root)) for p in filtered]
        for p in paths:
            self.assertNotIn("node_modules", p)

    def test_filters_pycache(self) -> None:
        all_py = sorted(self.root.glob("**/*.py"))
        filtered = filter_glob_results(self.root, all_py)
        paths = [str(p.relative_to(self.root)) for p in filtered]
        for p in paths:
            self.assertNotIn("__pycache__", p)

    def test_canonical_only_result(self) -> None:
        """After filtering, only src/app.py should remain."""
        all_py = sorted(self.root.glob("**/*.py"))
        filtered = filter_glob_results(self.root, all_py)
        paths = [str(p.relative_to(self.root)) for p in filtered]
        self.assertEqual(paths, ["src/app.py"])

class BuildFileIndexWorktreeExclusionTest(unittest.TestCase):
    """Tests for build_file_index() worktree and excluded-dir filtering."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.root = Path(self.tmpdir) / "project"

        # Canonical Python file
        src = self.root / "services" / "api" / "src"
        src.mkdir(parents=True)
        (src / "handler.py").write_text(textwrap.dedent("""\
            class RequestHandler:
                def handle(self):
                    pass
        """))

        # Canonical TypeScript file
        web = self.root / "apps" / "web" / "lib"
        web.mkdir(parents=True)
        (web / "utils.ts").write_text(textwrap.dedent("""\
            export function formatPrice(cents: number): string {
                return (cents / 100).toFixed(2);
            }
        """))

        # Worktree shadow copy (must be excluded)
        wt_src = (self.root / ".claude" / "worktrees" / "agent-abc"
                   / "services" / "api" / "src")
        wt_src.mkdir(parents=True)
        (wt_src / "handler.py").write_text(textwrap.dedent("""\
            class DuplicateHandler:
                pass
        """))

        wt_web = (self.root / ".claude" / "worktrees" / "agent-abc"
                   / "apps" / "web" / "lib")
        wt_web.mkdir(parents=True)
        (wt_web / "utils.ts").write_text(textwrap.dedent("""\
            export function shadowUtil(): void {}
        """))

        # Files in .git (excluded)
        git_dir = self.root / ".git" / "objects"
        git_dir.mkdir(parents=True)
        (git_dir / "test.py").write_text("git internal")

        # Files in node_modules (excluded)
        nm = self.root / "node_modules" / "pkg"
        nm.mkdir(parents=True)
        (nm / "index.ts").write_text("export const dep = 1;")

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_canonical_files_indexed(self) -> None:
        index = build_file_index(self.root)
        self.assertIn("services/api/src/handler.py", index)
        self.assertIn("apps/web/lib/utils.ts", index)

    def test_worktree_copies_not_indexed(self) -> None:
        index = build_file_index(self.root)
        for path in index:
            self.assertNotIn(
                ".claude/worktrees", path,
                f"worktree copy should not be indexed: {path}",
            )

    def test_git_dir_not_indexed(self) -> None:
        index = build_file_index(self.root)
        for path in index:
            self.assertNotIn(
                ".git", path.split("/"),
                f".git should not be indexed: {path}",
            )

    def test_node_modules_not_indexed(self) -> None:
        index = build_file_index(self.root)
        for path in index:
            self.assertNotIn(
                "node_modules", path.split("/"),
                f"node_modules should not be indexed: {path}",
            )

    def test_canonical_python_tokens_extracted(self) -> None:
        """Canonical handler.py should have RequestHandler token."""
        index = build_file_index(self.root)
        tokens = index.get("services/api/src/handler.py", [])
        self.assertIn("RequestHandler", tokens)

    def test_canonical_ts_tokens_extracted(self) -> None:
        """Canonical utils.ts should have formatPrice token."""
        index = build_file_index(self.root)
        tokens = index.get("apps/web/lib/utils.ts", [])
        self.assertIn("formatPrice", tokens)

    def test_shadow_tokens_not_present(self) -> None:
        """DuplicateHandler and shadowUtil must not appear in any indexed file."""
        index = build_file_index(self.root)
        all_tokens = []
        for tokens in index.values():
            all_tokens.extend(tokens)
        self.assertNotIn("DuplicateHandler", all_tokens,
                         "shadow Python class should not be indexed")
        self.assertNotIn("shadowUtil", all_tokens,
                         "shadow TS function should not be indexed")

    def test_legacy_worktrees_dir_excluded(self) -> None:
        """Files under .worktrees/ (legacy path) should also be excluded."""
        legacy = self.root / ".worktrees" / "branch1" / "services"
        legacy.mkdir(parents=True)
        (legacy / "shadow.py").write_text("class LegacyShadow: pass")
        index = build_file_index(self.root)
        for path in index:
            parts = path.split("/")
            self.assertNotIn(
                ".worktrees", parts,
                f".worktrees should be excluded: {path}",
            )

class ExclusionConstantsTest(unittest.TestCase):
    """Verify the exclusion constants have the expected entries."""

    def test_excluded_dir_names_contains_essentials(self) -> None:
        required = {".git", "node_modules", "__pycache__", ".cortex", ".worktrees"}
        self.assertTrue(
            required.issubset(EXCLUDED_DIR_NAMES),
            f"Missing from EXCLUDED_DIR_NAMES: {required - EXCLUDED_DIR_NAMES}",
        )

    def test_nested_repo_segments_contains_claude_worktrees(self) -> None:
        self.assertIn(
            (".claude", "worktrees"),
            EXCLUDED_NESTED_REPO_SEGMENTS,
            "EXCLUDED_NESTED_REPO_SEGMENTS must include (.claude, worktrees)",
        )

if __name__ == "__main__":
    unittest.main()
