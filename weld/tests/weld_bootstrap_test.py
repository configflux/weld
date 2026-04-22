"""Tests for the wd bootstrap command."""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from weld.bootstrap import bootstrap
from weld.cli import main as cli_main

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


def _assert_manual_enrichment_guidance(test: unittest.TestCase, content: str) -> None:
    test.assertIn("wd add-node", content)
    test.assertIn('"provider": "manual"', content)
    test.assertIn('"model": "agent-reviewed"', content)


class BootstrapClaudeTest(unittest.TestCase):
    """Verify bootstrap claude writes the expected files."""

    def test_creates_weld_readme(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("claude", root, force=True)
            readme = root / ".weld" / "README.md"
            self.assertTrue(readme.is_file())
            self.assertGreater(len(readme.read_text(encoding="utf-8").strip()), 50)

    def test_bootstrapped_readme_has_no_placeholder_tokens(self) -> None:
        """Regression: the bootstrapped .weld/README.md must not contain
        placeholder organization names or token markers. The template is
        copied verbatim (no substitution), so any placeholder in the template
        leaks directly to end users.
        """
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("claude", root, force=True)
            readme = root / ".weld" / "README.md"
            content = readme.read_text(encoding="utf-8")
            self.assertNotIn("your-org", content)
            self.assertNotIn("<placeholder>", content)

    def test_creates_claude_command(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("claude", root, force=True)
            cmd_file = root / ".claude" / "commands" / "weld.md"
            self.assertTrue(cmd_file.is_file())
            content = cmd_file.read_text(encoding="utf-8")
            self.assertIn("wd brief", content)
            _assert_manual_enrichment_guidance(self, content)

class BootstrapCodexTest(unittest.TestCase):
    """Verify bootstrap codex writes the expected files."""

    def test_creates_weld_readme(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("codex", root, force=True)
            readme = root / ".weld" / "README.md"
            self.assertTrue(readme.is_file())

    def test_creates_codex_skill(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("codex", root, force=True)
            skill = root / ".codex" / "skills" / "weld" / "SKILL.md"
            self.assertTrue(skill.is_file())
            content = skill.read_text(encoding="utf-8")
            self.assertIn("wd brief", content)
            _assert_manual_enrichment_guidance(self, content)

    def test_creates_codex_mcp_config(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("codex", root, force=True)
            config = root / ".codex" / "config.toml"
            self.assertTrue(config.is_file())
            content = config.read_text(encoding="utf-8")
            self.assertIn("[mcp_servers.weld]", content)
            self.assertIn("[mcp_servers.context7]", content)

class BootstrapOverwriteTest(unittest.TestCase):
    """Verify overwrite/skip behavior."""

    def test_does_not_overwrite_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            readme = root / ".weld" / "README.md"
            readme.parent.mkdir(parents=True)
            readme.write_text("sentinel", encoding="utf-8")
            output = io.StringIO()
            with patch("sys.stdout", output):
                bootstrap("claude", root, force=False)
            self.assertEqual(readme.read_text(encoding="utf-8"), "sentinel")
            # File differs from the bundled template, so bootstrap must now
            # surface the --diff / --force upgrade path instead of a silent
            # "already exists" message.
            self.assertIn("differs", output.getvalue())
            self.assertIn("--diff", output.getvalue())
            self.assertIn("--force", output.getvalue())

    def test_force_overwrites(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            readme = root / ".weld" / "README.md"
            readme.parent.mkdir(parents=True)
            readme.write_text("sentinel", encoding="utf-8")
            bootstrap("claude", root, force=True)
            self.assertNotEqual(readme.read_text(encoding="utf-8"), "sentinel")

class BootstrapDiscoverYamlTest(unittest.TestCase):
    """Verify discover.yaml delegation."""

    def test_skips_existing_discover_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            discover = root / ".weld" / "discover.yaml"
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

class BootstrapCopilotTest(unittest.TestCase):
    """Verify bootstrap copilot writes the expected files."""

    def test_creates_weld_readme(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("copilot", root, force=True)
            readme = root / ".weld" / "README.md"
            self.assertTrue(readme.is_file())

    def test_creates_copilot_skill(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("copilot", root, force=True)
            skill = root / ".github" / "skills" / "weld" / "SKILL.md"
            self.assertTrue(skill.is_file())
            content = skill.read_text(encoding="utf-8")
            self.assertIn("wd brief", content)
            _assert_manual_enrichment_guidance(self, content)

    def test_copilot_skill_has_yaml_frontmatter(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("copilot", root, force=True)
            skill = root / ".github" / "skills" / "weld" / "SKILL.md"
            content = skill.read_text(encoding="utf-8")
            self.assertTrue(content.startswith("---\n"))
            self.assertIn("name: weld", content)
            self.assertIn("allowed-tools:", content)
            self.assertIn("shell", content)

    def test_copilot_skill_has_description(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("copilot", root, force=True)
            skill = root / ".github" / "skills" / "weld" / "SKILL.md"
            content = skill.read_text(encoding="utf-8")
            self.assertIn("description:", content)

    def test_copilot_skill_description_has_trigger_phrases(self) -> None:
        """Skill matching keys on description text -- trigger phrases must be present."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("copilot", root, force=True)
            skill = root / ".github" / "skills" / "weld" / "SKILL.md"
            content = skill.read_text(encoding="utf-8")
            # Isolate the frontmatter description block. Frontmatter spans
            # from the leading '---\n' to the next '\n---\n'.
            self.assertTrue(content.startswith("---\n"))
            end = content.find("\n---\n", 4)
            self.assertGreater(end, 0, "frontmatter not terminated")
            frontmatter = content[4:end]
            self.assertIn("description:", frontmatter)
            for phrase in (
                "weld",
                "wd",
                "workspace graph",
                "discovery wave",
                "repo map",
                "query graph",
                "federation",
                "polyrepo",
            ):
                self.assertIn(
                    phrase,
                    frontmatter,
                    f"trigger phrase {phrase!r} missing from skill frontmatter",
                )

    def test_creates_copilot_instructions(self) -> None:
        """bootstrap copilot must also emit the always-on instruction file."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("copilot", root, force=True)
            instructions = (
                root / ".github" / "instructions" / "weld.instructions.md"
            )
            self.assertTrue(
                instructions.is_file(),
                f"missing instruction file: {instructions}",
            )

    def test_copilot_instructions_apply_to_all(self) -> None:
        """Instruction file must carry applyTo: '**' frontmatter."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("copilot", root, force=True)
            instructions = (
                root / ".github" / "instructions" / "weld.instructions.md"
            )
            content = instructions.read_text(encoding="utf-8")
            self.assertTrue(content.startswith("---\n"))
            end = content.find("\n---\n", 4)
            self.assertGreater(end, 0, "frontmatter not terminated")
            frontmatter = content[4:end]
            self.assertIn("applyTo:", frontmatter)
            # Accept either quoted form per YAML conventions.
            self.assertTrue(
                'applyTo: "**"' in frontmatter or "applyTo: '**'" in frontmatter,
                f"applyTo not set to **: {frontmatter!r}",
            )

    def test_copilot_instructions_stay_generic(self) -> None:
        """Instruction content must not couple to specific repos."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("copilot", root, force=True)
            instructions = (
                root / ".github" / "instructions" / "weld.instructions.md"
            )
            content = instructions.read_text(encoding="utf-8")
            # Generic discovery primer hits -- presence of `wd` is required.
            self.assertIn("wd ", content)
            # No hardcoded repo names from this monorepo. Build the forbidden
            # tokens from split fragments so this test itself does not embed
            # the legacy package token that the rebrand-trace guard flags.
            forbidden_tokens = (
                "".join(("cor", "tex")) + "-internal",
                "".join(("tilbuds", "radar")),
            )
            for token in forbidden_tokens:
                self.assertNotIn(token, content)

    def test_copilot_instructions_mention_workspaces_sentinel(self) -> None:
        """Instruction file should mention the workspaces.yaml sentinel."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("copilot", root, force=True)
            instructions = (
                root / ".github" / "instructions" / "weld.instructions.md"
            )
            content = instructions.read_text(encoding="utf-8")
            self.assertIn("workspaces.yaml", content)


class BootstrapTemplatesLoadableTest(unittest.TestCase):
    """Verify all markdown templates exist in the package."""

    def test_weld_readme_template_exists(self) -> None:
        self.assertTrue((_TEMPLATES_DIR / "weld_readme.md").is_file())

    def test_weld_cmd_claude_template_exists(self) -> None:
        self.assertTrue((_TEMPLATES_DIR / "weld_cmd_claude.md").is_file())

    def test_weld_skill_codex_template_exists(self) -> None:
        self.assertTrue((_TEMPLATES_DIR / "weld_skill_codex.md").is_file())

    def test_codex_mcp_config_template_exists(self) -> None:
        self.assertTrue((_TEMPLATES_DIR / "codex_mcp_config.toml").is_file())

    def test_weld_skill_copilot_template_exists(self) -> None:
        self.assertTrue((_TEMPLATES_DIR / "weld_skill_copilot.md").is_file())

    def test_weld_instructions_copilot_template_exists(self) -> None:
        self.assertTrue(
            (_TEMPLATES_DIR / "weld_instructions_copilot.md").is_file()
        )


class BootstrapCliDispatchTest(unittest.TestCase):
    """Verify bootstrap dispatch from the top-level CLI."""

    def test_cli_dispatches_bootstrap_claude(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            output = io.StringIO()
            with patch("sys.stdout", output):
                cli_main(["bootstrap", "claude", "--root", td, "--force"])
            cmd_file = Path(td) / ".claude" / "commands" / "weld.md"
            self.assertTrue(cmd_file.is_file())

    def test_cli_dispatches_bootstrap_codex(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            output = io.StringIO()
            with patch("sys.stdout", output):
                cli_main(["bootstrap", "codex", "--root", td, "--force"])
            skill = Path(td) / ".codex" / "skills" / "weld" / "SKILL.md"
            config = Path(td) / ".codex" / "config.toml"
            self.assertTrue(skill.is_file())
            self.assertTrue(config.is_file())

    def test_cli_dispatches_bootstrap_copilot(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            output = io.StringIO()
            with patch("sys.stdout", output):
                cli_main(["bootstrap", "copilot", "--root", td, "--force"])
            skill = Path(td) / ".github" / "skills" / "weld" / "SKILL.md"
            self.assertTrue(skill.is_file())

    def test_help_mentions_bootstrap(self) -> None:
        output = io.StringIO()
        with patch("sys.stdout", output):
            cli_main(["--help"])
        self.assertIn("bootstrap", output.getvalue())
        for framework in ("claude", "codex", "copilot"):
            self.assertIn(framework, output.getvalue())

    def test_bootstrap_help_mentions_all_frameworks(self) -> None:
        output = io.StringIO()
        with patch("sys.stdout", output):
            with self.assertRaises(SystemExit) as cm:
                cli_main(["bootstrap", "--help"])
        self.assertEqual(cm.exception.code, 0)
        for framework in ("claude", "codex", "copilot"):
            self.assertIn(framework, output.getvalue())

if __name__ == "__main__":
    unittest.main()
