"""Tests for the wd scaffold command and helper logic."""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from weld.cli import main as cli_main
from weld.scaffold import scaffold_template

class ScaffoldTemplateTest(unittest.TestCase):
    """Verify template scaffolding behavior."""

    def test_local_strategy_default_output_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            output_path = scaffold_template(
                "local-strategy",
                "smoke_test",
                cwd=root,
            )
            self.assertEqual(output_path, root / ".weld" / "strategies" / "smoke_test.py")
            self.assertTrue(output_path.is_file())

    def test_external_adapter_default_output_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            output_path = scaffold_template(
                "external-adapter",
                "demo_adapter",
                cwd=root,
            )
            self.assertEqual(output_path, root / ".weld" / "adapters" / "demo_adapter.py")
            self.assertTrue(output_path.is_file())

    def test_custom_output_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            output_path = scaffold_template(
                "local-strategy",
                "ignored-name",
                cwd=root,
                output=Path("custom/path/example.py"),
            )
            self.assertEqual(output_path, root / "custom" / "path" / "example.py")
            self.assertTrue(output_path.is_file())

    def test_existing_file_requires_force(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            output_path = root / ".weld" / "strategies" / "smoke_test.py"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text("# existing\n", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                scaffold_template("local-strategy", "smoke_test", cwd=root)

    def test_force_overwrites_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            output_path = root / ".weld" / "strategies" / "smoke_test.py"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text("# existing\n", encoding="utf-8")
            scaffold_template(
                "local-strategy",
                "smoke_test",
                cwd=root,
                force=True,
            )
            self.assertNotEqual(output_path.read_text(encoding="utf-8"), "# existing\n")

    def test_invalid_name_without_output_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with self.assertRaises(ValueError):
                scaffold_template("local-strategy", "bad-name", cwd=root)

class ScaffoldCliDispatchTest(unittest.TestCase):
    """Verify scaffold dispatch from the top-level CLI."""

    def test_cli_dispatches_scaffold(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            output = io.StringIO()
            with patch("sys.stdout", output), patch("pathlib.Path.cwd", return_value=root):
                cli_main(["scaffold", "local-strategy", "demo"])
            written = root / ".weld" / "strategies" / "demo.py"
            self.assertTrue(written.is_file())
            self.assertIn("Wrote .weld/strategies/demo.py", output.getvalue())

    def test_cli_help_mentions_scaffold(self) -> None:
        output = io.StringIO()
        with patch("sys.stdout", output):
            cli_main(["--help"])
        self.assertIn("scaffold", output.getvalue())

if __name__ == "__main__":
    unittest.main()
