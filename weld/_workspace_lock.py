"""PID-based workspace lockfile for polyrepo root discovery.

Provides :class:`WorkspaceLock`, the mutual-exclusion primitive that
guards concurrent ``wd discover`` runs on a federated workspace root.
Stale locks (dead PID or unreadable payload) are auto-cleaned with
a warning.
"""

from __future__ import annotations

import errno
import json
import os
import sys
import tempfile
from pathlib import Path

WORKSPACE_LOCK_FILENAME = "workspace.lock"


class WorkspaceLockedError(RuntimeError):
    """Raised when another discover process already holds the workspace lock.

    The error message names both the lockfile path and, when the lockfile
    is readable, the PID of the live holder -- operators triaging a stuck
    workspace should not need a second shell command to find it.
    """

    def __init__(self, lock_path: Path) -> None:
        self.lock_path = lock_path
        holder_pid = _read_lock_pid(lock_path)
        pid_suffix = f" (pid {holder_pid})" if holder_pid is not None else ""
        super().__init__(
            f"workspace discover already in progress{pid_suffix}; "
            f"lockfile: {lock_path}",
        )


def _read_lock_pid(path: Path) -> int | None:
    """Best-effort read of the ``pid`` field from ``workspace.lock``."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    pid = payload.get("pid") if isinstance(payload, dict) else None
    return pid if isinstance(pid, int) else None


class WorkspaceLock:
    """PID lockfile guarding root discovery on a workspace.

    ``acquire`` opens ``<root>/.weld/workspace.lock`` with ``O_CREAT |
    O_EXCL`` -- the sole rendezvous for concurrent discovers. A second
    caller gets :class:`WorkspaceLockedError`. If the existing lockfile's
    PID is dead or unreadable, it is removed, a warning is printed to
    stderr, and the acquire retries once.
    """

    def __init__(self, root: Path) -> None:
        self._path = root / ".weld" / WORKSPACE_LOCK_FILENAME
        self._held = False

    @property
    def path(self) -> Path:
        return self._path

    def acquire(self) -> "WorkspaceLock":
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"created_at": _utc_now(), "pid": os.getpid()}
        fd, tmp_path = tempfile.mkstemp(
            prefix=f"{WORKSPACE_LOCK_FILENAME}.tmp.",
            dir=str(self._path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
            os.chmod(tmp_path, 0o644)
            try:
                os.link(tmp_path, self._path)
            except FileExistsError as exc:
                if _is_lock_stale(self._path):
                    print(
                        f"[weld] warning: stale workspace.lock at {self._path}; "
                        "previous holder is no longer running, cleaning up.",
                        file=sys.stderr,
                    )
                    try:
                        self._path.unlink()
                    except FileNotFoundError:
                        pass
                    try:
                        os.link(tmp_path, self._path)
                    except FileExistsError as exc2:
                        raise WorkspaceLockedError(self._path) from exc2
                else:
                    raise WorkspaceLockedError(self._path) from exc
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        else:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

        self._held = True
        return self

    def release(self) -> None:
        if not self._held:
            return
        try:
            self._path.unlink(missing_ok=True)
        finally:
            self._held = False

    def __enter__(self) -> "WorkspaceLock":
        return self.acquire()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


def _is_lock_stale(path: Path) -> bool:
    """A missing/unreadable/malformed lockfile or one with a dead PID."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return True
    except OSError:
        return True

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return True

    pid = payload.get("pid") if isinstance(payload, dict) else None
    if not isinstance(pid, int) or pid <= 0:
        return True
    return not _pid_is_alive(pid)


def _pid_is_alive(pid: int) -> bool:
    """Portable ``os.kill(pid, 0)`` liveness probe; EPERM means alive."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        return exc.errno == errno.EPERM
    return True


def _utc_now() -> str:
    """ISO-8601 UTC timestamp with second precision."""
    from datetime import datetime, timezone

    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
