"""Shared helper -- two-layer hardening around pip-wheel test calls.

Layer A -- pip cache isolation
    Each ``pip wheel`` invocation gets a per-invocation ``PIP_CACHE_DIR``
    under the supplied test tmpdir, plus ``PIP_NO_CACHE_DIR=1``. The
    cross-test pip cache at ``~/.cache/pip`` was identified as the most
    likely race seed in the source-pollution investigation: concurrent
    wheel builds for the same package could resolve their shared cache
    entries back to live source via Bazel's hardlinked execroot.

Layer B -- content perimeter
    For the duration of the with-block, snapshot the bytes of each
    protected file. On exit -- including on exception paths -- if a
    file's bytes changed, restore the snapshot and raise loudly. Any
    producer that reaches back to live source through the execroot
    hardlink and rewrites the file is caught at a hard-fail-loud
    boundary, and the live tree is self-healed back to its pre-call
    bytes -- the same regression class the perimeter SHA guard detects,
    surfaced eagerly at the test boundary.

    This layer deliberately does **not** ``chmod`` the protected files.
    The historical chmod-to-``0o444`` perimeter mutated file *mode*, and
    because Bazel hardlinks the runfiles/execroot copies to a single
    shared inode, ``chmod`` on the live ``weld/__init__.py`` flipped the
    read-only bit on the very inode the sandbox runner must copy for
    *other* concurrently-starting tests -- yielding intermittent
    "Could not copy inputs into sandbox: .../weld/__init__.py
    (Permission denied)" failures (bd ck8l), and a stuck-``0o444`` leak
    on the worktree source in linked worktrees (bd 5ko1). A content
    snapshot touches no mode and shares no inode state, so that leak is
    structurally impossible while the protective intent is preserved
    (and strengthened: a byte rewrite is caught even when the producer
    would have bypassed a mode bit).

Both layers are wired together in :func:`hermetic_pip_wheel`. Callers
that want only the cache-isolation half pass ``protect=None``.

Design notes
------------

* The helper never mutates ``os.environ``. The yielded env dict is
  merged into the caller's ``subprocess.run(env=...)`` argument, so a
  flaky test cannot leak ``PIP_CACHE_DIR`` onto subsequent tests in the
  same Bazel runner.
* Missing protected paths are silently skipped. The two production
  consumers always pass an existing path, but the contract keeps the
  helper safe against drift (e.g., an installed-package smoke test
  running where the live source is not on disk).
* Byte snapshots are captured *inside* the with-block so a caller that
  pre-mutates a file outside the block sees the right restore target.
* File *modes* are never read or written: the perimeter must not
  perturb the mode of an inode that Bazel shares across the
  runfiles/execroot hardlink graph.
"""

from __future__ import annotations

import contextlib
import tempfile
from pathlib import Path
from typing import Iterable, Iterator


class SourcePerimeterViolation(AssertionError):
    """Raised when a protected file's bytes changed inside the perimeter.

    Subclasses :class:`AssertionError` so a violation reads as a test
    failure (the perimeter exists purely as a test boundary) while still
    being catchable by a more specific ``except`` if a future caller
    wants to assert on it directly.
    """


def pip_wheel_env(test_tmpdir: Path) -> dict[str, str]:
    """Return env vars that pin pip to a fresh, hermetic cache.

    Creates a unique cache directory under *test_tmpdir* and returns the
    env keys that pip honors:

    * ``PIP_NO_CACHE_DIR=1`` -- disable cache reads/writes. Belt to the
      ``PIP_CACHE_DIR`` braces below in case a future pip version
      changes which knob wins.
    * ``PIP_CACHE_DIR`` -- pointed at a freshly minted dir so even if
      the no-cache flag is somehow bypassed, the cache lives in the
      test's own tmpdir and dies with it.

    The two together break the cross-test cache sharing that was the
    most likely race seed in the source-pollution investigation.
    """
    cache_dir = Path(tempfile.mkdtemp(prefix="pip-cache-", dir=test_tmpdir))
    return {
        "PIP_NO_CACHE_DIR": "1",
        "PIP_CACHE_DIR": str(cache_dir),
    }


@contextlib.contextmanager
def content_perimeter(targets: Iterable[Path]) -> Iterator[None]:
    """Snapshot each target's bytes inside the block; verify + heal on exit.

    On enter, the bytes of every existing target are captured. On exit
    -- in a ``finally`` so an exception inside the with-body cannot
    suppress the check -- each target is re-read; if its bytes differ
    from the snapshot the original bytes are restored (self-healing the
    live source tree) and a :class:`SourcePerimeterViolation` is raised.

    Unlike the historical chmod-to-``0o444`` perimeter, this never reads
    or writes file *mode*. Bazel hardlinks the runfiles/execroot copies
    of a source file to one shared inode, so a ``chmod`` on the live
    file leaked the read-only bit onto the inode a concurrently-starting
    sandboxed test had to copy (bd ck8l / 5ko1). A byte snapshot shares
    no inode mode state, so that leak cannot occur.

    Missing targets are silently skipped -- callers can pass a path that
    may not exist on disk (e.g., an installed-package smoke test running
    where the live source is absent) without branching.

    Yields ``None``.
    """
    # Capture original bytes for the files that exist; remember the exact
    # path so the verify/restore on exit targets the same file the test
    # used. Bytes (not mode) are the protected invariant.
    saved: list[tuple[Path, bytes]] = []
    for path in targets:
        try:
            saved.append((path, path.read_bytes()))
        except FileNotFoundError:
            # Missing target -- skip silently (documented contract).
            continue
    try:
        yield
    finally:
        violations: list[Path] = []
        for path, original in saved:
            try:
                current = path.read_bytes()
            except FileNotFoundError:
                # The producer deleted the file outright -- that is a
                # violation too; restore it and record the path.
                path.write_bytes(original)
                violations.append(path)
                continue
            if current != original:
                # Self-heal the live tree, then flag the violation so the
                # offending producer fails loudly rather than silently
                # shipping a corrupted source file.
                path.write_bytes(original)
                violations.append(path)
        if violations:
            joined = ", ".join(str(p) for p in violations)
            raise SourcePerimeterViolation(
                "pip wheel mutated protected source file(s) "
                f"({joined}); original bytes have been restored. "
                "A producer reached back to the live source tree -- "
                "see the source-pollution regression class."
            )


@contextlib.contextmanager
def hermetic_pip_wheel(
    *,
    test_tmpdir: Path,
    protect: Iterable[Path] | None = None,
) -> Iterator[dict[str, str]]:
    """Combine :func:`pip_wheel_env` and :func:`content_perimeter`.

    Parameters
    ----------
    test_tmpdir:
        Per-test temporary directory. The hermetic pip cache lives
        under this dir and dies with it.
    protect:
        Optional iterable of files whose bytes must not change for the
        duration of the block. If any do, they are restored and a
        :class:`SourcePerimeterViolation` is raised on exit. When
        ``None`` (the default), only the cache isolation is active --
        useful for callers that only want Approach A.

    Yields
    ------
    dict
        Env keys that the caller should merge into
        ``subprocess.run(env=...)`` when invoking pip.
    """
    env = pip_wheel_env(test_tmpdir)
    target_list = list(protect) if protect is not None else []
    with content_perimeter(target_list):
        yield env
