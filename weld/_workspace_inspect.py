"""Child repository inspection helpers for workspace ledger.

Probes a single registered child: git HEAD, dirty status, and
graph.json validity. Used by :func:`build_workspace_state` to
populate :class:`WorkspaceChildState` entries.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

from weld._git import git_main_checkout_path


def resolve_child_root(root: Path, rel_path: str) -> Path:
    """Return the absolute path to a child repo under *root*.

    Primary: ``root / rel_path``. When that location is not a git repo and
    *root* is inside a linked git worktree, fall back to the main
    worktree's checkout (ADR 0028 §1). The fallback is necessary because
    isolated worktrees do not contain sibling child repos -- those live
    only at the main checkout. When neither location exists, the primary
    path is returned so callers can report the failure with the
    user-visible relative-path suffix intact.
    """
    primary = root / rel_path
    if primary.is_dir() and (primary / ".git").exists():
        return primary
    main_checkout = git_main_checkout_path(root)
    if main_checkout is None:
        return primary
    fallback = main_checkout / rel_path
    if fallback.is_dir() and (fallback / ".git").exists():
        return fallback
    return primary


def inspect_child(
    root: Path,
    rel_path: str,
    remote: str | None,
    seen_at: str,
) -> dict:
    """Return a kwargs dict suitable for ``WorkspaceChildState(...)``.

    Uses :func:`resolve_child_root` so a child that exists only at the
    main worktree's checkout (the common case under
    ``git worktree``-based isolation) is still reported as ``present``.
    See ADR 0028.
    """
    child_root = resolve_child_root(root, rel_path)
    graph_rel = (Path(rel_path) / ".weld" / "graph.json").as_posix()

    if not child_root.is_dir() or not (child_root / ".git").exists():
        return dict(
            status="missing",
            head_sha=None,
            head_ref=None,
            is_dirty=False,
            graph_path=graph_rel,
            graph_sha256=None,
            last_seen_utc=seen_at,
            remote=remote,
        )

    head_sha = _git_stdout(child_root, "rev-parse", "HEAD")
    head_ref = _git_stdout(child_root, "symbolic-ref", "-q", "HEAD")
    is_dirty = bool(_git_stdout(child_root, "status", "--porcelain"))
    graph_status, graph_sha256, graph_error = _graph_status(
        child_root / ".weld" / "graph.json",
    )

    return dict(
        status=graph_status,
        head_sha=head_sha,
        head_ref=head_ref,
        is_dirty=is_dirty,
        graph_path=graph_rel,
        graph_sha256=graph_sha256,
        last_seen_utc=seen_at,
        error=graph_error,
        remote=remote,
    )


def _git_stdout(repo_root: Path, *args: str) -> str | None:
    env = {**os.environ, "LC_ALL": "C"}
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if proc.returncode != 0:
        return None
    output = proc.stdout.strip()
    return output or None


def _graph_status(graph_path: Path) -> tuple[str, str | None, str | None]:
    if not graph_path.is_file():
        return "uninitialized", None, None

    try:
        raw = graph_path.read_bytes()
    except OSError as exc:
        return "corrupt", None, f"{type(exc).__name__}: {exc}"

    digest = hashlib.sha256(raw).hexdigest()
    try:
        decoded = raw.decode("utf-8")
        payload = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return "corrupt", digest, f"{type(exc).__name__}: {exc}"

    if not isinstance(payload, dict):
        return "corrupt", digest, "ValueError: top-level graph payload must be a JSON object"

    return "present", digest, None
