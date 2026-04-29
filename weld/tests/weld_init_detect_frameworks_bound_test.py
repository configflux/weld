"""Bounded-scan tests for ``detect_frameworks``.

ADR 0027 follow-up: ``detect_frameworks`` must bound its per-file work via
three early-exit rules (per-file, per-language, sampling cap) so ``wd init``
scales on large monorepos. Correctness for small repos is preserved.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.init_detect import (  # noqa: E402
    _MAX_FILES_PER_LANG,
    detect_frameworks,
    scan_files,
)


def _count_py_reads(target: Path):
    calls = {"count": 0}
    real_read = Path.read_text

    def counting(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if self.suffix == ".py":
            calls["count"] += 1
        return real_read(self, *args, **kwargs)

    return calls, counting


class DetectFrameworksCorrectnessTest(unittest.TestCase):
    """Detection on small, well-formed repos must be unchanged."""

    def test_detects_multiple_frameworks_across_languages(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "app.py").write_text("from fastapi import FastAPI\n")
            (root / "models.py").write_text("from sqlalchemy import Column\n")
            (root / "server.ts").write_text("import express from 'express'\n")

            files = scan_files(root)
            detected = detect_frameworks(root, files)
            frameworks = {fw for fw, _, _ in detected}

        self.assertEqual(frameworks, {"FastAPI", "SQLAlchemy", "Express"})

    def test_returns_strategy_and_relative_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "service.py").write_text("from flask import Flask\n")

            files = scan_files(root)
            detected = detect_frameworks(root, files)

        self.assertEqual(len(detected), 1)
        framework, strategy, rel = detected[0]
        self.assertEqual(framework, "Flask")
        self.assertEqual(strategy, "python_module")
        self.assertEqual(rel, "service.py")

    def test_detects_gin_via_canonical_go_import_path(self) -> None:
        """Go projects import gin via ``"github.com/gin-gonic/gin"``;
        the older Python-style ``from gin`` / ``import gin`` patterns
        never matched real Go source."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "main.go").write_text(
                'package main\n\n'
                'import (\n'
                '\t"fmt"\n'
                '\t"github.com/gin-gonic/gin"\n'
                ')\n\n'
                'func main() {\n'
                '\tr := gin.Default()\n'
                '\tfmt.Println(r)\n'
                '}\n',
            )
            files = scan_files(root)
            detected = detect_frameworks(root, files)
            by_fw = {fw: (s, p) for fw, s, p in detected}

        self.assertIn("Gin", by_fw)
        strategy, rel = by_fw["Gin"]
        self.assertEqual(strategy, "go_module")
        self.assertEqual(rel, "main.go")

    def test_does_not_match_gin_in_a_go_comment(self) -> None:
        """A commented-out reference must not register as detection."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "notes.go").write_text(
                'package notes\n\n'
                '// We considered "github.com/gin-gonic/gin" but settled on net/http.\n',
            )
            files = scan_files(root)
            detected = detect_frameworks(root, files)

        self.assertEqual([fw for fw, _, _ in detected if fw == "Gin"], [])

    def test_detects_gin_via_single_line_import(self) -> None:
        """The non-grouped ``import "..."`` form must still match.

        Pins the positive case opposite to the parenthesised import block
        already covered by ``test_detects_gin_via_canonical_go_import_path``.
        """
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "main.go").write_text(
                'package main\n\n'
                'import "github.com/gin-gonic/gin"\n\n'
                'func main() { _ = gin.Default() }\n',
            )
            files = scan_files(root)
            detected = detect_frameworks(root, files)

        self.assertIn("Gin", {fw for fw, _, _ in detected})

    def test_does_not_match_gin_inside_block_comment(self) -> None:
        """An interior block-comment line that contains the canonical path
        must NOT register as detection. The Go matcher previously rejected
        only line-prefix ``#`` and ``//`` comments, so block-comment bodies
        produced false positives via substring containment."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "doc.go").write_text(
                'package doc\n\n'
                '/*\n'
                ' * Thanks to "github.com/gin-gonic/gin" contributors.\n'
                ' */\n',
            )
            files = scan_files(root)
            detected = detect_frameworks(root, files)

        self.assertEqual([fw for fw, _, _ in detected if fw == "Gin"], [])

    def test_does_not_match_gin_in_backtick_raw_string(self) -> None:
        """A backtick raw-string literal containing the canonical path
        must NOT register. The raw-string is data, not an import."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "data.go").write_text(
                'package data\n\n'
                'var s = `github.com/gin-gonic/gin`\n',
            )
            files = scan_files(root)
            detected = detect_frameworks(root, files)

        self.assertEqual([fw for fw, _, _ in detected if fw == "Gin"], [])

    def test_does_not_match_gin_in_plain_string_literal(self) -> None:
        """A plain double-quoted string-literal assignment outside an import
        block must NOT register as detection."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "plain.go").write_text(
                'package plain\n\n'
                'var s = "github.com/gin-gonic/gin"\n',
            )
            files = scan_files(root)
            detected = detect_frameworks(root, files)

        self.assertEqual([fw for fw, _, _ in detected if fw == "Gin"], [])


