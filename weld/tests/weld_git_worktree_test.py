"""Git plumbing behind worktree-aware root resolution (ADR 0096 sec. 1).

These helpers are the only thing standing between "answer from the
checkout I am in" and "answer from whatever graph turns up", so they are
tested against **real** git repositories -- a main checkout, a linked
worktree nested inside it, a sibling worktree, and a bare clone with no
working tree of its own. Mocking git here would test the mock: the whole
point is that ``--git-common-dir`` and ``git worktree list`` behave the
way the design assumes across layouts no path pattern can describe.

The other half of the contract is degradation. Every helper runs on the
read path, where a probe that raises would turn a missing directory or
an absent ``git`` binary into a failed search. Each one is therefore
pinned to a neutral return -- ``None``, ``False``, ``[]`` -- rather than
an exception.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from weld._git_worktree import (
    get_git_branch,
    git_common_dir,
    git_toplevel,
    graph_is_tracked,
    list_worktrees,
    same_git_repo,
    tracked_graph_commit,
)

# Fixed, minimal environment: own repo, own identity, no ambient user
# config, so the fixtures are sandbox-hermetic. Mirrors
# ``weld_freshness_branch_test`` / ``discover_worktree_canonical_graph_test``.
_GIT_ENV = {"LC_ALL": "C", "PATH": "/usr/bin:/usr/local/bin:/bin"}


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        env=_GIT_ENV,
        check=True,
    )
    return proc.stdout.strip()


def _init_repo(root: Path) -> None:
    """Init a repo on branch ``main`` with one commit."""
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Weld Test")
    _git(root, "checkout", "-q", "-b", "main")
    (root / "hello.py").write_text("x = 1\n", encoding="utf-8")
    _git(root, "add", "hello.py")
    _git(root, "commit", "-q", "-m", "seed")


def _write_graph(root: Path) -> Path:
    graph = root / ".weld" / "graph.json"
    graph.parent.mkdir(parents=True, exist_ok=True)
    graph.write_text('{"nodes": [], "edges": []}\n', encoding="utf-8")
    return graph


def _same_path(left: Path, right: Path) -> bool:
    return os.path.realpath(left) == os.path.realpath(right)


def _rmtree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


class GitCommonDirTest(unittest.TestCase):
    """``--git-common-dir`` is the repository identity used for bounds."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(_rmtree, self.tmp)
        self.repo = self.tmp / "repo"
        _init_repo(self.repo)

    def test_common_dir_is_absolute_even_though_git_answers_relative(
        self,
    ) -> None:
        # git prints a bare ".git" here; a caller comparing that string
        # across two checkouts would find every repo identical.
        common = git_common_dir(self.repo)
        assert common is not None
        self.assertTrue(common.is_absolute())
        self.assertEqual(common.name, ".git")

    def test_linked_worktree_shares_the_common_dir_of_its_repo(self) -> None:
        linked = self.tmp / "linked"
        _git(self.repo, "worktree", "add", "-q", "-b", "wt", str(linked))
        self.assertEqual(git_common_dir(linked), git_common_dir(self.repo))
        # ...while the worktree's *own* git dir differs -- which is the
        # very distinction that makes common-dir the right identity.
        self.assertNotEqual(
            _git(linked, "rev-parse", "--absolute-git-dir"),
            _git(self.repo, "rev-parse", "--absolute-git-dir"),
        )

    def test_subdirectory_reports_the_repository_not_the_subdirectory(
        self,
    ) -> None:
        nested = self.repo / "pkg" / "deep"
        nested.mkdir(parents=True)
        self.assertEqual(git_common_dir(nested), git_common_dir(self.repo))

    def test_non_repo_and_missing_directory_return_none(self) -> None:
        plain = self.tmp / "plain"
        plain.mkdir()
        self.assertIsNone(git_common_dir(plain))
        self.assertIsNone(git_common_dir(self.tmp / "does-not-exist"))

    def test_regular_file_as_root_returns_none_instead_of_raising(
        self,
    ) -> None:
        # cwd= a file raises NotADirectoryError from subprocess; the read
        # path must see None, not a traceback.
        self.assertIsNone(git_common_dir(self.repo / "hello.py"))


