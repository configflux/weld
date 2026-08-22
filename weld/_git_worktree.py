"""Git worktree / branch plumbing for the connected structure tooling.

Sibling of :mod:`weld._git`, which sits at its line-count cap. ADR 0096
names this module as the home for the worktree-resolution helpers
(``get_git_branch``, ``git_common_dir``, ``same_git_repo``,
``git_toplevel``, ``graph_is_tracked``, ``tracked_graph_commit``,
``list_worktrees``, ``is_linked_worktree``).
``git_main_checkout_path`` -- introduced by
ADR 0028 and originally landed in :mod:`weld._git` -- was relocated here
under that same charter: it is a worktree-resolution probe built on
``git_common_dir``, so it belongs beside it rather than in the general
git module.

Every helper follows the same subprocess hygiene as :mod:`weld._git`,
funnelled through :func:`_git_text`: a **fixed argv** (no shell, no
caller string ever interpolated into an argument -- only ``cwd`` is
caller-derived), ``LC_ALL=C`` so output parsing is locale-independent, a
short timeout, and a neutral return value instead of an exception on any
failure. These probes run on the read path and must never break a read:
a missing ``git`` binary, a deleted directory, or a hung filesystem
degrades the answer, it does not raise.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

__all__ = [
    "get_git_branch",
    "git_common_dir",
    "git_main_checkout_path",
    "git_toplevel",
    "graph_is_tracked",
    "is_linked_worktree",
    "list_worktrees",
    "same_git_repo",
    "tracked_graph_commit",
]

#: Repo-relative pathspec of the canonical graph, as git sees it. Passed
#: after ``--`` so it can never be re-read as an option, and kept a
#: literal constant so no caller value reaches argv.
_GRAPH_PATHSPEC = ".weld/graph.json"


def _git_text(root: Path | str, *args: str, timeout: int = 5) -> str | None:
    """Run a read-only ``git`` command in *root*; return stdout or ``None``.

    ``None`` means "no answer": *root* is not a directory, ``git`` is
    absent or timed out, or the command exited non-zero. Callers turn
    that into their own neutral value rather than propagating an error.
    """
    try:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            cwd=str(root),
            timeout=timeout,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def get_git_branch(root: Path | str) -> str | None:
    """Return the branch checked out at *root*, or ``None``.

    ``None`` is returned for every state that has no branch identity to
    report: *root* is outside a git repository or does not exist, ``git``
    is unavailable or times out, or ``HEAD`` is **detached** (a raw-SHA
    checkout, a bisect, a CI job that checked out a tag).

    ``git symbolic-ref --quiet --short HEAD`` is used rather than
    ``git rev-parse --abbrev-ref HEAD`` precisely because it *fails* on a
    detached HEAD instead of printing the literal string ``HEAD``, which
    is indistinguishable from a branch actually named ``HEAD``. An unborn
    branch (a fresh ``git init`` before the first commit) still resolves:
    the ref name exists even with no commit behind it, which is the
    honest answer for "which branch is this checkout on".
    """
    out = _git_text(root, "symbolic-ref", "--quiet", "--short", "HEAD")
    if out is None:
        return None
    return out.strip() or None


def git_common_dir(root: Path | str) -> Path | None:
    """Return the repository's shared git directory, or ``None``.

    ``git rev-parse --git-common-dir`` names the *repository*, not the
    checkout: every linked worktree of one repo reports the same common
    dir, while two unrelated clones never do. That makes it the identity
    key for "are these the same repository", which is the bound
    :func:`same_git_repo` enforces.

    The result is made absolute (git often answers with the relative
    ``.git``) and passed through ``realpath`` so two spellings of one
    directory -- a symlinked checkout, a ``..`` segment -- compare equal.
    ``None`` when *root* is not in a repository or the lookup fails.
    """
    out = _git_text(root, "rev-parse", "--git-common-dir")
    if out is None or not out.strip():
        return None
    try:
        return Path(os.path.realpath(Path(root) / out.strip()))
    except OSError:
        return None


def git_main_checkout_path(root: Path) -> Path | None:
    """Return the main git worktree's checkout for *root*, or ``None``.

    When *root* lives inside a linked git worktree (created via
    ``git worktree add ...``), the shared git directory that
    :func:`git_common_dir` names is the *main* checkout's ``.git``; the
    parent of that path is the main worktree itself -- where sibling
    repositories registered in a federated ``workspaces.yaml`` actually
    live (ADR 0028).

    Returns ``None`` when *root* is not inside a git repository, when
    ``git`` is not available, when the lookup fails, or when the resolved
    main checkout is the same directory as *root* (i.e. *root* is already
    the main worktree, so there is nothing to fall back to).
    """
    common_path = git_common_dir(root)
    if common_path is None:
        return None
    main_checkout = common_path.parent
    try:
        if main_checkout.resolve() == Path(root).resolve():
            # *root* is already the main worktree; nothing to fall back to.
            return None
    except OSError:
        return None
    return main_checkout


def is_linked_worktree(root: Path | str) -> bool:
    """Return True when *root* is a **linked** git worktree.

    The test is exactly the one ADR 0096 fixes on, and the one
    ``tools/worktree_guard_hook.py`` and ``tools/tool_env.sh`` already
    use: ``--git-dir`` differs from ``--git-common-dir``. A linked
    worktree keeps its own ``.git/worktrees/<name>`` administrative
    directory while sharing the repository's common dir; the main
    checkout reports the same path for both. Being pure plumbing, this
    holds for every layout ``git worktree add`` supports and for every
    tool that creates one -- nested inside the main checkout, a sibling
    directory, ``/tmp``, or an agent SDK's private area -- with no path
    pattern to keep in sync.

    False whenever the question cannot be answered at all (*root* is not
    in a repository, ``git`` is unavailable, either lookup failed): the
    caller uses this to decide whether to *write*, so an unknown must
    never read as permission. Both sides go through ``realpath`` so two
    spellings of one directory compare equal.
    """
    common = git_common_dir(root)
    if common is None:
        return False
    out = _git_text(root, "rev-parse", "--git-dir")
    if out is None or not out.strip():
        return False
    try:
        own = Path(os.path.realpath(Path(root) / out.strip()))
    except OSError:
        return False
    return own != common


def same_git_repo(left: Path | str, right: Path | str) -> bool:
    """Return True when both paths belong to the same git repository.

    False whenever either side has no repository identity at all -- two
    non-repo directories are *not* "the same repo", because a caller uses
    this as a permission check and an unknown must never read as allowed.
    """
    left_dir = git_common_dir(left)
    return left_dir is not None and left_dir == git_common_dir(right)


def git_toplevel(root: Path | str) -> Path | None:
    """Return the working-tree root containing *root*, or ``None``.

    For a linked worktree this is that worktree's own directory, never
    the main checkout -- which is what makes it usable as the ceiling on
    an upward ``.weld/`` walk. ``None`` when *root* is outside a working
    tree (including inside a bare repository, which has none).
    """
    out = _git_text(root, "rev-parse", "--show-toplevel")
    if out is None or not out.strip():
        return None
    return Path(out.strip())


def graph_is_tracked(root: Path | str) -> bool:
    """Return True when ``.weld/graph.json`` is tracked by git at *root*.

    This is the Mode B test (ADR 0076 ``--track-graphs``): a tracked
    graph arrives with the checkout, so a fresh clone or worktree can
    bootstrap from it instead of paying a cold discover. ``git ls-files
    --error-unmatch`` exits non-zero for an untracked or unknown path,
    which :func:`_git_text` already collapses to ``None``.
    """
    return _git_text(
        root, "ls-files", "--error-unmatch", "--", _GRAPH_PATHSPEC,
    ) is not None


def tracked_graph_commit(root: Path | str) -> str | None:
    """Return the last commit that touched the tracked graph, or ``None``.

    Used as the basis SHA for a synthesized Mode B sidecar. It is a
    deliberately *conservative* answer: the last commit that touched
    ``.weld/graph.json`` is at or before the commit the graph actually
    describes, so derived staleness can only over-trigger a refresh --
    it can never report a stale graph as fresh.

    ``None`` when the path has no history (untracked, or a repo with no
    commits) or the lookup fails; the walk starts at ``HEAD``.
    """
    out = _git_text(
        root, "log", "-n", "1", "--format=%H", "--", _GRAPH_PATHSPEC,
        timeout=10,
    )
    if out is None:
        return None
    return out.strip() or None


def list_worktrees(root: Path | str) -> list[Path]:
    """Return every checkout of *root*'s repository, primary first.

    Pure ``git worktree list --porcelain`` -- no path patterns, no
    assumptions about who created a worktree or where it lives, so
    nested, sibling, ``/tmp`` and bare-repo-hub layouts all enumerate
    alike. Git emits the main worktree first; that order is preserved so
    a caller can prefer the primary checkout as a seed source.

    A bare primary is included (git lists it, with no working tree), as
    are registered-but-missing worktrees: a caller selecting a seed
    already has to check the directory for a readable graph, and that
    same check filters both. Returns ``[]`` on any failure.

    Paths come from the ``worktree`` attribute lines verbatim; the
    porcelain format is newline-delimited, so a checkout path containing
    a literal newline is not representable -- a limitation of the format
    itself, and such a path cannot be produced by ``git worktree add``.
    """
    out = _git_text(root, "worktree", "list", "--porcelain", timeout=10)
    if out is None:
        return []
    prefix = "worktree "
    return [
        Path(line[len(prefix):])
        for line in out.splitlines()
        if line.startswith(prefix) and line[len(prefix):].strip()
    ]
