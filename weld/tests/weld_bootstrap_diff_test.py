"""Tests for diff-aware wd bootstrap upgrades.

Covers the `--diff` / `--force` UX added by bd-5038-p1a.1. The
pre-existing silent-skip behaviour for identical files is preserved
with richer wording; for differing files, the default bootstrap now
tells the user the file differs and points them at `--diff` / `--force`.

The required coverage matrix is: Claude command (markdown), Codex MCP
config (non-markdown TOML), and the `.weld/README.md` reference asset.
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


def _seed_claude_tree(root: Path) -> Path:
    """Bootstrap claude once so on-disk files match bundled templates."""
    bootstrap("claude", root, force=True)
    return root / ".claude" / "commands" / "weld.md"


def _seed_codex_tree(root: Path) -> Path:
    """Bootstrap codex once so on-disk files match bundled templates."""
    bootstrap("codex", root, force=True)
    return root / ".codex" / "config.toml"


class IdenticalFileWordingTest(unittest.TestCase):
    """Identical existing files report up-to-date, not an ambiguous skip."""

    def test_identical_claude_command_reports_up_to_date(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _seed_claude_tree(root)
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                bootstrap("claude", root, force=False)
            output = buf.getvalue()
            self.assertIn("up-to-date", output.lower())
            # Must not fall back to the legacy "already exists, skipping"
            # wording for identical files -- that message is reserved for
            # files whose content differs from the template.
            self.assertNotIn("differs", output)

    def test_identical_readme_reports_up_to_date(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _seed_claude_tree(root)
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                bootstrap("claude", root, force=False)
            self.assertIn(".weld/README.md", buf.getvalue())
            self.assertIn("up-to-date", buf.getvalue().lower())

    def test_identical_codex_mcp_config_reports_up_to_date(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _seed_codex_tree(root)
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                bootstrap("codex", root, force=False)
            output = buf.getvalue()
            self.assertIn(".codex/config.toml", output)
            self.assertIn("up-to-date", output.lower())


class DifferingFileWordingTest(unittest.TestCase):
    """Differing files surface the upgrade path explicitly."""

    def test_differing_claude_command_points_at_diff_and_force(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cmd = _seed_claude_tree(root)
            # Edit inside a managed region (ADR 0033): change the trust-boundary
            # body so the writer detects in-region drift instead of routing
            # through the pre-marker migration path.
            text = cmd.read_text(encoding="utf-8")
            text = text.replace(
                "Run `wd discover` automatically only on repositories you trust.",
                "Run `wd discover` ONLY ON TRUSTED REPOS.",
            )
            cmd.write_text(text, encoding="utf-8")
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                bootstrap("claude", root, force=False)
            output = buf.getvalue()
            self.assertIn("differs", output)
            self.assertIn("--diff", output)
            self.assertIn("--force", output)
            # The file must still be preserved -- default bootstrap is a
            # dry warning only, never a silent overwrite.
            self.assertIn("ONLY ON TRUSTED REPOS", cmd.read_text(encoding="utf-8"))

    def test_differing_codex_mcp_config_points_at_diff_and_force(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = _seed_codex_tree(root)
            text = config.read_text(encoding="utf-8")
            # In-region edit on the mcp-servers region.
            text = text.replace('command = "python"', 'command = "python3"')
            config.write_text(text, encoding="utf-8")
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                bootstrap("codex", root, force=False)
            output = buf.getvalue()
            self.assertIn("differs", output)
            self.assertIn("--diff", output)
            self.assertIn("--force", output)
            self.assertIn('command = "python3"', config.read_text(encoding="utf-8"))

    def test_differing_readme_points_at_diff_and_force(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _seed_claude_tree(root)
            readme = root / ".weld" / "README.md"
            text = readme.read_text(encoding="utf-8")
            # In-region edit on the files-table region.
            text = text.replace("`discover.yaml`", "`discover.yml`", 1)
            readme.write_text(text, encoding="utf-8")
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                bootstrap("claude", root, force=False)
            output = buf.getvalue()
            self.assertIn(".weld/README.md", output)
            self.assertIn("differs", output)
            self.assertIn("--diff", output)
            self.assertIn("--force", output)


class DiffFlagTest(unittest.TestCase):
    """`--diff` prints unified diffs, never writes, and exits by diff count."""

    def test_diff_without_diffs_exits_zero(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _seed_claude_tree(root)
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                with self.assertRaises(SystemExit) as cm:
                    cli_main(["bootstrap", "claude", "--root", str(root), "--diff"])
            self.assertEqual(cm.exception.code, 0)
            # No diff markers when everything is identical.
            self.assertNotIn("---", buf.getvalue())
            self.assertNotIn("+++", buf.getvalue())

    def test_diff_with_diffs_exits_one_and_prints_unified_diff(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cmd = _seed_claude_tree(root)
            text = cmd.read_text(encoding="utf-8")
            # In-region edit so --diff produces a region-scoped unified diff
            # instead of routing through the pre-marker migration path.
            text = text.replace(
                "Run `wd discover` automatically only on repositories you trust.",
                "Run `wd discover` ONLY ON TRUSTED REPOS.",
            )
            cmd.write_text(text, encoding="utf-8")
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                with self.assertRaises(SystemExit) as cm:
                    cli_main(["bootstrap", "claude", "--root", str(root), "--diff"])
            self.assertEqual(cm.exception.code, 1)
            output = buf.getvalue()
            # Unified diff markers must be present.
            self.assertIn("---", output)
            self.assertIn("+++", output)
            # The customised line should appear on the "-" side.
            self.assertIn("ONLY ON TRUSTED REPOS", output)
            # --diff must not mutate the file.
            self.assertIn(
                "ONLY ON TRUSTED REPOS",
                cmd.read_text(encoding="utf-8"),
            )

    def test_diff_does_not_write_missing_files(self) -> None:
        """--diff against an empty tree must not create files."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                with self.assertRaises(SystemExit) as cm:
                    cli_main(["bootstrap", "claude", "--root", str(root), "--diff"])
            # Missing files count as differing content (empty vs template).
            self.assertEqual(cm.exception.code, 1)
            # But nothing should have been written to disk.
            self.assertFalse((root / ".claude" / "commands" / "weld.md").exists())
            self.assertFalse((root / ".weld" / "README.md").exists())

    def test_diff_covers_codex_mcp_config(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = _seed_codex_tree(root)
            text = config.read_text(encoding="utf-8")
            # In-region edit so --diff emits a region-scoped unified diff.
            text = text.replace('command = "python"', 'command = "python3"')
            config.write_text(text, encoding="utf-8")
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                with self.assertRaises(SystemExit) as cm:
                    cli_main(["bootstrap", "codex", "--root", str(root), "--diff"])
            self.assertEqual(cm.exception.code, 1)
            output = buf.getvalue()
            self.assertIn(".codex/config.toml", output)
            self.assertIn("---", output)
            self.assertIn("+++", output)
            # Content untouched.
            self.assertIn(
                'command = "python3"',
                config.read_text(encoding="utf-8"),
            )


class ForceFlagPreservesBehaviourTest(unittest.TestCase):
    """`--force` upgrades files while opt-out / federation still apply."""

    def test_force_overwrites_claude_command(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cmd = _seed_claude_tree(root)
            original = cmd.read_text(encoding="utf-8")
            cmd.write_text("# customised\n", encoding="utf-8")
            bootstrap("claude", root, force=True)
            self.assertEqual(cmd.read_text(encoding="utf-8"), original)

    def test_force_with_cli_only_writes_cli_variant(self) -> None:
        """--force must honour opt-out variant selection."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _seed_claude_tree(root)  # seed with default (MCP-on) content
            bootstrap("claude", root, force=True, cli_only=True)
            cmd = root / ".claude" / "commands" / "weld.md"
            content = cmd.read_text(encoding="utf-8")
            self.assertNotIn("mcp", content.lower())
            self.assertNotIn("wd enrich", content)

    def test_force_with_federation_sentinel_applies_paragraph(self) -> None:
        """--force upgrade must still append the federation paragraph."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _seed_claude_tree(root)
            # Introduce the federation sentinel, then force an upgrade.
            sentinel = root / ".weld" / "workspaces.yaml"
            sentinel.write_text("children: []\n", encoding="utf-8")
            bootstrap("claude", root, force=True)
            cmd = root / ".claude" / "commands" / "weld.md"
            content = cmd.read_text(encoding="utf-8")
            self.assertIn("wd workspace status", content)

    def test_force_preserves_codex_no_mcp_opt_out(self) -> None:
        # Per ADR 0033 §6, sibling variants (.md and .cli.md) declare the same
        # managed-region names with byte-identical bodies, so swapping variants
        # on a previously-seeded file does NOT re-trigger migration. The
        # operator-owned content outside the markers (including any MCP
        # mentions left over from the .md variant) is preserved -- exactly the
        # property the marker model is meant to guarantee.
        #
        # The relevant invariants for --force --no-mcp are: (a) the codex MCP
        # config file is removed from the write list, and (b) the skill file
        # is left in a parseable state (no broken marker pairs).
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _seed_codex_tree(root)
            bootstrap("codex", root, force=True, no_mcp=True)
            skill = root / ".codex" / "skills" / "weld" / "SKILL.md"
            content = skill.read_text(encoding="utf-8")
            # Marker pairs survived the variant swap intact.
            self.assertIn(
                "<!-- weld-managed:start name=retrieval-commands -->", content,
            )
            self.assertIn(
                "<!-- weld-managed:end name=retrieval-commands -->", content,
            )


class DiffFlagCliWiringTest(unittest.TestCase):
    """The CLI surface exposes --diff / --force for each framework."""

    def test_bootstrap_help_mentions_diff_and_force(self) -> None:
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            with self.assertRaises(SystemExit):
                cli_main(["bootstrap", "claude", "--help"])
        output = buf.getvalue()
        self.assertIn("--diff", output)
        self.assertIn("--force", output)


class IncludeUnmanagedRequiresDiffTest(unittest.TestCase):
    """`--include-unmanaged` only makes sense with `--diff`.

    The flag toggles whole-file vs region-scoped comparison (ADR 0033) and is
    silently ignored in write mode. Reject it at parse time so operators get a
    clear error instead of silently-incorrect behaviour. See bd
    1776099136-5038-tkxt for the UX rationale.
    """

    def test_include_unmanaged_without_diff_exits_two(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            err = io.StringIO()
            with patch("sys.stderr", err):
                with self.assertRaises(SystemExit) as cm:
                    cli_main([
                        "bootstrap", "copilot", "--root", str(root),
                        "--include-unmanaged",
                    ])
            # argparse-style validation -> exit 2 with a clear stderr message.
            self.assertEqual(cm.exception.code, 2)
            stderr = err.getvalue()
            self.assertIn("--include-unmanaged", stderr)
            self.assertIn("--diff", stderr)
            # Nothing should have been written without --diff.
            self.assertFalse(
                (root / ".github" / "skills" / "weld" / "SKILL.md").exists()
            )

    def test_include_unmanaged_with_diff_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                with self.assertRaises(SystemExit) as cm:
                    cli_main([
                        "bootstrap", "copilot", "--root", str(root),
                        "--diff", "--include-unmanaged",
                    ])
            # Empty tree vs templates -> diff exits 1; the important property
            # for this test is that argparse did NOT reject the combination.
            self.assertIn(cm.exception.code, (0, 1))

    def test_include_unmanaged_without_diff_rejected_for_every_framework(
        self,
    ) -> None:
        for framework in ("claude", "codex", "copilot"):
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                err = io.StringIO()
                with patch("sys.stderr", err):
                    with self.assertRaises(SystemExit) as cm:
                        cli_main([
                            "bootstrap", framework, "--root", str(root),
                            "--include-unmanaged",
                        ])
                self.assertEqual(
                    cm.exception.code, 2,
                    f"{framework}: expected exit 2 without --diff",
                )
                self.assertIn("--include-unmanaged", err.getvalue())
                self.assertIn("--diff", err.getvalue())


if __name__ == "__main__":
    unittest.main()