class SameGitRepoTest(unittest.TestCase):
    """The same-repository bound used by the request-root check."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(_rmtree, self.tmp)
        self.repo = self.tmp / "repo"
        _init_repo(self.repo)

    def test_worktree_and_main_checkout_are_the_same_repo(self) -> None:
        linked = self.tmp / "linked"
        _git(self.repo, "worktree", "add", "-q", "-b", "wt", str(linked))
        self.assertTrue(same_git_repo(linked, self.repo))
        self.assertTrue(same_git_repo(self.repo, linked))

    def test_unrelated_clone_is_not_the_same_repo(self) -> None:
        other = self.tmp / "other"
        _init_repo(other)
        self.assertFalse(same_git_repo(other, self.repo))

    def test_two_non_repos_are_not_the_same_repo(self) -> None:
        # "Unknown" must never read as "allowed": this is a bound check.
        a, b = self.tmp / "a", self.tmp / "b"
        a.mkdir()
        b.mkdir()
        self.assertFalse(same_git_repo(a, b))
        self.assertFalse(same_git_repo(a, self.repo))
        self.assertFalse(same_git_repo(self.repo, a))


class GitToplevelTest(unittest.TestCase):
    """The ceiling for the upward ``.weld/`` walk."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(_rmtree, self.tmp)
        self.repo = self.tmp / "repo"
        _init_repo(self.repo)

    def test_subdirectory_resolves_to_the_checkout_root(self) -> None:
        nested = self.repo / "pkg" / "deep"
        nested.mkdir(parents=True)
        top = git_toplevel(nested)
        assert top is not None
        self.assertTrue(_same_path(top, self.repo))

    def test_nested_worktree_reports_itself_not_the_outer_checkout(
        self,
    ) -> None:
        # The layout that breaks an unbounded walk: a worktree living
        # inside the checkout it was created from.
        linked = self.repo / "wt" / "feature"
        _git(self.repo, "worktree", "add", "-q", "-b", "feature", str(linked))
        top = git_toplevel(linked / "..")
        assert top is not None
        # ``linked/..`` is still inside the outer checkout, so the outer
        # toplevel is correct there; from *inside* the worktree the
        # answer must flip to the worktree itself.
        self.assertTrue(_same_path(top, self.repo))
        inner = git_toplevel(linked)
        assert inner is not None
        self.assertTrue(_same_path(inner, linked))

    def test_non_repo_returns_none(self) -> None:
        plain = self.tmp / "plain"
        plain.mkdir()
        self.assertIsNone(git_toplevel(plain))

    def test_bare_repository_has_no_working_tree(self) -> None:
        bare = self.tmp / "hub.git"
        _git(self.tmp, "clone", "-q", "--bare", str(self.repo), str(bare))
        self.assertIsNone(git_toplevel(bare))


