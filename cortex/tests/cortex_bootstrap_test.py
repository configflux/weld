"""Tests for the cortex bootstrap command."""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cortex.bootstrap import bootstrap
from cortex.cli import main as cli_main

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

class BootstrapClaudeTest(unittest.TestCase):
    """Verify bootstrap claude writes the expected files."""

    def test_creates_cortex_readme(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("claude", root, force=True)
            readme = root / ".cortex" / "README.md"
            self.assertTrue(readme.is_file())
            self.assertGreater(len(readme.read_text(encoding="utf-8").strip()), 50)

    def test_creates_claude_command(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("claude", root, force=True)
            cmd_file = root / ".claude" / "commands" / "cortex.md"
            self.assertTrue(cmd_file.is_file())
            content = cmd_file.read_text(encoding="utf-8")
            self.assertIn("cortex brief", content)

class BootstrapCodexTest(unittest.TestCase):
    """Verify bootstrap codex writes the expected files."""

    def test_creates_cortex_readme(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("codex", root, force=True)
            readme = root / ".cortex" / "README.md"
            self.assertTrue(readme.is_file())

    def test_creates_codex_skill(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("codex", root, force=True)
            skill = root / ".codex" / "skills" / "cortex" / "SKILL.md"
            self.assertTrue(skill.is_file())
            content = skill.read_text(encoding="utf-8")
            self.assertIn("cortex brief", content)

class BootstrapOverwriteTest(unittest.TestCase):
    """Verify overwrite/skip behavior."""

    def test_does_not_overwrite_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            readme = root / ".cortex" / "README.md"
            readme.parent.mkdir(parents=True)
            readme.write_text("sentinel", encoding="utf-8")
            output = io.StringIO()
            with patch("sys.stdout", output):
                bootstrap("claude", root, force=False)
            self.assertEqual(readme.read_text(encoding="utf-8"), "sentinel")
            self.assertIn("already exists", output.getvalue())

    def test_force_overwrites(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            readme = root / ".cortex" / "README.md"
            readme.parent.mkdir(parents=True)
            readme.write_text("sentinel", encoding="utf-8")
            bootstrap("claude", root, force=True)
            self.assertNotEqual(readme.read_text(encoding="utf-8"), "sentinel")

class BootstrapDiscoverYamlTest(unittest.TestCase):
    """Verify discover.yaml delegation."""

    def test_skips_existing_discover_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            discover = root / ".cortex" / "discover.yaml"
            discover.parent.mkdir(parents=True)
            discover.write_text("# existing config\n", encoding="utf-8")
            output = io.StringIO()
            with patch("sys.stdout", output):
                bootstrap("claude", root, force=True)
            self.assertEqual(
                discover.read_text(encoding="utf-8"),
                "# existing config\n",
            )
            self.assertIn("already exists", output.getvalue())

class BootstrapTemplatesLoadableTest(unittest.TestCase):
    """Verify all markdown templates exist in the package."""

    def test_cortex_readme_template_exists(self) -> None:
        self.assertTrue((_TEMPLATES_DIR / "cortex_readme.md").is_file())

    def test_cortex_cmd_claude_template_exists(self) -> None:
        self.assertTrue((_TEMPLATES_DIR / "cortex_cmd_claude.md").is_file())

    def test_cortex_skill_codex_template_exists(self) -> None:
        self.assertTrue((_TEMPLATES_DIR / "cortex_skill_codex.md").is_file())

class BootstrapCliDispatchTest(unittest.TestCase):
    """Verify bootstrap dispatch from the top-level CLI."""

    def test_cli_dispatches_bootstrap_claude(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            output = io.StringIO()
            with patch("sys.stdout", output):
                cli_main(["bootstrap", "claude", "--root", td, "--force"])
            cmd_file = Path(td) / ".claude" / "commands" / "cortex.md"
            self.assertTrue(cmd_file.is_file())

    def test_cli_dispatches_bootstrap_codex(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            output = io.StringIO()
            with patch("sys.stdout", output):
                cli_main(["bootstrap", "codex", "--root", td, "--force"])
            skill = Path(td) / ".codex" / "skills" / "cortex" / "SKILL.md"
            self.assertTrue(skill.is_file())

    def test_help_mentions_bootstrap(self) -> None:
        output = io.StringIO()
        with patch("sys.stdout", output):
            cli_main(["--help"])
        self.assertIn("bootstrap", output.getvalue())

if __name__ == "__main__":
    unittest.main()
