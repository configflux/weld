"""Workspace ledger, lockfile, and status helpers for polyrepo roots.

Every writer under ``.weld/`` uses :func:`atomic_write_text` (or its
binary sibling :func:`atomic_write_bytes`): temp file in the same
directory + :func:`os.replace`. A ``workspace.lock`` whose PID is dead
or whose payload is unreadable is treated as stale, removed, and warned
about on the next acquire (ADR 0011 section 8).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from weld._workspace_inspect import inspect_child as _inspect_child
from weld._workspace_lock import (
    WorkspaceLock,
    WorkspaceLockedError,
    WORKSPACE_LOCK_FILENAME,
)
from weld.workspace import WorkspaceConfig, load_workspaces_yaml

# Re-export lock types so existing ``from weld.workspace_state import ...``
# continues to work without touching every consumer.
__all__ = [
    "WorkspaceLock",
    "WorkspaceLockedError",
    "WorkspaceStateError",
    "WorkspaceChildState",
    "WorkspaceState",
    "atomic_write_bytes",
    "atomic_write_text",
    "find_workspaces_yaml",
    "load_workspace_config",
    "build_workspace_state",
    "save_workspace_state",
    "load_workspace_state_json",
    "format_workspace_status",
    "main",
    "WORKSPACE_STATE_VERSION",
    "WORKSPACE_STATE_FILENAME",
    "WORKSPACE_LOCK_FILENAME",
]

WORKSPACE_STATE_VERSION = 1
WORKSPACE_STATE_FILENAME = "workspace-state.json"
_WORKSPACES_CANDIDATES: tuple[str, ...] = (".weld/workspaces.yaml", "workspaces.yaml")
_STATUS_ORDER: tuple[str, ...] = ("present", "missing", "uninitialized", "corrupt")


def atomic_write_text(final_path: Path | str, text: str) -> None:
    """Atomically replace ``final_path`` with ``text``.

    Writes via :func:`tempfile.mkstemp` in the same directory as
    ``final_path`` (POSIX rename atomicity) then :func:`os.replace`.
    Any exception removes the temp file and leaves ``final_path``
    untouched, so callers see exactly the old bytes or exactly the new.
    Missing parent directories are created.
    """
    final = Path(final_path)
    final.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f"{final.name}.tmp.",
        dir=str(final.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(str(tmp_path), str(final))
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def atomic_write_bytes(final_path: Path | str, data: bytes) -> None:
    """Atomically replace ``final_path`` with ``data``.

    Bytes-mode sibling of :func:`atomic_write_text`: same temp-file
    naming convention (``<basename>.tmp.*`` in the same directory),
    same :func:`os.replace` rename, same cleanup-on-failure so callers
    see exactly the old bytes or exactly the new. Missing parent
    directories are created.
    """
    final = Path(final_path)
    final.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f"{final.name}.tmp.",
        dir=str(final.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(str(tmp_path), str(final))
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


class WorkspaceStateError(RuntimeError):
    """Raised when workspace state or configuration cannot be loaded."""


@dataclass
class WorkspaceChildState:
    """Lifecycle ledger for a single registered child repository."""

    status: str
    head_sha: str | None
    head_ref: str | None
    is_dirty: bool
    graph_path: str
    graph_sha256: str | None
    last_seen_utc: str
    error: str | None = None
    remote: str | None = None

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "status": self.status,
            "head_sha": self.head_sha,
            "head_ref": self.head_ref,
            "is_dirty": self.is_dirty,
            "graph_path": self.graph_path,
            "graph_sha256": self.graph_sha256,
            "last_seen_utc": self.last_seen_utc,
        }
        if self.error is not None:
            data["error"] = self.error
        if self.remote is not None:
            data["remote"] = self.remote
        return data


@dataclass
class WorkspaceState:
    """Serialized workspace ledger persisted under ``.weld/``."""

    version: int = WORKSPACE_STATE_VERSION
    children: dict[str, WorkspaceChildState] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "children": {
                name: self.children[name].to_dict()
                for name in sorted(self.children)
            },
        }


def find_workspaces_yaml(root: Path | str) -> Path | None:
    """Return the configured workspace registry path, if present."""
    root_path = Path(root)
    for rel_path in _WORKSPACES_CANDIDATES:
        candidate = root_path / rel_path
        if candidate.is_file():
            return candidate
    return None


def load_workspace_config(root: Path | str) -> WorkspaceConfig | None:
    """Load the workspace registry for *root*, or ``None`` when absent."""
    config_path = find_workspaces_yaml(root)
    if config_path is None:
        return None
    return load_workspaces_yaml(config_path)


def _utc_now() -> str:
    from datetime import datetime, timezone

    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def build_workspace_state(
    root: Path | str,
    config: WorkspaceConfig,
    *,
    now: str | None = None,
) -> WorkspaceState:
    """Inspect every registered child and build the current ledger snapshot."""
    root_path = Path(root)
    seen_at = _utc_now() if now is None else now
    children: dict[str, WorkspaceChildState] = {}

    for child in sorted(config.children, key=lambda entry: entry.name):
        kwargs = _inspect_child(root_path, child.path, child.remote, seen_at)
        children[child.name] = WorkspaceChildState(**kwargs)

    return WorkspaceState(children=children)


def save_workspace_state(root: Path | str, state: WorkspaceState) -> None:
    """Write ``workspace-state.json`` atomically via :func:`atomic_write_text`."""
    state_path = Path(root) / ".weld" / WORKSPACE_STATE_FILENAME
    text = json.dumps(state.to_dict(), indent=2, sort_keys=True) + "\n"
    atomic_write_text(state_path, text)


def load_workspace_state_json(root: Path | str) -> dict[str, object]:
    """Load ``workspace-state.json`` and validate its top-level shape."""
    state_path = Path(root) / ".weld" / WORKSPACE_STATE_FILENAME
    if not state_path.is_file():
        raise WorkspaceStateError(
            f"{state_path} not found; run `wd workspace bootstrap` to "
            f"materialize this workspace, or `wd discover` if you intended "
            f"to (re)discover from scratch",
        )

    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkspaceStateError(f"failed to read {state_path}: {exc}") from exc

    if not isinstance(data, dict):
        raise WorkspaceStateError(f"{state_path} must contain a JSON object")
    if data.get("version") != WORKSPACE_STATE_VERSION:
        raise WorkspaceStateError(
            f"{state_path} has unsupported version {data.get('version')!r}",
        )
    if not isinstance(data.get("children"), dict):
        raise WorkspaceStateError(f"{state_path} is missing a children object")
    return data


def format_workspace_status(state: dict[str, object]) -> str:
    """Render a human-readable workspace status summary."""
    raw_children = state.get("children", {})
    if not isinstance(raw_children, dict):
        raise WorkspaceStateError("workspace-state.json children payload is not an object")

    counts = Counter(
        str(entry.get("status", "unknown"))
        for entry in raw_children.values()
        if isinstance(entry, dict)
    )
    lines = [
        f"Workspace status ({len(raw_children)} children)",
        "Counts: "
        + ", ".join(f"{status}={counts.get(status, 0)}" for status in _STATUS_ORDER),
    ]

    for name in sorted(raw_children):
        entry = raw_children[name]
        if not isinstance(entry, dict):
            lines.append(f"{name}: invalid")
            continue
        status = str(entry.get("status", "unknown"))
        dirty = " dirty" if entry.get("is_dirty") else ""
        head_ref = entry.get("head_ref") or "detached"
        head_sha = entry.get("head_sha")
        head_suffix = f" {str(head_sha)[:12]}" if isinstance(head_sha, str) and head_sha else ""
        lines.append(f"{name}: {status}{dirty} ({head_ref}{head_suffix})")

    return "\n".join(lines)


def _run_bootstrap_subcommand(args: argparse.Namespace) -> int:
    """Execute ``wd workspace bootstrap`` via the orchestrator module."""
    from weld._workspace_bootstrap import bootstrap_workspace

    try:
        result = bootstrap_workspace(
            args.root, max_depth=args.max_depth, ignore_all=args.ignore_all,
        )
    except FileNotFoundError as exc:
        print(f"[weld] error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        payload = {
            "root_init_ran": result.root_init_ran,
            "workspace_yaml_written": result.workspace_yaml_written,
            "children_discovered": result.children_discovered,
            "children_initialized": result.children_initialized,
            "children_recursed": result.children_recursed,
            "children_present": result.children_present,
            "errors": result.errors,
        }
        sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write("\n".join(result.summary_lines()) + "\n")

    # Non-zero exit when any discovered child is not present after the
    # run, so scripts and CI can treat a partial bootstrap as a failure.
    missing = set(result.children_discovered) - set(result.children_present)
    if result.children_discovered and missing:
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for ``wd workspace``."""
    from weld.workspace import DEFAULT_MAX_DEPTH as _WS_DEFAULT_MAX_DEPTH

    parser = argparse.ArgumentParser(
        prog="wd workspace",
        description="Inspect workspace registry and child status ledger",
    )
    subparsers = parser.add_subparsers(dest="workspace_command")

    status_parser = subparsers.add_parser(
        "status",
        help="Show workspace child status from workspace-state.json",
    )
    status_parser.add_argument(
        "--root",
        default=".",
        help="Workspace root directory (default: .)",
    )
    status_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the raw workspace-state.json payload",
    )

    bootstrap_parser = subparsers.add_parser(
        "bootstrap",
        help=(
            "One-shot polyrepo bootstrap: init root, scan nested repos, "
            "init each child, recurse-discover, rebuild root meta-graph"
        ),
    )
    bootstrap_parser.add_argument(
        "--root",
        default=".",
        help="Workspace root directory (default: .)",
    )
    bootstrap_parser.add_argument(
        "--max-depth",
        type=int,
        default=_WS_DEFAULT_MAX_DEPTH,
        help=(
            "Maximum directory depth when scanning for nested git repos "
            f"(default: {_WS_DEFAULT_MAX_DEPTH})"
        ),
    )
    bootstrap_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a JSON summary of what the bootstrap did",
    )
    bootstrap_parser.add_argument(
        "--ignore-all",
        action="store_true",
        help="Write a fully-ignoring .weld/.gitignore in the root and every "
             "child (every weld file ignored). Default is selective: track "
             "config and the canonical graph, ignore per-machine state.",
    )

    args = parser.parse_args(argv)
    if args.workspace_command == "bootstrap":
        return _run_bootstrap_subcommand(args)
    if args.workspace_command != "status":
        parser.print_help()
        return 0

    try:
        state = load_workspace_state_json(args.root)
    except WorkspaceStateError as exc:
        print(f"[weld] error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        sys.stdout.write(json.dumps(state, indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write(format_workspace_status(state) + "\n")
    return 0