class TrackedGraphTest(unittest.TestCase):
    """Mode B detection: is ``.weld/graph.json`` carried by the checkout?"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(_rmtree, self.tmp)
        self.repo = self.tmp / "repo"
        _init_repo(self.repo)

    def test_untracked_graph_is_not_tracked_and_has_no_commit(self) -> None:
        _write_graph(self.repo)
        self.assertFalse(graph_is_tracked(self.repo))
        self.assertIsNone(tracked_graph_commit(self.repo))

    def test_tracked_graph_reports_its_last_touching_commit(self) -> None:
        _write_graph(self.repo)
        _git(self.repo, "add", "-f", ".weld/graph.json")
        _git(self.repo, "commit", "-q", "-m", "track graph")
        expected = _git(self.repo, "rev-parse", "HEAD")
        self.assertTrue(graph_is_tracked(self.repo))
        self.assertEqual(tracked_graph_commit(self.repo), expected)

    def test_commit_is_the_last_graph_touch_not_merely_head(self) -> None:
        # The conservative-basis property: a later commit that does not
        # touch the graph must not be reported as the graph's basis.
        _write_graph(self.repo)
        _git(self.repo, "add", "-f", ".weld/graph.json")
        _git(self.repo, "commit", "-q", "-m", "track graph")
        graph_commit = _git(self.repo, "rev-parse", "HEAD")
        (self.repo / "other.py").write_text("y = 2\n", encoding="utf-8")
        _git(self.repo, "add", "other.py")
        _git(self.repo, "commit", "-q", "-m", "unrelated")
        self.assertNotEqual(_git(self.repo, "rev-parse", "HEAD"), graph_commit)
        self.assertEqual(tracked_graph_commit(self.repo), graph_commit)

    def test_linked_worktree_of_a_tracked_repo_sees_the_tracked_graph(
        self,
    ) -> None:
        _write_graph(self.repo)
        _git(self.repo, "add", "-f", ".weld/graph.json")
        _git(self.repo, "commit", "-q", "-m", "track graph")
        linked = self.tmp / "linked"
        _git(self.repo, "worktree", "add", "-q", "-b", "wt", str(linked))
        self.assertTrue(graph_is_tracked(linked))
        self.assertEqual(
            tracked_graph_commit(linked), _git(self.repo, "rev-parse", "HEAD"),
        )

    def test_non_repo_degrades_instead_of_raising(self) -> None:
        plain = self.tmp / "plain"
        plain.mkdir()
        _write_graph(plain)
        self.assertFalse(graph_is_tracked(plain))
        self.assertIsNone(tracked_graph_commit(plain))


class ListWorktreesTest(unittest.TestCase):
    """Seed-source enumeration: pure git plumbing, primary first."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(_rmtree, self.tmp)
        self.repo = self.tmp / "repo"
        _init_repo(self.repo)

    def test_single_checkout_lists_only_itself(self) -> None:
        listed = list_worktrees(self.repo)
        self.assertEqual(len(listed), 1)
        self.assertTrue(_same_path(listed[0], self.repo))

    def test_primary_comes_first_across_nested_and_sibling_layouts(
        self,
    ) -> None:
        sibling = self.tmp / "sibling"
        nested = self.repo / "wt" / "inner"
        _git(self.repo, "worktree", "add", "-q", "-b", "sib", str(sibling))
        _git(self.repo, "worktree", "add", "-q", "-b", "nest", str(nested))
        listed = list_worktrees(nested)
        self.assertTrue(_same_path(listed[0], self.repo))
        self.assertEqual(len(listed), 3)
        resolved = {os.path.realpath(p) for p in listed}
        self.assertEqual(
            resolved,
            {os.path.realpath(p) for p in (self.repo, sibling, nested)},
        )

    def test_bare_hub_is_listed_so_a_seed_search_can_skip_it(self) -> None:
        # A bare primary has no working tree; it is enumerated (git lists
        # it) and a caller filters it by finding no readable graph there.
        bare = self.tmp / "hub.git"
        _git(self.tmp, "clone", "-q", "--bare", str(self.repo), str(bare))
        checkout = self.tmp / "from-hub"
        _git(bare, "worktree", "add", "-q", str(checkout), "main")
        listed = list_worktrees(checkout)
        resolved = [os.path.realpath(p) for p in listed]
        self.assertIn(os.path.realpath(bare), resolved)
        self.assertIn(os.path.realpath(checkout), resolved)
        self.assertEqual(resolved[0], os.path.realpath(bare))

    def test_non_repo_returns_empty_list(self) -> None:
        plain = self.tmp / "plain"
        plain.mkdir()
        self.assertEqual(list_worktrees(plain), [])


class BranchIdentityRegressionTest(unittest.TestCase):
    """``get_git_branch`` keeps its contract after the shared-runner refactor."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(_rmtree, self.tmp)
        self.repo = self.tmp / "repo"
        _init_repo(self.repo)

    def test_branch_detached_head_and_non_repo(self) -> None:
        self.assertEqual(get_git_branch(self.repo), "main")
        _git(self.repo, "checkout", "-q", "--detach", "HEAD")
        self.assertIsNone(get_git_branch(self.repo))
        plain = self.tmp / "plain"
        plain.mkdir()
        self.assertIsNone(get_git_branch(plain))


class MissingGitBinaryTest(unittest.TestCase):
    """No ``git`` on PATH degrades every helper; it never raises."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(_rmtree, self.tmp)
        self.repo = self.tmp / "repo"
        _init_repo(self.repo)
        empty_path = self.tmp / "empty-bin"
        empty_path.mkdir()
        previous = os.environ.get("PATH", "")
        os.environ["PATH"] = str(empty_path)
        self.addCleanup(os.environ.__setitem__, "PATH", previous)

    def test_every_helper_returns_its_neutral_value(self) -> None:
        self.assertIsNone(get_git_branch(self.repo))
        self.assertIsNone(git_common_dir(self.repo))
        self.assertIsNone(git_toplevel(self.repo))
        self.assertIsNone(tracked_graph_commit(self.repo))
        self.assertFalse(graph_is_tracked(self.repo))
        self.assertFalse(same_git_repo(self.repo, self.repo))
        self.assertEqual(list_worktrees(self.repo), [])


if __name__ == "__main__":
    unittest.main()
