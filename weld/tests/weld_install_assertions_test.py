"""Local mirror of the install-test CI assertions.

The `.github/workflows/install-test.yml` CI workflow runs three assertions
after `install.sh`:

    1. `wd --version` exits 0
    2. `wd discover --help` exits 0
    3. The `wd --version` output contains the contents of the `VERSION`
       file at the repo root.

These assertions previously only ran in CI, so any regression in the
top-level CLI flag handling was caught after publish instead of before.
This Bazel test reproduces the same three assertions in-process so
local verification catches them before a publish.

The test invokes `weld.cli.main` directly (mirroring
``weld_brief_cli_test.py``) so it runs reliably in the Bazel sandbox
without needing a pip install.
"""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

# Ensure the weld package is importable from the repo root when running
# outside of Bazel (e.g., pytest). Bazel's py_test handles this via deps.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from weld.cli import main as cli_main  # noqa: E402


def _run_cli(argv: list[str]) -> tuple[int, str]:
    """Run weld.cli.main with argv, returning (exit_code, captured_stdout).

    ``wd discover --help`` calls argparse which raises ``SystemExit(0)``
    after printing, so catch SystemExit and treat it as the exit code.
    """
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            rc = cli_main(argv)
        return (rc or 0), buf.getvalue()
    except SystemExit as exc:
        # argparse's --help prints to stdout (already redirected) then
        # raises SystemExit(0). Preserve the numeric code.
        code = exc.code
        if code is None:
            code = 0
        if not isinstance(code, int):
            code = 1
        return code, buf.getvalue()


class InstallAssertionsTest(unittest.TestCase):
    """Reproduce the install-test.yml CI assertions locally."""

    def test_weld_version_exits_zero(self) -> None:
        """`wd --version` must exit 0 and produce non-empty output."""
        rc, out = _run_cli(["--version"])
        self.assertEqual(rc, 0, f"wd --version exited non-zero: {rc}")
        self.assertTrue(
            out.strip(),
            "wd --version produced empty output",
        )

    def test_weld_short_version_flag_exits_zero(self) -> None:
        """The `-V` short form documented in cli.py must also exit 0."""
        rc, out = _run_cli(["-V"])
        self.assertEqual(rc, 0, f"wd -V exited non-zero: {rc}")
        self.assertTrue(out.strip(), "wd -V produced empty output")

    def test_weld_discover_help_exits_zero(self) -> None:
        """`wd discover --help` must exit 0 (argparse SystemExit(0))."""
        rc, out = _run_cli(["discover", "--help"])
        self.assertEqual(
            rc,
            0,
            f"wd discover --help exited non-zero: {rc}",
        )
        # argparse help starts with "usage:"; guard against the help body
        # accidentally going empty.
        self.assertIn(
            "usage:",
            out.lower(),
            "wd discover --help did not print a usage line",
        )

    def test_weld_version_matches_version_file(self) -> None:
        """The `wd --version` output must contain the VERSION string.

        This mirrors the third step of install-test.yml:

            expected="$(cat VERSION)"
            actual="$(wd --version)"
            echo "${actual}" | grep -qF "${expected}"
        """
        version_file = _REPO_ROOT / "VERSION"
        self.assertTrue(
            version_file.is_file(),
            f"VERSION file not found at {version_file}; install-test.yml "
            "expects it at the repo root",
        )
        expected = version_file.read_text(encoding="utf-8").strip()
        self.assertTrue(expected, "VERSION file is empty")

        rc, out = _run_cli(["--version"])
        self.assertEqual(rc, 0)
        self.assertIn(
            expected,
            out,
            f"wd --version output {out.strip()!r} does not contain "
            f"expected VERSION {expected!r}",
        )

    def test_weld_version_file_fallback_contains_version(self) -> None:
        """Even when importlib.metadata lookup fails, the fallback path
        (reading VERSION at the repo root) must print the VERSION string.

        This pins the behaviour that the install-test.yml assertion relies
        on, so a refactor of the version-resolution code can't silently
        drop the VERSION-file fallback.
        """
        version_file = _REPO_ROOT / "VERSION"
        expected = version_file.read_text(encoding="utf-8").strip()

        def _raise(_name: str) -> str:
            raise RuntimeError("forced fallback for test")

        with patch("importlib.metadata.version", side_effect=_raise):
            rc, out = _run_cli(["--version"])
        self.assertEqual(rc, 0)
        self.assertIn(expected, out)


if __name__ == "__main__":
    unittest.main()
