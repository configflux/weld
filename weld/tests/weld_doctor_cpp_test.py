"""Tests for ``wd doctor --cpp`` (ADR 0057 Wave 3).

The doctor report exposes three pieces of state to the user:

  1. Whether the ``[cpp-libclang]`` extra is importable.
  2. Whether ``compile_commands.json`` is present in the repo.
  3. Coverage: how many ``.cpp``/``.cc`` files in the repo are
     referenced by the database vs. how many exist on disk.

These tests construct a temp tree, write a synthetic database, and
verify the report rows for each scenario. The libclang availability
probe is mocked so the suite runs identically with or without the
extra installed.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


from dataclasses import dataclass  # noqa: E402

from weld import _doctor_cpp  # noqa: E402
from weld.strategies import _cpp_libclang_db as db_mod  # noqa: E402


@dataclass(frozen=True)
class _FakeResult:
    """Stand-in for ``weld.doctor.CheckResult``.

    The doctor entry point passes the dataclass shape; we mirror it so
    the section helper can run isolated from the rest of the doctor.
    """

    level: str
    message: str
    section: str = "Project"
    note_id: str | None = None


def _write_db(tmp: Path, entries: list[dict]) -> Path:
    db = tmp / "compile_commands.json"
    db.write_text(json.dumps(entries), encoding="utf-8")
    return db


def _check(root: Path) -> list[_FakeResult]:
    return _doctor_cpp.check_cpp(root, _FakeResult)


# ---------------------------------------------------------------------------
# Coverage reporting
# ---------------------------------------------------------------------------


class DoctorCppReportTest(unittest.TestCase):
    """Report rows reflect the libclang readiness of the tree."""

    def test_reports_libclang_missing_when_extra_not_installed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            with mock.patch.object(db_mod, "is_libclang_available", return_value=False):
                rows = _check(tmp)
            note_msgs = [r.message for r in rows if r.level == "note"]
            self.assertTrue(any("libclang extra not installed" in m for m in note_msgs))

    def test_reports_compile_db_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            rows = _check(tmp)
            self.assertTrue(any(
                "compile_commands.json not found" in r.message for r in rows
            ))

    def test_reports_env_var_state(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            # Env var unset -> "not set" note.
            with mock.patch.dict(
                os.environ, {db_mod.ENABLE_ENV_VAR: ""}, clear=False,
            ):
                rows = _check(tmp)
            messages = " ".join(r.message for r in rows)
            self.assertIn(db_mod.ENABLE_ENV_VAR, messages)
            self.assertIn("not set", messages)

            # Env var set -> "opt-in active" ok.
            with mock.patch.dict(
                os.environ, {db_mod.ENABLE_ENV_VAR: "1"}, clear=False,
            ):
                rows = _check(tmp)
            ok_messages = " ".join(r.message for r in rows if r.level == "ok")
            self.assertIn("opt-in active", ok_messages)

    def test_full_coverage_reports_ok(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            (tmp / "src").mkdir()
            (tmp / "src" / "a.cpp").write_text("int a;\n")
            _write_db(tmp, [
                {"directory": str(tmp), "file": "src/a.cpp",
                 "arguments": ["clang++", "src/a.cpp"]},
            ])
            with mock.patch.object(db_mod, "is_libclang_available", return_value=True):
                rows = _check(tmp)
            ok_messages = " ".join(r.message for r in rows if r.level == "ok")
            self.assertIn("compile-db coverage", ok_messages)
            self.assertIn("(full)", ok_messages)

    def test_partial_coverage_reports_warn(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            (tmp / "src").mkdir()
            (tmp / "src" / "covered.cpp").write_text("int a;\n")
            (tmp / "src" / "uncovered.cpp").write_text("int b;\n")
            _write_db(tmp, [
                {"directory": str(tmp), "file": "src/covered.cpp",
                 "arguments": ["clang++", "src/covered.cpp"]},
            ])
            with mock.patch.object(db_mod, "is_libclang_available", return_value=True):
                rows = _check(tmp)
            warns = [r.message for r in rows if r.level == "warn"]
            self.assertTrue(any("compile-db coverage: 1/2" in m for m in warns))
            self.assertTrue(any("1 uncovered fall back" in m for m in warns))

    def test_zero_coverage_with_sources_reports_note(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            (tmp / "src").mkdir()
            (tmp / "src" / "a.cpp").write_text("int a;\n")
            _write_db(tmp, [])  # empty -> zero coverage
            with mock.patch.object(db_mod, "is_libclang_available", return_value=True):
                rows = _check(tmp)
            notes = [r.message for r in rows if r.level == "note"]
            self.assertTrue(any("compile-db coverage: 0/1" in m for m in notes))


# ---------------------------------------------------------------------------
# CppCoverageReport accessors
# ---------------------------------------------------------------------------


class CoverageReportAccessorsTest(unittest.TestCase):
    """The dataclass exposes computed helpers used by the formatter."""

    def test_db_present_reflects_path(self) -> None:
        report = _doctor_cpp.CppCoverageReport(
            libclang_available=False,
            env_enabled=False,
            db_path=None,
            db_entries=0,
            covered_count=0,
            on_disk_count=0,
        )
        self.assertFalse(report.db_present)
        with_path = _doctor_cpp.CppCoverageReport(
            libclang_available=False,
            env_enabled=False,
            db_path="compile_commands.json",
            db_entries=0,
            covered_count=0,
            on_disk_count=0,
        )
        self.assertTrue(with_path.db_present)

    def test_uncovered_count_is_nonnegative(self) -> None:
        report = _doctor_cpp.CppCoverageReport(
            libclang_available=True,
            env_enabled=True,
            db_path="compile_commands.json",
            db_entries=10,
            covered_count=15,  # more covered than on-disk should not be -5
            on_disk_count=10,
        )
        self.assertEqual(report.uncovered_count, 0)


# ---------------------------------------------------------------------------
# Compile-db stub
# ---------------------------------------------------------------------------


class EmitStubTest(unittest.TestCase):
    """``emit_compile_db_stub`` writes a parseable empty array + README."""

    def test_writes_empty_array_and_readme(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            json_path, readme_path = _doctor_cpp.emit_compile_db_stub(tmp)
            self.assertEqual(json_path.read_text(encoding="utf-8"), "[]\n")
            text = readme_path.read_text(encoding="utf-8")
            self.assertIn("CMAKE_EXPORT_COMPILE_COMMANDS", text)
            self.assertIn("hedron_compile_commands", text)
            # Empty array round-trips through the parser -> dormant.
            self.assertEqual(
                db_mod.parse_entries(json_path, root=tmp), [],
            )

    def test_refuses_to_overwrite_nontrivial_db(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            (tmp / "compile_commands.json").write_text(
                json.dumps([{"file": "a.cpp", "directory": str(tmp),
                              "arguments": ["clang++"]}]),
                encoding="utf-8",
            )
            with self.assertRaises(FileExistsError):
                _doctor_cpp.emit_compile_db_stub(tmp)

    def test_overwrites_empty_array_in_place(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            (tmp / "compile_commands.json").write_text("[]", encoding="utf-8")
            # Idempotent: rewriting the empty array is allowed.
            _doctor_cpp.emit_compile_db_stub(tmp)


if __name__ == "__main__":
    unittest.main()
