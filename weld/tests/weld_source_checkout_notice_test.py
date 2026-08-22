"""Unit + end-to-end tests for the shadowed-source-checkout notice (bd emmg).

The invariant under test: when the current directory sits inside a weld
*source checkout* and the ``weld`` package actually executing is some other
copy, ``wd`` says so once on stderr -- and says nothing in every other
situation. stdout is never touched, so a ``--json`` payload stays parseable.

The detection matrix is driven against synthetic checkouts (a directory with
``VERSION`` and ``weld/_version.py``) rather than the ambient tree, so the
tests answer the same way whether they run from a source checkout, from an
installed wheel, or from a Bazel runfiles tree.
"""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from weld._source_checkout_notice import (
    ENV_VAR,
    emit_source_checkout_notice,
    find_source_checkout_root,
)


def _make_checkout(root: Path, version: str = "9.9.9") -> Path:
    """Materialise the two markers that identify a weld source checkout."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "VERSION").write_text(f"{version}\n", encoding="utf-8")
    (root / "weld").mkdir(exist_ok=True)
    (root / "weld" / "_version.py").write_text("", encoding="utf-8")
    return root


class FindSourceCheckoutRootTest(unittest.TestCase):
    def test_finds_checkout_from_a_nested_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_checkout(Path(tmp))
            nested = root / "docs" / "adrs"
            nested.mkdir(parents=True)
            self.assertEqual(find_source_checkout_root(nested), root)

    def test_returns_none_outside_a_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(find_source_checkout_root(Path(tmp)))

    def test_version_file_alone_is_not_a_weld_checkout(self) -> None:
        """A `VERSION` file is common; the weld package next to it is not."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "VERSION").write_text("1.2.3\n", encoding="utf-8")
            self.assertIsNone(find_source_checkout_root(root))

    def test_nearest_checkout_wins_when_nested(self) -> None:
        """A worktree inside a checkout must resolve to the worktree."""
        with tempfile.TemporaryDirectory() as tmp:
            outer = _make_checkout(Path(tmp), version="1.0.0")
            inner = outer / "worktrees" / "feature-x"
            inner.mkdir(parents=True)
            _make_checkout(inner, version="2.0.0")
            self.assertEqual(find_source_checkout_root(inner), inner)

    def test_missing_directory_is_not_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gone = Path(tmp) / "deleted" / "cwd"
            self.assertIsNone(find_source_checkout_root(gone))


