"""Which checkout a ``wd`` read is answered from (ADR 0096 sec. 1).

The failure this module exists to prevent is silent: a search run inside
a worktree that has no graph of its own finds the *outer* checkout's
graph one directory up and answers confidently from the wrong branch.
Nothing in the output says so. The guard is a ceiling on the upward
``.weld/`` walk, and the only honest way to test it is against real
``git worktree add`` layouts -- nested, sibling, and bare-clone -- with
no path patterns and nothing specific to the tool that made them.

:func:`resolve_request_root` is the same question asked by a server on
behalf of an untrusted caller, so it is tested as a bound: same-repo
checkouts pass and everything else raises, with one message that reveals
nothing about the filesystem.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from weld._root_resolver import (
    RootOutOfBoundsError,
    resolve_request_root,
    resolve_weld_root,
)

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
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Weld Test")
    _git(root, "checkout", "-q", "-b", "main")
    (root / "hello.py").write_text("x = 1\n", encoding="utf-8")
    _git(root, "add", "hello.py")
    _git(root, "commit", "-q", "-m", "seed")


def _weld_dir(root: Path) -> Path:
    d = root / ".weld"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _same_path(left: Path, right: Path) -> bool:
    return os.path.realpath(left) == os.path.realpath(right)


class _RepoFixture(unittest.TestCase):
    """A main checkout with ``.weld/``, plus a temp dir to hang layouts on."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = self.tmp / "repo"
        _init_repo(self.repo)
        _weld_dir(self.repo)

    def assertResolves(self, cwd: Path, expected: Path) -> None:
        actual = resolve_weld_root(None, cwd=cwd)
        self.assertTrue(
            _same_path(actual, expected),
            f"resolved {actual} from {cwd}; expected {expected}",
        )


class ExplicitRootTest(_RepoFixture):
    """An explicit ``--root`` is taken as given -- never re-walked."""

    def test_explicit_root_wins_over_the_cwd_walk(self) -> None:
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        resolved = resolve_weld_root(elsewhere, cwd=self.repo / "pkg")
        self.assertTrue(_same_path(resolved, elsewhere))

    def test_explicit_root_is_not_walked_up_to_a_weld_ancestor(self) -> None:
        # A subdirectory of a repo that *does* have .weld/ must stay that
        # subdirectory: the operator named it, so nothing may substitute
        # the repo root behind their back.
        sub = self.repo / "pkg" / "deep"
        sub.mkdir(parents=True)
        self.assertTrue(_same_path(resolve_weld_root(sub, cwd=self.repo), sub))

    def test_relative_explicit_root_is_made_absolute(self) -> None:
        resolved = resolve_weld_root(Path("."), cwd=self.repo)
        self.assertTrue(resolved.is_absolute())

    def test_explicit_root_accepts_a_string(self) -> None:
        resolved = resolve_weld_root(str(self.repo), cwd=self.tmp)
        self.assertTrue(_same_path(resolved, self.repo))


class CwdWalkTest(_RepoFixture):
    """Precedence 2 and 3: nearest ``.weld/`` ancestor, else the toplevel."""

    def test_repo_root_resolves_to_itself(self) -> None:
        self.assertResolves(self.repo, self.repo)

    def test_deep_subdirectory_resolves_to_the_repo_root(self) -> None:
        deep = self.repo / "pkg" / "sub" / "deeper"
        deep.mkdir(parents=True)
        self.assertResolves(deep, self.repo)

    def test_nearest_weld_wins_over_a_further_one(self) -> None:
        # A vendored sub-project with its own graph is answered from that
        # sub-project, not from the repo root above it.
        nested = self.repo / "vendor" / "child"
        _weld_dir(nested)
        (nested / "src").mkdir()
        self.assertResolves(nested / "src", nested)

    def test_repo_without_any_weld_falls_back_to_the_toplevel(self) -> None:
        bare_repo = self.tmp / "no-weld"
        _init_repo(bare_repo)
        deep = bare_repo / "a" / "b"
        deep.mkdir(parents=True)
        self.assertResolves(deep, bare_repo)


class CeilingTest(_RepoFixture):
    """The load-bearing bound: the walk stops at the worktree boundary."""

    def test_nested_worktree_without_a_graph_never_climbs_to_the_outer_one(
        self,
    ) -> None:
        # The exact wrong-branch failure ADR 0096 forbids: the worktree
        # lives inside a checkout that HAS .weld/, and has none itself.
        linked = self.repo / "wt" / "feature"
        _git(self.repo, "worktree", "add", "-q", "-b", "feature", str(linked))
        self.assertFalse((linked / ".weld").exists())
        (linked / "pkg").mkdir()
        self.assertResolves(linked, linked)
        self.assertResolves(linked / "pkg", linked)

    def test_nested_worktree_with_its_own_graph_resolves_to_itself(
        self,
    ) -> None:
        linked = self.repo / "wt" / "seeded"
        _git(self.repo, "worktree", "add", "-q", "-b", "seeded", str(linked))
        _weld_dir(linked)
        sub = linked / "weld"
        sub.mkdir()
        self.assertResolves(sub, linked)

    def test_directory_above_a_nested_worktree_still_uses_the_outer_repo(
        self,
    ) -> None:
        # The ceiling is per-cwd, not a global exclusion: standing in the
        # container directory is standing in the outer checkout.
        linked = self.repo / "wt" / "feature"
        _git(self.repo, "worktree", "add", "-q", "-b", "feature", str(linked))
        self.assertResolves(self.repo / "wt", self.repo)

    def test_sibling_worktree_resolves_to_itself(self) -> None:
        sibling = self.tmp / "sibling"
        _git(self.repo, "worktree", "add", "-q", "-b", "sib", str(sibling))
        self.assertResolves(sibling, sibling)
        _weld_dir(sibling)
        (sibling / "pkg").mkdir()
        self.assertResolves(sibling / "pkg", sibling)

    def test_worktree_from_a_bare_hub_resolves_to_itself(self) -> None:
        bare = self.tmp / "hub.git"
        _git(self.tmp, "clone", "-q", "--bare", str(self.repo), str(bare))
        checkout = self.tmp / "from-hub"
        _git(bare, "worktree", "add", "-q", "--detach", str(checkout), "main")
        self.assertResolves(checkout, checkout)

    def test_detached_head_worktree_still_resolves(self) -> None:
        # Branch identity is absent here; root resolution must not care.
        linked = self.tmp / "detached"
        _git(self.repo, "worktree", "add", "-q", "--detach", str(linked))
        self.assertResolves(linked, linked)


