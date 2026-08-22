"""Where a ``wd`` read is answered from (ADR 0096 section 1).

Before this module, ``--root`` defaulted to the literal ``Path(".")``:
a read answered from whatever ``.weld/`` happened to sit in the current
directory, and from a subdirectory it answered from nothing at all.
Resolution replaces that with a single rule -- *answer from the checkout
the caller is standing in* -- and two entry points that share it:

* :func:`resolve_weld_root` for the CLI, where the caller's position is
  the current working directory.
* :func:`resolve_request_root` for a long-lived server (the MCP path,
  ADR 0083), where a request may name a root and that name is untrusted.

The load-bearing subtlety is the **ceiling**. Worktrees nest: an agent's
worktree commonly lives *inside* the main checkout, whose ``.weld/`` is
one or two directories further up. An unbounded upward walk would find
that outer graph and cheerfully answer from the wrong branch -- the
exact failure ADR 0096 exists to prevent -- so the walk stops at the
working-tree root reported by git for the caller's own directory.
Resolution therefore never crosses a checkout boundary: it can decline
to find a graph, but it cannot substitute somebody else's.

Nothing here writes. Commands that *create* state (``wd discover``,
``wd init``, ``wd warm``) keep explicit-root semantics on purpose.
"""

from __future__ import annotations

import os
from pathlib import Path

from weld._git_worktree import git_toplevel, same_git_repo

__all__ = [
    "ROOT_HELP",
    "RootOutOfBoundsError",
    "resolve_request_root",
    "resolve_weld_root",
]

#: Shared ``--root`` help text for every graph-backed read parser, so the
#: documented default cannot drift between ``wd query`` and ``wd brief``.
ROOT_HELP = (
    "Project root directory. Default: resolved from the current "
    "directory -- the nearest enclosing directory with a .weld/, bounded "
    "by this git worktree, else the worktree root. Resolution never "
    "crosses into another checkout."
)

#: One message for every rejection in :func:`resolve_request_root`.
#: Distinguishing "no such directory" from "outside the repository"
#: would turn the error into a filesystem-existence oracle for whoever
#: is driving the server, so the reason is deliberately not narrowed and
#: the offending path is never echoed back.
_OUT_OF_BOUNDS_MESSAGE = (
    "requested root is not an existing directory inside the same "
    "repository as the serving root"
)


class RootOutOfBoundsError(ValueError):
    """A requested root is not an in-repository directory of the server.

    Raised only by :func:`resolve_request_root`. The CLI path cannot
    trigger it: an operator running ``wd --root ...`` already has the
    process's own filesystem authority, so there is nothing to bound.
    """


def _nearest_weld_ancestor(start: Path, ceiling: Path) -> Path | None:
    """Nearest self-or-ancestor of *start* holding ``.weld/``, or ``None``.

    The walk inspects *ceiling* itself (a graph at the worktree root is
    the common case) and then stops -- it never steps past it. Reaching
    the filesystem root without ever meeting *ceiling* also stops the
    walk: a start directory that is not actually under the ceiling is a
    reason to give up, not to keep climbing.

    Comparison goes through ``realpath`` so a symlinked checkout or a
    ``..`` segment cannot slip past the boundary by spelling alone; the
    ceiling's own key is computed once rather than per level.
    """
    ceiling_key = os.path.realpath(ceiling)
    for candidate in (start, *start.parents):
        if (candidate / ".weld").is_dir():
            return candidate
        if os.path.realpath(candidate) == ceiling_key:
            return None
    return None


def resolve_weld_root(
    arg_root: Path | str | None = None,
    cwd: Path | str | None = None,
) -> Path:
    """Return the project root a read should be answered from.

    Precedence, highest first:

    1. *arg_root* -- an explicit ``--root``. Taken as given (made
       absolute, nothing else): the operator named a root, so no walk
       may second-guess it, and no symlink is resolved behind their back.
    2. The nearest self-or-ancestor of *cwd* containing ``.weld/``,
       bounded by the git working-tree root of *cwd*.
    3. That working-tree root, when the walk found no ``.weld/``.
    4. *cwd* itself, when there is no git repository to bound a walk --
       an unbounded climb out of a plain directory could only land in an
       unrelated project.

    *cwd* defaults to the process working directory. The return value is
    always absolute; it is not required to exist, and no ``.weld/`` is
    required to be there -- callers own the "no graph here" message.
    """
    if arg_root is not None:
        return Path(arg_root).absolute()
    start = Path(cwd).absolute() if cwd is not None else Path.cwd()
    ceiling = git_toplevel(start)
    if ceiling is None:
        return start
    found = _nearest_weld_ancestor(start, ceiling)
    return ceiling if found is None else found


def resolve_request_root(
    requested: Path | str | None,
    server_root: Path | str,
) -> Path:
    """Resolve a request-supplied root against the root a server serves.

    ``None`` -- the ordinary case -- means "wherever the server was
    launched", and *server_root* is returned unchanged (absolute).

    Any other value is untrusted input and must clear one bound: it has
    to be an existing directory belonging to the **same git repository**
    as *server_root*, i.e. a sibling checkout of the tree the operator
    already exposed. Repository identity comes from
    ``--git-common-dir``, so linked worktrees pass and an unrelated
    clone, a bare path outside git, a regular file, or a path that does
    not exist all fail. The accepted path is normalized through
    ``realpath``, which is also what defeats ``..`` traversal: the check
    is applied to the destination, never to the spelling.

    :raises RootOutOfBoundsError: on every rejection, with one uniform
        message (see :data:`_OUT_OF_BOUNDS_MESSAGE`).
    """
    base = Path(server_root).absolute()
    if requested is None:
        return base
    try:
        resolved = Path(os.path.realpath(requested))
        accepted = resolved.is_dir() and same_git_repo(resolved, base)
    except OSError as exc:
        raise RootOutOfBoundsError(_OUT_OF_BOUNDS_MESSAGE) from exc
    if not accepted:
        raise RootOutOfBoundsError(_OUT_OF_BOUNDS_MESSAGE)
    return resolved
