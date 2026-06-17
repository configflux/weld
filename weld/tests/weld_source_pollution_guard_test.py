"""Source-tree pollution guard: pip wheel must not mutate live source.

Regression test for a known regression class: full-scope local task gate
runs intermittently emptied ``weld/__init__.py`` as a side effect of a
test, and the gate's tracked-diff guard then failed the run. A prior fix
added the tracked-diff guard (a perimeter detector) but did not remove
the producer; this test pins the producer-side invariant so a recurrence
isolates here rather than via the gate.

Invariant pinned:

    SHA-256(weld/__init__.py) before pip wheel == SHA-256 after pip wheel

We hash bytes (not ``read_text`` round trips) so a CRLF/BOM/encoding
normalization fails the check the same way an empty rewrite would.

Skipped cleanly when ``ensurepip`` / ``pip`` are not available so local
runs without a Python toolchain stay green; CI Ubuntu runners exercise
the full path.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Resolve repo root for source-tree imports when running outside Bazel.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from weld.tests._pip_wheel_hardening import hermetic_pip_wheel  # noqa: E402
from weld.tests._source_tree_copy import (  # noqa: E402
    copy_weld_source,
    wheel_build_allowlist,
)


def _weld_source_root() -> Path:
    return _REPO_ROOT / "weld"


def _sha256_bytes(path: Path) -> str:
    """Return the SHA-256 hex digest of *path*'s bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ensurepip_available() -> bool:
    try:
        import ensurepip  # noqa: F401  # type: ignore[import-not-found]
    except ImportError:
        return False
    return True


class PipWheelSourceTreeIntegrityTest(unittest.TestCase):
    """Pip wheel of weld/ must not mutate ``weld/__init__.py`` on disk."""

    def setUp(self) -> None:
        if not _ensurepip_available():
            self.skipTest(
                "ensurepip not available; wheel-build is CI-only"
            )
        if shutil.which("pip") is None and shutil.which("pip3") is None:
            try:
                import pip  # noqa: F401  # type: ignore[import-not-found]
            except ImportError:
                self.skipTest(
                    "pip is not importable; wheel-build is CI-only"
                )

    def test_pip_wheel_does_not_mutate_live_init_py(self) -> None:
        weld_root = _weld_source_root()
        init_path = weld_root / "__init__.py"
        self.assertTrue(
            init_path.is_file(),
            f"weld/__init__.py not found at {init_path}",
        )

        before_sha = _sha256_bytes(init_path)
        before_size = init_path.stat().st_size

        with tempfile.TemporaryDirectory(prefix="weld-src-pollution-") as tmp:
            tmp_path = Path(tmp)
            package_src = tmp_path / "weld-src"
            # Explicit allowlist via the shared helper -- keeps the
            # per-test copy under ~5 MB by skipping weld/tests/ (~6 MB
            # of fixtures) and __pycache__.
            copy_weld_source(
                weld_root, package_src,
                allowlist=wheel_build_allowlist(weld_root),
            )
            dist_dir = tmp_path / "dist"
            dist_dir.mkdir()

            # Two-layer hardening around the pip wheel call:
            #   A. per-invocation PIP_CACHE_DIR + PIP_NO_CACHE_DIR=1
            #      so the cross-test pip cache cannot seed a race.
            #   B. content perimeter on live weld/__init__.py -- its
            #      bytes are snapshotted for the call and, if a producer
            #      reaches back through Bazel's hardlinked execroot and
            #      rewrites them, they are restored and the test fails
            #      loudly. (The perimeter never chmods the file: a mode
            #      flip leaked onto the shared runfiles inode and raced
            #      concurrent sandbox copies into EACCES -- bd ck8l.)
            with hermetic_pip_wheel(
                test_tmpdir=tmp_path, protect=[init_path],
            ) as pip_env:
                proc = subprocess.run(
                    [
                        sys.executable, "-m", "pip", "wheel",
                        "--quiet", "--no-deps", str(package_src),
                        "-w", str(dist_dir),
                    ],
                    env={**os.environ, **pip_env},
                    capture_output=True, text=True, check=False,
                )
            self.assertEqual(
                proc.returncode, 0,
                f"pip wheel failed (rc={proc.returncode}); "
                f"stdout={proc.stdout!r} stderr={proc.stderr!r}",
            )

        after_sha = _sha256_bytes(init_path)
        after_size = init_path.stat().st_size

        # Equality on SHA is the actual contract; surfacing size + the
        # short hex digests in the failure message keeps the diff
        # actionable in CI logs without re-running.
        self.assertEqual(
            after_sha,
            before_sha,
            (
                "weld/__init__.py byte content changed during pip wheel.\n"
                f"  before: size={before_size}B sha256={before_sha}\n"
                f"  after:  size={after_size}B sha256={after_sha}\n"
                "See the source-pollution regression class."
            ),
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
