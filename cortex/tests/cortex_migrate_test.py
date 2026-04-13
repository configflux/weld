"""Tests for `cortex migrate` and the `kg` deprecation shim (ADR 0019)."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cortex import migrate as migrate_mod
from cortex.cli import main as cli_main
from cortex.compat import kg_shim_main
from cortex.migrate import MigrationReport, migrate

def _mcp_pre() -> dict:
    """A `.mcp.json` payload in the pre-migration `kg` shape."""
    return {
        "mcpServers": {
            "context7": {
                "command": "npx",
                "args": ["-y", "@upstash/context7-mcp@latest"],
            },
            "kg": {"command": "python", "args": ["-m", "kg.mcp_server"]},
        }
    }

def _mcp_post() -> dict:
    """A `.mcp.json` payload already in the post-migration `cortex` shape."""
    return {
        "mcpServers": {
            "context7": {
                "command": "npx",
                "args": ["-y", "@upstash/context7-mcp@latest"],
            },
            "cortex": {
                "command": "python",
                "args": ["-m", "cortex.mcp_server"],
            },
        }
    }

class MigrateDataDirTest(unittest.TestCase):
    """Rename `.kg/` -> `.cortex/` and report what happened."""

    def test_renames_kg_when_cortex_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".kg").mkdir()
            (root / ".kg" / "graph.json").write_text("{}", encoding="utf-8")

            report = migrate(root)

            self.assertFalse((root / ".kg").exists())
            self.assertTrue((root / ".cortex").is_dir())
            self.assertEqual(
                (root / ".cortex" / "graph.json").read_text(encoding="utf-8"),
                "{}",
            )
            self.assertIn(".kg/ -> .cortex/", " ".join(report.migrated))

    def test_skips_rename_when_cortex_already_exists(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".kg").mkdir()
            (root / ".kg" / "graph.json").write_text("{}", encoding="utf-8")
            (root / ".cortex").mkdir()
            (root / ".cortex" / "graph.json").write_text(
                "existing", encoding="utf-8"
            )

            report = migrate(root)

            # Existing .cortex/ preserved; .kg/ left alone so the adopter
            # can resolve the collision.
            self.assertEqual(
                (root / ".cortex" / "graph.json").read_text(encoding="utf-8"),
                "existing",
            )
            self.assertTrue((root / ".kg").is_dir())
            self.assertTrue(any(".kg" in n for n in report.skipped))

    def test_skips_rename_when_kg_absent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            report = migrate(root)
            self.assertFalse((root / ".kg").exists())
            self.assertFalse((root / ".cortex").exists())
            self.assertTrue(
                any(".kg" in n for n in report.skipped) or not report.migrated
            )

class MigrateMcpJsonTest(unittest.TestCase):
    """Patch `.mcp.json` from kg -> cortex."""

    def test_patches_server_key_and_module(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mcp = root / ".mcp.json"
            mcp.write_text(json.dumps(_mcp_pre(), indent=2), encoding="utf-8")

            report = migrate(root)

            data = json.loads(mcp.read_text(encoding="utf-8"))
            servers = data["mcpServers"]
            self.assertIn("cortex", servers)
            self.assertNotIn("kg", servers)
            self.assertEqual(
                servers["cortex"]["args"], ["-m", "cortex.mcp_server"]
            )
            self.assertIn("context7", servers)
            self.assertIn(".mcp.json", " ".join(report.migrated))

    def test_skips_when_already_migrated(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mcp = root / ".mcp.json"
            original = json.dumps(_mcp_post(), indent=2)
            mcp.write_text(original, encoding="utf-8")

            report = migrate(root)

            # Byte-identical when no change is needed.
            self.assertEqual(mcp.read_text(encoding="utf-8"), original)
            self.assertTrue(
                any(".mcp.json" in n for n in report.skipped),
                f"expected a skipped .mcp.json note, got: {report.skipped}",
            )

    def test_skips_when_absent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            migrate(root)  # must not raise
            self.assertFalse((root / ".mcp.json").exists())

    def test_no_kg_entry_is_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            mcp = root / ".mcp.json"
            payload = {
                "mcpServers": {
                    "context7": {
                        "command": "npx",
                        "args": ["-y", "@upstash/context7-mcp@latest"],
                    }
                }
            }
            mcp.write_text(json.dumps(payload, indent=2), encoding="utf-8")

            original = mcp.read_text(encoding="utf-8")
            migrate(root)
            self.assertEqual(mcp.read_text(encoding="utf-8"), original)

class MigrateGitignoreTest(unittest.TestCase):
    """Rewrite `.kg/*.tmp.*` entries in `.gitignore`."""

    def test_rewrites_kg_tmp_pattern(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            gi = root / ".gitignore"
            gi.write_text(
                "node_modules/\n.kg/*.tmp.*\n.venv/\n", encoding="utf-8"
            )

            report = migrate(root)

            new = gi.read_text(encoding="utf-8")
            self.assertIn(".cortex/*.tmp.*", new)
            self.assertNotIn(".kg/*.tmp.*", new)
            # Other lines must be preserved.
            self.assertIn("node_modules/", new)
            self.assertIn(".venv/", new)
            self.assertIn(".gitignore", " ".join(report.migrated))

    def test_skips_when_pattern_absent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            gi = root / ".gitignore"
            original = "node_modules/\n.cortex/*.tmp.*\n"
            gi.write_text(original, encoding="utf-8")
            migrate(root)
            self.assertEqual(gi.read_text(encoding="utf-8"), original)

    def test_skips_when_absent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            migrate(root)  # must not raise
            self.assertFalse((root / ".gitignore").exists())

class MigrateManualReportTest(unittest.TestCase):
    """Surface manual-rename items to the adopter."""

    def test_reports_claude_command_rename(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            legacy = root / ".claude" / "commands" / "kg.md"
            legacy.parent.mkdir(parents=True)
            legacy.write_text("legacy", encoding="utf-8")

            report = migrate(root)

            self.assertTrue(
                any(".claude/commands/kg.md" in x for x in report.manual),
                f"expected manual hint, got: {report.manual}",
            )
            # migrate must NOT touch .claude files itself.
            self.assertTrue(legacy.is_file())

    def test_reports_codex_skill_move(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            legacy = root / ".codex" / "skills" / "kg" / "SKILL.md"
            legacy.parent.mkdir(parents=True)
            legacy.write_text("legacy", encoding="utf-8")

            report = migrate(root)

            self.assertTrue(
                any(".codex/skills/kg" in x for x in report.manual),
                f"expected codex hint, got: {report.manual}",
            )

    def test_reports_settings_bash_kg_entries(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            settings = root / ".claude" / "settings.json"
            settings.parent.mkdir(parents=True)
            settings.write_text(
                json.dumps(
                    {"permissions": {"allow": ["Bash(kg *)", "Bash(ls)"]}}
                ),
                encoding="utf-8",
            )

            report = migrate(root)

            self.assertTrue(
                any("settings.json" in x for x in report.manual),
                f"expected settings.json hint, got: {report.manual}",
            )

class MigrateIdempotencyTest(unittest.TestCase):
    """Running migrate twice must not error and must be a no-op."""

    def test_double_run_is_safe(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".kg").mkdir()
            (root / ".kg" / "graph.json").write_text("{}", encoding="utf-8")
            mcp = root / ".mcp.json"
            mcp.write_text(json.dumps(_mcp_pre(), indent=2), encoding="utf-8")
            gi = root / ".gitignore"
            gi.write_text(".kg/*.tmp.*\n", encoding="utf-8")

            migrate(root)
            cortex_snap = (
                root / ".cortex" / "graph.json"
            ).read_text(encoding="utf-8")
            mcp_snap = mcp.read_text(encoding="utf-8")
            gi_snap = gi.read_text(encoding="utf-8")

            # Second run must not raise or change anything.
            second = migrate(root)

            self.assertEqual(
                (root / ".cortex" / "graph.json").read_text(encoding="utf-8"),
                cortex_snap,
            )
            self.assertEqual(mcp.read_text(encoding="utf-8"), mcp_snap)
            self.assertEqual(gi.read_text(encoding="utf-8"), gi_snap)
            self.assertEqual(second.migrated, [])

class MigrateReportShapeTest(unittest.TestCase):
    def test_report_has_expected_fields(self) -> None:
        report = MigrationReport()
        self.assertEqual(report.migrated, [])
        self.assertEqual(report.skipped, [])
        self.assertEqual(report.manual, [])

class MigrateCliDispatchTest(unittest.TestCase):
    """`cortex migrate` must be reachable from the top-level CLI."""

    def test_cli_dispatches_migrate(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".kg").mkdir()
            (root / ".kg" / "graph.json").write_text("{}", encoding="utf-8")

            out = io.StringIO()
            with patch("sys.stdout", out):
                rc = cli_main(["migrate", "--root", str(root)])

            self.assertEqual(rc, 0)
            self.assertTrue((root / ".cortex").is_dir())
            self.assertFalse((root / ".kg").exists())

    def test_help_mentions_migrate(self) -> None:
        out = io.StringIO()
        with patch("sys.stdout", out):
            cli_main(["--help"])
        self.assertIn("migrate", out.getvalue())

    def test_migrate_help(self) -> None:
        out = io.StringIO()
        with patch("sys.stdout", out):
            try:
                migrate_mod.main(["--help"])
            except SystemExit:
                pass
        self.assertIn("migrate", out.getvalue().lower())

class KgShimTest(unittest.TestCase):
    """The `kg` console script must print a deprecation warning and delegate."""

    def test_shim_prints_deprecation_warning(self) -> None:
        err, out = io.StringIO(), io.StringIO()
        with patch("sys.stderr", err), patch("sys.stdout", out):
            rc = kg_shim_main(["--help"])
        self.assertEqual(rc, 0)
        self.assertIn("kg has been renamed to cortex", err.getvalue())
        self.assertIn("cortex migrate", err.getvalue())
        # Delegated --help output goes to stdout.
        self.assertIn("Usage: cortex", out.getvalue())

    def test_shim_delegates_to_cortex_cli(self) -> None:
        captured: dict[str, list[str] | None] = {"argv": None}

        def fake_cli(argv: list[str] | None = None) -> int:
            captured["argv"] = list(argv) if argv is not None else []
            return 0

        err = io.StringIO()
        with patch("sys.stderr", err), patch(
            "cortex.compat.cortex_cli_main", fake_cli
        ):
            rc = kg_shim_main(["query", "foo"])

        self.assertEqual(rc, 0)
        self.assertEqual(captured["argv"], ["query", "foo"])

if __name__ == "__main__":
    unittest.main()
