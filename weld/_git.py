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