class NonGitTest(_RepoFixture):
    """Precedence 4: no repository means no walk at all."""

    def test_plain_directory_resolves_to_itself(self) -> None:
        plain = self.tmp / "plain" / "deep"
        plain.mkdir(parents=True)
        self.assertResolves(plain, plain)

    def test_no_climb_into_an_unrelated_parent_project(self) -> None:
        # ``parent`` has a .weld/ but no git; a child directory under it
        # must NOT be answered from it -- an unbounded walk in a non-git
        # tree can only find unrelated projects.
        parent = self.tmp / "parent"
        _weld_dir(parent)
        child = parent / "child"
        child.mkdir()
        self.assertResolves(child, child)

    def test_default_cwd_is_used_when_none_is_passed(self) -> None:
        plain = self.tmp / "cwd-default"
        plain.mkdir()
        previous = Path.cwd()
        os.chdir(plain)
        try:
            self.assertTrue(_same_path(resolve_weld_root(), plain))
        finally:
            os.chdir(previous)

    def test_result_is_always_absolute(self) -> None:
        plain = self.tmp / "abs"
        plain.mkdir()
        self.assertTrue(resolve_weld_root(None, cwd=plain).is_absolute())

    def test_vanished_cwd_degrades_to_that_path_rather_than_raising(
        self,
    ) -> None:
        # git cannot be consulted from a directory that is gone, so there
        # is no repository to bound a walk. A read served from here will
        # report "no graph"; resolution's job is only not to blow up, and
        # emphatically not to substitute some enclosing checkout.
        gone = self.repo / "deleted"
        self.assertResolves(gone, gone)


class RequestRootTest(_RepoFixture):
    """The server-side bound (consumed by the optional MCP wiring)."""

    def test_none_returns_the_server_root(self) -> None:
        self.assertTrue(
            _same_path(resolve_request_root(None, self.repo), self.repo),
        )

    def test_same_repo_worktree_is_accepted_and_normalized(self) -> None:
        sibling = self.tmp / "sibling"
        _git(self.repo, "worktree", "add", "-q", "-b", "sib", str(sibling))
        noisy = sibling / "pkg" / ".."
        (sibling / "pkg").mkdir()
        resolved = resolve_request_root(noisy, self.repo)
        self.assertTrue(_same_path(resolved, sibling))
        self.assertNotIn("..", resolved.parts)

    def test_subdirectory_of_the_same_repo_is_accepted(self) -> None:
        sub = self.repo / "pkg"
        sub.mkdir()
        self.assertTrue(_same_path(resolve_request_root(sub, self.repo), sub))

    def test_unrelated_repository_is_rejected(self) -> None:
        other = self.tmp / "other"
        _init_repo(other)
        with self.assertRaises(RootOutOfBoundsError):
            resolve_request_root(other, self.repo)

    def test_directory_outside_any_repository_is_rejected(self) -> None:
        plain = self.tmp / "plain"
        plain.mkdir()
        with self.assertRaises(RootOutOfBoundsError):
            resolve_request_root(plain, self.repo)

    def test_regular_file_inside_the_repo_is_rejected(self) -> None:
        with self.assertRaises(RootOutOfBoundsError):
            resolve_request_root(self.repo / "hello.py", self.repo)

    def test_nonexistent_path_is_rejected(self) -> None:
        with self.assertRaises(RootOutOfBoundsError):
            resolve_request_root(self.repo / "nope" / "gone", self.repo)

    def test_traversal_is_judged_on_the_destination_not_the_spelling(
        self,
    ) -> None:
        escape = self.repo / ".." / "outside"
        (self.tmp / "outside").mkdir()
        with self.assertRaises(RootOutOfBoundsError):
            resolve_request_root(escape, self.repo)

    def test_rejection_message_is_uniform_and_leaks_no_path(self) -> None:
        # Distinguishing "missing" from "out of repo" would make the
        # error a filesystem-existence oracle for the caller.
        secret = self.tmp / "no-such-secret-dir"
        outside = self.tmp / "outside-repo"
        outside.mkdir()
        messages = []
        for candidate in (secret, outside, self.repo / "hello.py"):
            with self.assertRaises(RootOutOfBoundsError) as ctx:
                resolve_request_root(candidate, self.repo)
            messages.append(str(ctx.exception))
            self.assertNotIn(str(candidate), str(ctx.exception))
            self.assertNotIn(candidate.name, str(ctx.exception))
        self.assertEqual(len(set(messages)), 1)


if __name__ == "__main__":
    unittest.main()