class DetectFrameworksBoundedScanTest(unittest.TestCase):
    """ADR 0027: per-language early exit and sampling cap."""

    def test_per_language_early_exit_after_all_frameworks_seen(self) -> None:
        """Once every Python framework has been seen, no further .py files
        are opened."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # The first file (alphabetically) imports every detected
            # Python framework so per-language outstanding empties on it.
            (root / "a_kitchen_sink.py").write_text(
                "from fastapi import FastAPI\n"
                "from django import db\n"
                "from flask import Flask\n"
                "from sqlalchemy import Column\n"
                "from pydantic import BaseModel\n"
                "from prisma import Prisma\n",
            )
            # 50 trailing .py files; none should be opened.
            for i in range(50):
                (root / f"z_extra_{i:03d}.py").write_text(f"x = {i}\n")

            files = sorted(scan_files(root))
            calls, counting = _count_py_reads(root)

            with patch.object(Path, "read_text", counting):
                detected = detect_frameworks(root, files)

        self.assertEqual(
            {fw for fw, _, _ in detected},
            {"FastAPI", "Django", "Flask", "SQLAlchemy", "Pydantic", "Prisma"},
        )
        self.assertEqual(
            calls["count"], 1,
            f"Expected 1 .py read after early exit; got {calls['count']}",
        )

    def test_sampling_cap_limits_per_language_files_read(self) -> None:
        """More than ``_MAX_FILES_PER_LANG`` no-import .py files trigger the
        sampling cap; ``read_text`` is called at most that many times for
        Python files."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            extra = 200
            for i in range(_MAX_FILES_PER_LANG + extra):
                (root / f"mod{i:05d}.py").write_text(f"x = {i}\n")

            files = scan_files(root)
            calls, counting = _count_py_reads(root)

            with patch.object(Path, "read_text", counting):
                detect_frameworks(root, files)

        self.assertLessEqual(
            calls["count"],
            _MAX_FILES_PER_LANG,
            (
                f"Read {calls['count']} .py files; cap is "
                f"{_MAX_FILES_PER_LANG}"
            ),
        )

    def test_per_file_early_exit_breaks_outer_line_loop(self) -> None:
        """After every Python framework for a file is detected, the
        outer ``for source_line in text.splitlines()`` loop must break.

        Direct signal: replace ``Path.read_text`` with a wrapper whose
        result behaves like a string but whose ``splitlines()`` yields
        a *generator* that raises after a fixed number of lines. With
        the per-file ``break`` in place, the loop yields six detection
        lines plus exactly one extra line (the break is checked at the
        top of the next iteration). Without the break, the loop would
        consume every line in the file. The tripwire fires only on the
        eighth element so the legitimate one-line lookahead is allowed.
        """

        class _Tripwire(str):
            __slots__ = ()

            def splitlines(self, keepends: bool = False) -> list[str]:  # type: ignore[override]
                lines = str.splitlines(self, keepends)

                def gen():
                    # Permit six detection lines + one lookahead (the
                    # `if not file_remaining: break` is at the top of
                    # each iteration, so the seventh line is fetched
                    # before the break fires). The eighth line MUST
                    # never be reached on a correctly-bounded scan.
                    for i, line in enumerate(lines):
                        if i >= 8:
                            raise AssertionError(
                                f"per-file early exit did not fire: "
                                f"line {i} reached on a kitchen-sink "
                                f"file (max permitted index is 7)",
                            )
                        yield line

                return gen()  # type: ignore[return-value]

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # One framework per line (six lines), then 100 trailing
            # ``x = 1`` lines that the tripwire will guard against.
            (root / "kitchen_sink.py").write_text(
                "from fastapi import FastAPI\n"
                "from django import db\n"
                "from flask import Flask\n"
                "from sqlalchemy import Column\n"
                "from pydantic import BaseModel\n"
                "from prisma import Prisma\n"
                + ("x = 1\n" * 100),
            )

            real_read_text = Path.read_text

            def tripwire_read_text(self, *args, **kwargs):  # type: ignore[no-untyped-def]
                if self.suffix == ".py":
                    return _Tripwire(real_read_text(self, *args, **kwargs))
                return real_read_text(self, *args, **kwargs)

            files = scan_files(root)
            with patch.object(Path, "read_text", tripwire_read_text):
                detected = detect_frameworks(root, files)

        self.assertEqual(
            {fw for fw, _, _ in detected},
            {"FastAPI", "Django", "Flask", "SQLAlchemy", "Pydantic", "Prisma"},
        )


