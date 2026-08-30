"""Shared Mode A checkout fixture for the ADR 0096 §2 gate-5 tests.

Mode A is the ADR 0076 default: config is committed, ``graph.json`` is
gitignored. A linked worktree therefore arrives with ``discover.yaml``
and nothing else -- the state gate 5 exists to fill from a sibling
checkout.

The repository is built exactly as a developer builds one (commit the
source, then ``wd discover``), so the primary ends up holding the graph
in its ignored ``.weld/`` while the tracked tree stays clean. Worktrees
are created with plain ``git worktree add``; nothing here knows or cares
which tool made them, which is the property under test.

Git and discover plumbing is shared with the Mode B suites in
:mod:`_seed_fixture`; the names re-exported below are what the gate-5
suites import.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from weld._gitignore_writer import CONFIG_ONLY_GITIGNORE
from weld.tests._seed_fixture import (  # noqa: F401 -- re-exported fixture API
    SIDECAR,
    commit_all,
    discover,
    git,
    graph_nodes,
    init_repo,
    read,
    sidecar,
    stale_info,
    weld_listing,
    wrapped_discover,
)

#: Node id the fixture's ``alpha.py`` extracts to, and the id a
#: branch-only ``beta.py`` adds. Asserting on these is how a test says
#: "this graph describes *that* checkout's content".
ALPHA_NODE = "file:alpha"
BETA_NODE = "file:beta"

#: Placeholder for the checkout under test inside an argv table, so a
#: suite can spell an invocation once and bind the root per test.
ROOT = "<root>"


def seed_mode_a_repo(
    root: Path,
    *,
    run_discover: bool = True,
    gitignore: str = CONFIG_ONLY_GITIGNORE,
) -> None:
    """Init a default-mode repo: config committed, graph ignored and local.

    With *run_discover* False the repository is left graphless, which is
    how a test builds a worktree that has no seed source to find.

    *gitignore* is the repository's ignore policy. The default tracks
    ``discover.yaml``, which is what makes seeding possible at all; pass
    ``IGNORE_ALL_GITIGNORE`` to build the ``wd init --ignore-all`` shape,
    where the commit below cannot add the config and no linked worktree
    ever receives one.
    """
    init_repo(root, gitignore)
    commit_all(root, "mode A: track config, ignore the graph")
    if run_discover:
        discover(root)


def run_read(root: Path, argv: tuple[str, ...], *extra: str) -> tuple[int, str]:
    """Drive the real read CLI at *root*; return ``(exit_code, stderr)``.

    The shared ``read`` helper asserts success, but a *declined* seed ends
    in first-run guidance and a nonzero exit -- which is the observable
    several gate-5 suites are about, so it needs the code rather than an
    assertion. Occurrences of :data:`ROOT` in *argv* are bound to *root*.
    """
    from weld.cli import main as cli_main

    resolved = [str(root) if tok == ROOT else tok for tok in (*argv, *extra)]
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = cli_main(resolved) or 0
        except SystemExit as exc:
            code = exc.code or 0
    return code, err.getvalue()


def add_branch_file(root: Path, stem: str = "beta") -> None:
    """Commit a file that exists only on *root*'s branch."""
    (root / f"{stem}.py").write_text(
        f"def {stem}():\n    return 2\n", encoding="utf-8",
    )
    commit_all(root, f"add {stem}")


def node_exports(root: Path, node_id: str) -> list[str]:
    """Symbols the graph currently attributes to *node_id*.

    A test that needs "the graph re-read this file" wants an observable
    that changes with the file's *content* rather than with the file set,
    because a rewrite that edits a file in place moves no files at all.
    Use :func:`graph_nodes` when the question really is about the file set;
    both work in-process (bd jbpb retired the process-lifetime repo-boundary
    cache that once made a newly created file invisible to a later pass).
    """
    graph = json.loads((root / ".weld" / "graph.json").read_text(encoding="utf-8"))
    node = (graph.get("nodes") or {}).get(node_id) or {}
    return list((node.get("props") or {}).get("exports") or [])


class ModeAFixture(unittest.TestCase):
    """A discovered Mode A primary, plus worktrees and clones on demand."""

    #: Subclasses set False to get a primary with no graph at all.
    discover_primary = True

    #: Ignore policy the repository is built with. Subclasses set
    #: ``IGNORE_ALL_GITIGNORE`` to get a repo whose worktrees arrive
    #: without ``.weld/discover.yaml``.
    gitignore = CONFIG_ONLY_GITIGNORE

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.origin = self.tmp / "origin"
        seed_mode_a_repo(
            self.origin,
            run_discover=self.discover_primary,
            gitignore=self.gitignore,
        )

    def worktree(self, name: str = "feature") -> Path:
        """A linked worktree on its own new branch."""
        target = self.tmp / name
        git(self.origin, "worktree", "add", "-q", "-b", name, str(target))
        return target

    def clone(self) -> Path:
        """A plain clone: no graph, and no sibling checkout to seed from."""
        target = self.tmp / "clone"
        git(self.tmp, "clone", "-q", str(self.origin), str(target))
        git(target, "config", "user.email", "test@example.com")
        git(target, "config", "user.name", "Weld Test")
        return target
