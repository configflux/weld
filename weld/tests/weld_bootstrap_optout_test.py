"""Tests for --no-mcp / --no-enrich / --cli-only bootstrap opt-out flags.

Split from weld_bootstrap_test.py to keep both files under the 400-line cap.
The opt-out flags form a cohesive surface of their own (one flag set, one
``.cli.md`` variant family), so colocating them here is natural.
"""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from weld.bootstrap import bootstrap
from weld.cli import main as cli_main

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


def _has_mcp_mention(content: str) -> bool:
    return "mcp" in content.lower()


def _has_enrich_mention(content: str) -> bool:
    # Match ``wd enrich`` or manual enrichment guidance (the add-node block).
    return "wd enrich" in content or "wd add-node" in content


class BootstrapCodexOptOutTest(unittest.TestCase):
    """Codex is the primary --no-mcp beneficiary; it drops config.toml."""

    def test_no_mcp_drops_config_file(self) -> None:
        """--no-mcp on codex must skip .codex/config.toml entirely."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("codex", root, force=True, no_mcp=True)
            skill = root / ".codex" / "skills" / "weld" / "SKILL.md"
            config = root / ".codex" / "config.toml"
            self.assertTrue(skill.is_file(), "skill must still be written")
            self.assertFalse(
                config.is_file(),
                "codex config.toml must not be written with --no-mcp",
            )

    def test_cli_only_output_has_no_mcp_or_enrich(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("codex", root, force=True, cli_only=True)
            skill = root / ".codex" / "skills" / "weld" / "SKILL.md"
            self.assertTrue(skill.is_file())
            content = skill.read_text(encoding="utf-8")
            self.assertFalse(
                _has_mcp_mention(content),
                f"codex --cli-only skill must not mention MCP: {content!r}",
            )
            self.assertFalse(
                _has_enrich_mention(content),
                f"codex --cli-only skill must not mention enrich: {content!r}",
            )
            self.assertFalse(
                (root / ".codex" / "config.toml").is_file(),
                "codex --cli-only must not write config.toml",
            )

    def test_no_enrich_strips_enrich_from_skill(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("codex", root, force=True, no_enrich=True)
            skill = root / ".codex" / "skills" / "weld" / "SKILL.md"
            content = skill.read_text(encoding="utf-8")
            self.assertFalse(
                _has_enrich_mention(content),
                f"codex --no-enrich skill must not mention enrich: {content!r}",
            )


class BootstrapCopilotOptOutTest(unittest.TestCase):
    """Copilot mentions MCP in markdown; opt-out swaps to .cli.md variants."""

    def test_cli_only_output_has_no_mcp_or_enrich(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("copilot", root, force=True, cli_only=True)
            skill = root / ".github" / "skills" / "weld" / "SKILL.md"
            instructions = (
                root / ".github" / "instructions" / "weld.instructions.md"
            )
            self.assertTrue(skill.is_file())
            self.assertTrue(instructions.is_file())
            for path in (skill, instructions):
                content = path.read_text(encoding="utf-8")
                self.assertFalse(
                    _has_mcp_mention(content),
                    f"copilot --cli-only {path.name} must not mention MCP",
                )
                self.assertFalse(
                    _has_enrich_mention(content),
                    f"copilot --cli-only {path.name} must not mention enrich",
                )

    def test_no_mcp_strips_mcp_from_skill_and_instructions(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("copilot", root, force=True, no_mcp=True)
            skill = root / ".github" / "skills" / "weld" / "SKILL.md"
            instructions = (
                root / ".github" / "instructions" / "weld.instructions.md"
            )
            for path in (skill, instructions):
                content = path.read_text(encoding="utf-8")
                self.assertFalse(
                    _has_mcp_mention(content),
                    f"copilot --no-mcp {path.name} must not mention MCP",
                )


class BootstrapClaudeOptOutTest(unittest.TestCase):
    """Claude's command template mentions MCP inline; opt-out swaps variants."""

    def test_cli_only_output_has_no_mcp_or_enrich(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("claude", root, force=True, cli_only=True)
            cmd = root / ".claude" / "commands" / "weld.md"
            self.assertTrue(cmd.is_file())
            content = cmd.read_text(encoding="utf-8")
            self.assertFalse(
                _has_mcp_mention(content),
                f"claude --cli-only command must not mention MCP: {content!r}",
            )
            self.assertFalse(
                _has_enrich_mention(content),
                f"claude --cli-only command must not mention enrich: {content!r}",
            )

    def test_no_mcp_strips_mcp_from_command(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("claude", root, force=True, no_mcp=True)
            cmd = root / ".claude" / "commands" / "weld.md"
            content = cmd.read_text(encoding="utf-8")
            self.assertFalse(
                _has_mcp_mention(content),
                f"claude --no-mcp command must not mention MCP: {content!r}",
            )


class BootstrapOptOutCliDispatchTest(unittest.TestCase):
    """Verify argparse wires the opt-out flags through the top-level CLI."""

    def test_cli_only_flag_dispatches(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            output = io.StringIO()
            with patch("sys.stdout", output):
                cli_main([
                    "bootstrap", "codex", "--root", td, "--force", "--cli-only",
                ])
            self.assertFalse(
                (Path(td) / ".codex" / "config.toml").is_file(),
                "--cli-only via CLI must skip codex config.toml",
            )

    def test_no_mcp_flag_dispatches(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            output = io.StringIO()
            with patch("sys.stdout", output):
                cli_main([
                    "bootstrap", "codex", "--root", td, "--force", "--no-mcp",
                ])
            self.assertFalse(
                (Path(td) / ".codex" / "config.toml").is_file(),
                "--no-mcp via CLI must skip codex config.toml",
            )

    def test_no_enrich_flag_dispatches(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            output = io.StringIO()
            with patch("sys.stdout", output):
                cli_main([
                    "bootstrap", "claude", "--root", td,
                    "--force", "--no-enrich",
                ])
            cmd = Path(td) / ".claude" / "commands" / "weld.md"
            content = cmd.read_text(encoding="utf-8")
            self.assertNotIn(
                "wd enrich", content,
                "--no-enrich via CLI must strip enrich from claude command",
            )


class BootstrapCliTemplateFilesTest(unittest.TestCase):
    """Every user-facing markdown template must ship a .cli.md sibling."""

    def test_copilot_skill_cli_variant_exists(self) -> None:
        self.assertTrue(
            (_TEMPLATES_DIR / "weld_skill_copilot.cli.md").is_file()
        )

    def test_copilot_instructions_cli_variant_exists(self) -> None:
        self.assertTrue(
            (_TEMPLATES_DIR / "weld_instructions_copilot.cli.md").is_file()
        )

    def test_codex_skill_cli_variant_exists(self) -> None:
        self.assertTrue(
            (_TEMPLATES_DIR / "weld_skill_codex.cli.md").is_file()
        )

    def test_claude_cmd_cli_variant_exists(self) -> None:
        self.assertTrue(
            (_TEMPLATES_DIR / "weld_cmd_claude.cli.md").is_file()
        )


if __name__ == "__main__":
    unittest.main()
