"""Tests for the explicit pre-marker-layout migration in `wd bootstrap`.

Per ADR 0033 §3, the writer never tries to wrap pre-existing operator content
in markers heuristically. When the destination file exists but contains no
``weld-managed:start`` line anywhere, the writer prints an actionable message
and exits non-zero; ``--force`` re-seeds the file with the bundled template
verbatim (markers and all). This module exercises that path end-to-end.
"""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from weld.bootstrap import bootstrap
from weld.cli import main as cli_main


def _seed_pre_marker_skill(root: Path) -> Path:
    """Write a copilot SKILL.md whose body has no managed-region markers."""
    skill = root / ".github" / "skills" / "weld" / "SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text(
        "---\n"
        "name: weld\n"
        "description: legacy pre-marker bootstrap output\n"
        "---\n"
        "\n"
        "# Weld\n"
        "\n"
        "## Retrieval commands\n"
        "\n"
        "Some hand-edited body text that pre-dates managed regions.\n",
        encoding="utf-8",
    )
    # Also seed the README so the migration message is the only blocker.
    readme = root / ".weld" / "README.md"
    readme.parent.mkdir(parents=True, exist_ok=True)
    bootstrap("copilot", root, force=True)
    # Re-overwrite the skill to the pre-marker layout (force just rewrote it).
    skill.write_text(
        "---\n"
        "name: weld\n"
        "description: legacy pre-marker bootstrap output\n"
        "---\n"
        "\n"
        "# Weld\n"
        "\n"
        "## Retrieval commands\n"
        "\n"
        "Some hand-edited body text that pre-dates managed regions.\n",
        encoding="utf-8",
    )
    return skill


class PreMarkerWriteTest(unittest.TestCase):
    """Default ``bootstrap <fw>`` on a pre-marker file refuses to clobber."""

    def test_pre_marker_file_is_left_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            skill = _seed_pre_marker_skill(root)
            before = skill.read_text(encoding="utf-8")
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                bootstrap("copilot", root)
            self.assertEqual(skill.read_text(encoding="utf-8"), before)
            output = buf.getvalue()
            self.assertIn("pre-marker layout", output)
            self.assertIn("--force", output)
            # Message must point at the ADR docs slug for self-service.
            self.assertIn("0033", output)


class PreMarkerForceTest(unittest.TestCase):
    """``--force`` re-seeds the bundled template verbatim, markers and all."""

    def test_force_reseeds_with_markers(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            skill = _seed_pre_marker_skill(root)
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                bootstrap("copilot", root, force=True)
            content = skill.read_text(encoding="utf-8")
            self.assertIn(
                "<!-- weld-managed:start name=retrieval-commands -->",
                content,
            )
            self.assertIn(
                "<!-- weld-managed:end name=retrieval-commands -->",
                content,
            )
            # The hand-edited body line must be gone after re-seed.
            self.assertNotIn("Some hand-edited body text", content)

    def test_second_run_after_force_is_no_op(self) -> None:
        """Migration is idempotent: a second run after ``--force`` is a no-op."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _seed_pre_marker_skill(root)
            bootstrap("copilot", root, force=True)
            skill = root / ".github" / "skills" / "weld" / "SKILL.md"
            after_force = skill.read_text(encoding="utf-8")
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                bootstrap("copilot", root)
            self.assertEqual(skill.read_text(encoding="utf-8"), after_force)
            self.assertIn("up-to-date", buf.getvalue().lower())


class PreMarkerDiffTest(unittest.TestCase):
    """``--diff`` against an unmarked file exits 1 with the same message."""

    def test_diff_against_pre_marker_exits_one(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _seed_pre_marker_skill(root)
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                with self.assertRaises(SystemExit) as cm:
                    cli_main([
                        "bootstrap", "copilot",
                        "--root", str(root), "--diff",
                    ])
            self.assertEqual(cm.exception.code, 1)
            self.assertIn("pre-marker layout", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
