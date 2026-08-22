"""Every graph-backed read resolves its root from the caller's checkout.

Black-box wiring test for ADR 0096 section 1, run through ``weld.cli.main``
-- the surface a user actually types at. Two behaviours are pinned, and
each of the five read entry points must satisfy both, because each owns
its own parser (``query`` and friends share ``_graph_cli``; ``brief``,
``trace``, ``impact`` and ``diff`` each build their own) and a resolve
call missing from any one of them is a silent wrong-root read there.

1. **From a subdirectory the read still finds the graph.** With
   ``--root`` defaulting to the literal ``"."`` this failed: standing in
   ``pkg/deep`` looked for ``pkg/deep/.weld/graph.json`` and reported a
   first-run "no graph" message inside a fully discovered repository.

2. **From a graphless nested worktree the read reports no graph rather
   than answering from the checkout it is nested inside.** This is the
   whole point of the ceiling. Seeding that worktree is a later change
   (ADR 0096 section 2); until then the honest answer is "no graph
   here", and the answer that must never appear is the outer checkout's.

Explicit ``--root`` is asserted to still win, since resolution must not
quietly override an operator who named a root.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from weld.cli import main as cli_main
from weld.contract import SCHEMA_VERSION

_GIT_ENV = {"LC_ALL": "C", "PATH": "/usr/bin:/usr/local/bin:/bin"}

#: Read commands that funnel through ``ensure_graph_exists``. Each entry
#: is the argv after ``wd``; all five must resolve their own ``--root``.
_READ_INVOCATIONS = (
    ("query", "Store"),
    ("brief", "Store"),
    ("trace", "--node", "entity:Store"),
    ("impact", "entity:Store"),
    ("diff",),
)

_MISSING_GRAPH = "No Weld graph found"


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


def _write_graph(root: Path, label: str) -> None:
    """Write a minimal graph whose single node names its checkout."""
    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    (weld_dir / "graph.json").write_text(
        json.dumps(
            {
                "meta": {
                    "version": SCHEMA_VERSION,
                    "git_sha": "deadbeef",
                    "updated_at": "2026-08-13T00:00:00+00:00",
                },
                "nodes": {
                    "entity:Store": {
                        "type": "entity",
                        "label": label,
                        "props": {"file": "domain/store.py"},
                    },
                },
                "edges": [],
            },
        ),
        encoding="utf-8",
    )


def _run(cwd: Path, argv: tuple[str, ...]) -> str:
    """Run ``wd <argv>`` from *cwd*; return stdout + stderr combined.

    Auto-refresh is frozen so a stale fixture graph cannot trigger a real
    discovery pass, and the exit code is irrelevant here -- these tests
    assert *which root answered*, which lives in the output either way.
    """
    out, err = io.StringIO(), io.StringIO()
    previous_cwd = Path.cwd()
    os.chdir(cwd)
    try:
        with redirect_stdout(out), redirect_stderr(err):
            try:
                cli_main(list(argv))
            except SystemExit:
                pass
    finally:
        os.chdir(previous_cwd)
    return out.getvalue() + err.getvalue()


class RootResolutionWiringTest(unittest.TestCase):
    """One repo with a graph, one nested graphless worktree inside it."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = self.tmp / "repo"
        self.repo.mkdir(parents=True)
        _git(self.repo, "init", "-q")
        _git(self.repo, "config", "user.email", "test@example.com")
        _git(self.repo, "config", "user.name", "Weld Test")
        _git(self.repo, "checkout", "-q", "-b", "main")
        (self.repo / "hello.py").write_text("x = 1\n", encoding="utf-8")
        _git(self.repo, "add", "hello.py")
        _git(self.repo, "commit", "-q", "-m", "seed")
        _write_graph(self.repo, "OuterCheckoutStore")

        self.deep = self.repo / "pkg" / "deep"
        self.deep.mkdir(parents=True)

        # A worktree nested *inside* the checkout above -- the layout an
        # unbounded upward walk resolves wrongly.
        self.worktree = self.repo / "wt" / "feature"
        _git(
            self.repo, "worktree", "add", "-q", "-b", "feature",
            str(self.worktree),
        )
        self.assertFalse((self.worktree / ".weld").exists())

        for key, value in (("WELD_AUTO_REFRESH", "0"), ("WELD_TELEMETRY", "off")):
            previous = os.environ.get(key)
            os.environ[key] = value
            self.addCleanup(_restore_env, key, previous)

    def test_read_from_a_subdirectory_finds_the_repo_graph(self) -> None:
        for argv in _READ_INVOCATIONS:
            with self.subTest(command=argv[0]):
                output = _run(self.deep, argv)
                self.assertNotIn(_MISSING_GRAPH, output)

    def test_read_from_a_graphless_nested_worktree_reports_no_graph(
        self,
    ) -> None:
        # The wrong-branch guard, end to end: the outer graph is one and
        # two directories up, and must not be reached.
        for argv in _READ_INVOCATIONS:
            with self.subTest(command=argv[0]):
                output = _run(self.worktree, argv)
                self.assertIn(_MISSING_GRAPH, output)
                self.assertNotIn("OuterCheckoutStore", output)

    def test_deep_subdirectory_of_the_worktree_is_bounded_too(self) -> None:
        deep_in_worktree = self.worktree / "pkg" / "deep"
        deep_in_worktree.mkdir(parents=True)
        output = _run(deep_in_worktree, ("query", "Store"))
        self.assertIn(_MISSING_GRAPH, output)
        self.assertNotIn("OuterCheckoutStore", output)

    def test_worktree_with_its_own_graph_answers_from_itself(self) -> None:
        _write_graph(self.worktree, "WorktreeStore")
        (self.worktree / "pkg").mkdir()
        output = _run(self.worktree / "pkg", ("query", "Store"))
        self.assertIn("WorktreeStore", output)
        self.assertNotIn("OuterCheckoutStore", output)

    def test_explicit_root_still_wins_from_inside_the_worktree(self) -> None:
        # ``--root`` is a top-level flag on the ``wd`` parser, so it
        # precedes the subcommand.
        output = _run(
            self.worktree, ("--root", str(self.repo), "query", "Store"),
        )
        self.assertIn("OuterCheckoutStore", output)

    def test_explicit_root_wins_for_a_standalone_parser_too(self) -> None:
        output = _run(
            self.worktree, ("brief", "Store", "--root", str(self.repo)),
        )
        self.assertNotIn(_MISSING_GRAPH, output)


def _restore_env(key: str, previous: str | None) -> None:
    if previous is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = previous


if __name__ == "__main__":
    unittest.main()
