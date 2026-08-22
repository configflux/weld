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

from weld._git_worktree import git_main_checkout_path
from weld._graph_schema import GraphShapeError, validate_dict_payload


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
    graph_file = child_root / ".weld" / "graph.json"
    graph_status, graph_sha256, graph_error, counts = _graph_status(graph_file)

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
        # ADR 0011 §5 self-describing ledger fields (additive, null for
        # non-present children). graph_mtime_ns lets a status fast-path skip
        # the SHA recompute; node/edge counts make the ledger printable
        # without reopening the child graph.
        graph_mtime_ns=_graph_mtime_ns(graph_file) if graph_status == "present" else None,
        node_count=counts[0],
        edge_count=counts[1],
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


def _graph_mtime_ns(graph_path: Path) -> int | None:
    """Return the child ``graph.json``'s ``st_mtime_ns``, or ``None``.

    ADR 0011 §5: persisted so a status fast-path can skip recomputing the
    graph SHA when the file has not been touched since the ledger was built.
    """
    try:
        return graph_path.stat().st_mtime_ns
    except OSError:
        return None


def _graph_status(
    graph_path: Path,
) -> tuple[str, str | None, str | None, tuple[int | None, int | None]]:
    """Classify the child graph and return ``(status, sha256, error, counts)``.

    ``counts`` is ``(node_count, edge_count)`` for a ``present`` graph and
    ``(None, None)`` otherwise (ADR 0011 §5 self-describing ledger fields).

    The top-level dict-shape check is routed through
    :func:`weld._graph_schema.validate_dict_payload` -- the same guard
    :func:`weld._graph_schema.load_graph_file` and
    :func:`weld.federation_support.load_graph_bytes` call -- so its wording
    can't drift into a second, hand-typed copy (bd 5038-9jz2). The raised
    :class:`~weld._graph_schema.GraphShapeError` is caught right here: this
    stays a tuple-returning classifier and never raises outward.
    """
    if not graph_path.is_file():
        return "uninitialized", None, None, (None, None)

    try:
        raw = graph_path.read_bytes()
    except OSError as exc:
        return "corrupt", None, f"{type(exc).__name__}: {exc}", (None, None)

    digest = hashlib.sha256(raw).hexdigest()
    try:
        decoded = raw.decode("utf-8")
        payload = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return "corrupt", digest, f"{type(exc).__name__}: {exc}", (None, None)

    try:
        validate_dict_payload(payload)
    except GraphShapeError as exc:
        return "corrupt", digest, f"{type(exc).__name__}: {exc}", (None, None)

    nodes = payload.get("nodes")
    edges = payload.get("edges")
    node_count = len(nodes) if isinstance(nodes, (dict, list)) else None
    edge_count = len(edges) if isinstance(edges, (dict, list)) else None
    return "present", digest, None, (node_count, edge_count)