class DetectFrameworksEnvCapOverrideTest(unittest.TestCase):
    """``WELD_INIT_FRAMEWORK_CAP`` env override for forensic re-runs.

    Acceptance (from tracked issue):
      - unset env preserves the default ``_MAX_FILES_PER_LANG`` cap
      - ``=0`` disables the cap (unbounded scan)
      - ``=N`` for N>0 sets a custom cap
      - non-numeric / empty / negative values silently fall back to default
    """

    def _count_reads(self, env_value: str | None, file_count: int) -> int:
        """Build ``file_count`` no-import .py files, scan with the env
        override set to ``env_value`` (``None`` removes it), and return
        the observed ``read_text`` call count for .py files."""
        env_patch = (
            {"WELD_INIT_FRAMEWORK_CAP": env_value}
            if env_value is not None else {}
        )
        with patch.dict(os.environ, env_patch, clear=False):
            if env_value is None:
                os.environ.pop("WELD_INIT_FRAMEWORK_CAP", None)
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                for i in range(file_count):
                    (root / f"mod{i:05d}.py").write_text(f"x = {i}\n")
                files = scan_files(root)
                calls, counting = _count_py_reads(root)
                with patch.object(Path, "read_text", counting):
                    detect_frameworks(root, files)
                return calls["count"]

    def test_env_override_zero_disables_cap(self) -> None:
        """``=0`` reads every .py file (unbounded), exceeding default cap."""
        reads = self._count_reads("0", _MAX_FILES_PER_LANG + 50)
        self.assertGreater(reads, _MAX_FILES_PER_LANG)

    def test_env_override_custom_positive_cap(self) -> None:
        """``=5`` reads at most 5 .py files even when many more exist."""
        self.assertLessEqual(self._count_reads("5", 50), 5)

    def test_env_override_non_numeric_falls_back_silently(self) -> None:
        """A non-numeric value MUST NOT crash; default cap is enforced."""
        reads = self._count_reads("foo", _MAX_FILES_PER_LANG + 25)
        self.assertLessEqual(reads, _MAX_FILES_PER_LANG)

    def test_env_override_empty_falls_back(self) -> None:
        """Empty value (``=``) is treated as unset; default cap applies."""
        reads = self._count_reads("", _MAX_FILES_PER_LANG + 10)
        self.assertLessEqual(reads, _MAX_FILES_PER_LANG)

    def test_env_override_negative_falls_back(self) -> None:
        """Negative values are nonsensical; fall back to default cap."""
        reads = self._count_reads("-5", _MAX_FILES_PER_LANG + 10)
        self.assertLessEqual(reads, _MAX_FILES_PER_LANG)


if __name__ == "__main__":
    unittest.main()
