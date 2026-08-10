"""Advisory exclusive lock serializing connected-structure mutations.

Every mutating ``wd`` verb does an unlocked load -> mutate-in-memory ->
save of ``.weld/graph.json``. Two concurrent writers each load the same
snapshot and the last save silently discards the other's mutation --
observed in the field as 34 nodes lost across 12 parallel enrichment
agents all running ``wd add-node``. :func:`graph_write_lock` wraps the
whole read-modify-write in a blocking OS-level file lock so concurrent
writers serialize instead of clobbering (ADR 0094).

The lock is ``flock``-based on POSIX and ``msvcrt.locking``-based on
Windows. Both die with the holding process, so a crashed writer can
never wedge later ones -- there is no stale-lock cleanup to get wrong.
Readers never take the lock: the save path's atomic rename keeps reads
consistent without it.
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

GRAPH_WRITE_LOCK_FILENAME = "graph.write.lock"
_DEFAULT_TIMEOUT_S = 60.0
_POLL_INTERVAL_S = 0.05


class GraphWriteLockTimeout(RuntimeError):
    """Another process held the graph write lock past the timeout."""

    def __init__(self, lock_path: Path, timeout_s: float) -> None:
        self.lock_path = lock_path
        super().__init__(
            f"timed out after {timeout_s:.0f}s waiting for the graph write "
            f"lock at {lock_path}; another weld process is mutating the "
            "graph. Retry, or raise WELD_GRAPH_LOCK_TIMEOUT (seconds)."
        )


def _resolve_timeout_s(explicit: float | None) -> float:
    if explicit is not None:
        return max(explicit, 0.0)
    raw = os.environ.get("WELD_GRAPH_LOCK_TIMEOUT", "")
    if raw:
        try:
            return max(float(raw), 0.0)
        except ValueError:
            pass
    return _DEFAULT_TIMEOUT_S


def _lock_ops() -> tuple[Callable[[int], None], Callable[[int], None]] | None:
    """Return (try_lock, unlock) for this platform, or None when unsupported."""
    try:
        import fcntl

        def _try_lock(fd: int) -> None:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

        def _unlock(fd: int) -> None:
            fcntl.flock(fd, fcntl.LOCK_UN)

        return _try_lock, _unlock
    except ImportError:
        pass
    try:
        import msvcrt

        def _try_lock_nt(fd: int) -> None:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

        def _unlock_nt(fd: int) -> None:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

        return _try_lock_nt, _unlock_nt
    except ImportError:
        return None


@contextmanager
def graph_write_lock(
    root: Path | str, *, timeout_s: float | None = None,
) -> Iterator[None]:
    """Hold the exclusive graph write lock for *root* while the body runs.

    Blocks (polling) until the lock is free, then yields. Raises
    :class:`GraphWriteLockTimeout` after ``timeout_s`` seconds (default
    60, overridable via ``WELD_GRAPH_LOCK_TIMEOUT``). On a platform with
    neither ``fcntl`` nor ``msvcrt`` the body runs unlocked -- degraded
    to the pre-lock behavior rather than refusing to mutate at all.
    """
    ops = _lock_ops()
    if ops is None:
        yield
        return
    try_lock, unlock = ops
    timeout = _resolve_timeout_s(timeout_s)
    lock_dir = Path(root) / ".weld"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / GRAPH_WRITE_LOCK_FILENAME
    deadline = time.monotonic() + timeout
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        while True:
            try:
                try_lock(fd)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise GraphWriteLockTimeout(lock_path, timeout) from None
                time.sleep(_POLL_INTERVAL_S)
        try:
            yield
        finally:
            try:
                unlock(fd)
            except OSError:
                pass  # process exit releases it regardless
    finally:
        os.close(fd)