class EmitSourceCheckoutNoticeTest(unittest.TestCase):
    def _emit(self, cwd: Path, package_dir: Path, **kwargs: object) -> str:
        buf = io.StringIO()
        emitted = emit_source_checkout_notice(
            cwd=cwd, package_dir=package_dir, stream=buf, **kwargs
        )
        self.assertEqual(emitted, bool(buf.getvalue()))
        return buf.getvalue()

    def test_emits_one_line_naming_both_sides(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_checkout(Path(tmp) / "repo")
            installed = Path(tmp) / "site-packages" / "weld"
            installed.mkdir(parents=True)
            out = self._emit(root, installed, running_version="0.21.0")
        self.assertEqual(out.count("\n"), 1, f"not a single line: {out!r}")
        self.assertTrue(out.startswith("[weld] "))
        self.assertIn("0.21.0", out)
        self.assertIn(str(installed), out)
        self.assertIn(str(root), out)
        self.assertIn("9.9.9", out)

    def test_names_the_escape_hatch_and_the_silencer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_checkout(Path(tmp) / "repo")
            installed = Path(tmp) / "elsewhere"
            installed.mkdir()
            out = self._emit(root, installed)
        self.assertIn("python3 -m weld", out)
        self.assertIn(ENV_VAR, out)

    def test_fires_even_when_the_two_versions_match(self) -> None:
        """The trigger is path identity, and it has to be.

        `VERSION` moves at release time, so every unversioned change on top
        of a release -- all of development -- reads as "same version" while
        being just as unexercised. A version comparison would go quiet in
        exactly the window the notice exists for.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_checkout(Path(tmp) / "repo", version="1.2.3")
            installed = Path(tmp) / "site-packages" / "weld"
            installed.mkdir(parents=True)
            out = self._emit(root, installed, running_version="1.2.3")
        self.assertIn("1.2.3", out)
        self.assertEqual(out.count("\n"), 1)

    def test_silent_when_the_running_package_is_the_checkouts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_checkout(Path(tmp))
            self.assertEqual(self._emit(root, root / "weld"), "")

    def test_silent_when_the_package_path_is_a_symlink_to_the_checkout(
        self,
    ) -> None:
        """An editable install may be reached through a symlinked path."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_checkout(Path(tmp) / "repo")
            link = Path(tmp) / "link-to-weld"
            try:
                link.symlink_to(root / "weld", target_is_directory=True)
            except (OSError, NotImplementedError):  # pragma: no cover
                self.skipTest("symlinks unavailable on this platform")
            self.assertEqual(self._emit(root, link), "")

    def test_silent_outside_a_source_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plain = Path(tmp) / "some-project"
            plain.mkdir()
            self.assertEqual(self._emit(plain, Path(tmp) / "weld"), "")

    def test_env_var_off_values_suppress(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_checkout(Path(tmp) / "repo")
            installed = Path(tmp) / "elsewhere"
            installed.mkdir()
            for value in ("off", "0", "false", "no", "disabled", "OFF"):
                with self.subTest(value=value):
                    with mock.patch.dict(os.environ, {ENV_VAR: value}):
                        self.assertEqual(self._emit(root, installed), "")

    def test_other_env_values_do_not_suppress(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_checkout(Path(tmp) / "repo")
            installed = Path(tmp) / "elsewhere"
            installed.mkdir()
            with mock.patch.dict(os.environ, {ENV_VAR: "on"}):
                self.assertNotEqual(self._emit(root, installed), "")

    def test_unreadable_version_degrades_instead_of_raising(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_checkout(Path(tmp) / "repo")
            (root / "VERSION").write_bytes(b"\xff\xfe not utf-8")
            installed = Path(tmp) / "elsewhere"
            installed.mkdir()
            out = self._emit(root, installed)
        self.assertIn("unknown", out)
        self.assertEqual(out.count("\n"), 1)

    def test_absurdly_long_version_is_rejected(self) -> None:
        """The VERSION file has no schema; it must not become a megaphone."""
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_checkout(Path(tmp) / "repo")
            (root / "VERSION").write_text("9" * 500, encoding="utf-8")
            installed = Path(tmp) / "elsewhere"
            installed.mkdir()
            out = self._emit(root, installed)
        self.assertNotIn("9" * 200, out)
        self.assertEqual(out.count("\n"), 1)

    def test_unresolvable_running_version_degrades(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_checkout(Path(tmp) / "repo")
            installed = Path(tmp) / "elsewhere"
            installed.mkdir()
            with mock.patch(
                "weld._source_checkout_notice.weld_version", return_value=None
            ):
                out = self._emit(root, installed)
        self.assertIn("unknown", out)


class CliEndToEndTest(unittest.TestCase):
    """Drive the real dispatcher: the notice must reach stderr, not stdout."""

    def _run_version_from(self, cwd: Path) -> tuple[int, str, str]:
        from weld import cli

        out, err = io.StringIO(), io.StringIO()
        previous = os.getcwd()
        os.chdir(cwd)
        try:
            with mock.patch.dict(os.environ, {"WELD_TELEMETRY": "off"}):
                with redirect_stdout(out), redirect_stderr(err):
                    code = cli.main(["--version"])
        finally:
            os.chdir(previous)
        return code, out.getvalue(), err.getvalue()

    def test_notice_rides_stderr_while_stdout_stays_pure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # The running `weld` package lives in this test's own tree, never
            # under the synthetic checkout, so the mismatch is genuine.
            root = _make_checkout(Path(tmp), version="42.0.0")
            code, out, err = self._run_version_from(root)
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("wd "), out)
        self.assertNotIn("[weld]", out)
        self.assertIn("42.0.0", err)

    def test_silent_when_not_in_a_weld_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = self._run_version_from(Path(tmp))
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("wd "), out)
        self.assertNotIn(ENV_VAR, err)


if __name__ == "__main__":
    unittest.main()
