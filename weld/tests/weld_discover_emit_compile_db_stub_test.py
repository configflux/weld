"""Tests for ``wd discover --emit-compile-db-stub`` (ADR 0057 Wave 3).

The flag is a one-shot helper that writes a placeholder
``compile_commands.json`` plus a sibling README documenting how to
generate a real database. It exits before running discovery so the
flag composes with no other side effects.

These tests exercise the CLI surface end-to-end (``wd discover`` ->
:func:`weld.discover.main`) and verify the written artefacts plus the
exit code contract.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


from weld.discover import main  # noqa: E402


class DiscoverEmitCompileDbStubTest(unittest.TestCase):
    """The flag writes the stub and short-circuits discovery."""

    def test_writes_stub_and_readme_to_repo_root(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            code = main([str(tmp), "--emit-compile-db-stub"])
            self.assertEqual(code, 0)
            db_path = tmp / "compile_commands.json"
            readme_path = tmp / "compile_commands.README.md"
            self.assertTrue(db_path.is_file())
            self.assertTrue(readme_path.is_file())
            # The stub must be valid JSON (empty array) so the libclang
            # parser stays dormant against it.
            parsed = json.loads(db_path.read_text(encoding="utf-8"))
            self.assertEqual(parsed, [])

    def test_refuses_to_overwrite_real_db(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            (tmp / "compile_commands.json").write_text(
                json.dumps([{
                    "directory": str(tmp),
                    "file": "a.cpp",
                    "arguments": ["clang++", "a.cpp"],
                }]),
                encoding="utf-8",
            )
            code = main([str(tmp), "--emit-compile-db-stub"])
            self.assertEqual(code, 2)

    def test_short_circuits_discovery(self) -> None:
        """The flag exits before discovery so no graph is written.

        Concretely: no ``.weld/`` directory is created, no graph is
        emitted to stdout, and the call returns 0 unconditionally
        (provided the stub itself is writable).
        """
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            self.assertFalse((tmp / ".weld").exists())
            code = main([str(tmp), "--emit-compile-db-stub"])
            self.assertEqual(code, 0)
            self.assertFalse((tmp / ".weld" / "graph.json").exists())


if __name__ == "__main__":
    unittest.main()
