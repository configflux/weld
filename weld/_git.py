"""Git helpers for the connected structure tooling.

Provides functions to query git state without importing external
libraries -- uses subprocess to call the git CLI directly.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

def get_git_sha(root: Path) -> str | None:
    """Return the current HEAD SHA for the repo at *root*, or None.

    Returns ``None`` when *root* is not inside a git repository or
    when ``git`` is not available.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=str(root),
            timeout=5,
            env={**os.environ, "LC_ALL": "C"},
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None

def is_git_repo(root: Path) -> bool:
    """Return True if *root* is inside a git working tree."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            cwd=str(root),
            timeout=5,
            env={**os.environ, "LC_ALL": "C"},
        )
        return result.returncode == 0 and result.stdout.strip() == "true"
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False

def commits_behind(root: Path, old_sha: str, new_sha: str) -> int:
    """Count commits between *old_sha* and *new_sha*.

    Returns the number of commits reachable from *new_sha* that are not
    reachable from *old_sha* (i.e. ``git rev-list --count old..new``).

    Returns ``-1`` if the count cannot be determined (e.g. force-push
    removed the old SHA from history).
    """
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", f"{old_sha}..{new_sha}"],
            capture_output=True,
            text=True,
            cwd=str(root),
            timeout=10,
            env={**os.environ, "LC_ALL": "C"},
        )
        if result.returncode == 0:
            return int(result.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError, ValueError):
        pass
    return -1


def drift_is_graph_only(root: Path, graph_sha: str) -> bool:
    """Return True if every file changed between *graph_sha* and HEAD is
    the graph JSON file (ADR 0017, bd-p1a.6).

    ``wd touch`` stamps ``meta.git_sha = HEAD`` before the user commits
    the graph. Committing ``.weld/graph.json`` then moves HEAD forward
    while the recorded ``graph_sha`` still points at pre-commit HEAD.
    The SHA drift that results is purely bookkeeping: the graph matches
    its inputs, there is nothing to do. Reporting it as drift makes
    ``wd prime`` suggest another ``wd touch``, which then requires
    another commit, which bumps HEAD again -- an infinite touch/commit
    loop.

    This helper detects that exact situation. It returns True only when
    the diff is non-empty AND every changed path equals
    ``.weld/graph.json``. When the diff cannot be computed (git missing,
    SHA unreachable, force-push) or when the diff is empty, the answer
    is False and callers fall back to the normal ``sha_behind`` signal.
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{graph_sha}..HEAD"],
            capture_output=True, text=True, cwd=str(root), timeout=10,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    if result.returncode != 0:
        return False
    paths = [p for p in result.stdout.splitlines() if p]
    if not paths:
        return False
    return all(p == ".weld/graph.json" for p in paths)


def source_files_changed_since(
    root: Path, graph_sha: str, tracked: list[str]
) -> list[str]:
    """Return files changed between *graph_sha* and HEAD that fall under
    any path in *tracked* (ADR 0017).

    *tracked* is a list of directory prefixes or file paths (as stored
    in ``meta.discovered_from``). Directory prefixes may end in ``/``
    or be bare names; both forms match descendants. The root prefix
    ``"./"`` / ``"."`` is treated as "any path" (strategies that scan
    from the repo root record their ``discovered_from`` that way). An
    empty *tracked* yields an empty result -- nothing can be intersected.

    Returns ``[]`` when the diff cannot be computed (git missing, SHA
    unreachable, force-push): callers must treat that as "unknown" and
    fall back to other staleness signals.
    """
    if not tracked:
        return []
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{graph_sha}..HEAD"],
            capture_output=True, text=True, cwd=str(root), timeout=10,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    if result.returncode != 0:
        return []
    out: list[str] = []
    for path in result.stdout.splitlines():
        if not path:
            continue
        for prefix in tracked:
            if not isinstance(prefix, str) or not prefix:
                continue
            # Repo-root marker "./" or "." means every file is tracked.
            if (
                prefix in (".", "./")
                or (prefix.endswith("/") and path.startswith(prefix))
                or path == prefix
                or path.startswith(prefix.rstrip("/") + "/")
            ):
                out.append(path)
                break
    return out
