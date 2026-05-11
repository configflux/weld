"""Shared helper -- two-layer hardening around pip-wheel test calls.

Layer A -- pip cache isolation
    Each ``pip wheel`` invocation gets a per-invocation ``PIP_CACHE_DIR``
    under the supplied test tmpdir, plus ``PIP_NO_CACHE_DIR=1``. The
    cross-test pip cache at ``~/.cache/pip`` was identified as the most
    likely race seed in the source-pollution investigation: concurrent
    wheel builds for the same package could resolve their shared cache
    entries back to live source via Bazel's hardlinked execroot.

Layer B -- read-only perimeter
    For the duration of the with-block, chmod the protected files to
    ``0o444``. The original mode is captured per file and restored
    deterministically on exit, including on exception paths. Any future
    producer that reaches back to live source through the execroot
    hardlink hits a deterministic ``EACCES`` instead of silently
    overwriting bytes -- the same regression class that the perimeter
    SHA guard already detects, but at a hard-fail-loud boundary instead
    of after-the-fact detection.

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
* Original modes are captured *inside* the with-block so a caller that
  pre-chmods a file outside the block sees the right restore target.
"""

from __future__ import annotations

import contextlib
import stat
import tempfile
from pathlib import Path
from typing import Iterable, Iterator


# Mode applied while the perimeter is active. ``0o444`` (read-only for
# owner/group/other) lets legitimate readers (pip / setuptools loading
# the package source) succeed, while any write call hits ``EACCES``.
_READONLY_MODE = 0o444


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
def readonly_perimeter(targets: Iterable[Path]) -> Iterator[None]:
    """Chmod each target to read-only inside the block; restore on exit.

    The original mode is captured per file. Restoration runs in a
    ``finally`` block so an exception inside the with-body cannot leave
    the source tree write-disabled. Missing targets are silently
    skipped -- callers can pass a path that may not exist on disk
    without branching.

    Yields ``None``. The caller can read the chmod state via
    ``stat.S_IMODE(path.stat().st_mode)`` if needed.
    """
    # Capture original modes for the files that exist; remember the
    # exact path so restore touches the same inode the test used.
    saved: list[tuple[Path, int]] = []
    try:
        for path in targets:
            if not path.exists():
                continue
            saved.append((path, stat.S_IMODE(path.stat().st_mode)))
            path.chmod(_READONLY_MODE)
        yield
    finally:
        for path, mode in saved:
            # ``chmod`` honors the inode even if the path was renamed
            # mid-test; if the file vanished entirely (extremely
            # unlikely in our test contexts), swallow the error -- the
            # tracked-diff guard at the gate boundary still catches
            # any stray mutation.
            try:
                path.chmod(mode)
            except FileNotFoundError:
                pass


@contextlib.contextmanager
def hermetic_pip_wheel(
    *,
    test_tmpdir: Path,
    protect: Iterable[Path] | None = None,
) -> Iterator[dict[str, str]]:
    """Combine :func:`pip_wheel_env` and :func:`readonly_perimeter`.

    Parameters
    ----------
    test_tmpdir:
        Per-test temporary directory. The hermetic pip cache lives
        under this dir and dies with it.
    protect:
        Optional iterable of files to flip read-only for the duration
        of the block. When ``None`` (the default), only the cache
        isolation is active -- useful for callers that only want
        Approach A.

    Yields
    ------
    dict
        Env keys that the caller should merge into
        ``subprocess.run(env=...)`` when invoking pip.
    """
    env = pip_wheel_env(test_tmpdir)
    target_list = list(protect) if protect is not None else []
    with readonly_perimeter(target_list):
        yield env
