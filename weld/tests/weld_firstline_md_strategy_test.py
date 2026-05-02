"""Tests for the firstline_md discovery strategy.

The strategy walks markdown files matching ``glob`` and emits one
``command:<stem>`` node per file. The first non-empty line of the file's
content (after any optional YAML frontmatter is stripped) becomes the
node's ``description`` property.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld.strategies._helpers import StrategyResult
from weld.strategies.firstline_md import extract


_HAPPY_BODY = """\
Run the local task gate before pushing.

Detailed steps follow in the rest of the file.
"""

_FRONTMATTER_BODY = """\
---
title: ignored
status: ignored
---

Description from the body lives here.

More detail.
"""


class TestFirstlineMdEmptyAndMissing(unittest.TestCase):
    """Empty and missing inputs must produce a well-formed empty result."""

    def test_missing_parent_directory_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = {"glob": "commands/*.md"}
            result = extract(root, source, {})
            self.assertIsInstance(result, StrategyResult)
            self.assertEqual(result.nodes, {})
            self.assertEqual(result.edges, [])
            self.assertEqual(result.discovered_from, [])

    def test_empty_directory_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "commands").mkdir()
            source = {"glob": "commands/*.md"}
            result = extract(root, source, {})
            self.assertEqual(result.nodes, {})
            # Parent existed, so it is recorded as discovered_from.
            self.assertEqual(result.discovered_from, ["commands/"])

    def test_empty_file_yields_node_with_blank_description(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cmd_dir = root / "commands"
            cmd_dir.mkdir()
            (cmd_dir / "noop.md").write_text("", encoding="utf-8")
            result = extract(root, {"glob": "commands/*.md"}, {})
            self.assertIn("command:noop", result.nodes)
            self.assertEqual(
                result.nodes["command:noop"]["props"]["description"], ""
            )


class TestFirstlineMdHappyPath(unittest.TestCase):
    """Canonical extraction: stem-based id, first non-empty line description."""

    def test_extracts_command_node_with_first_line_description(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cmd_dir = root / "commands"
            cmd_dir.mkdir()
            (cmd_dir / "push.md").write_text(_HAPPY_BODY, encoding="utf-8")
            result = extract(root, {"glob": "commands/*.md"}, {})
            self.assertIn("command:push", result.nodes)
            node = result.nodes["command:push"]
            self.assertEqual(node["type"], "command")
            self.assertEqual(node["label"], "push")
            props = node["props"]
            self.assertEqual(props["file"], "commands/push.md")
            self.assertEqual(
                props["description"],
                "Run the local task gate before pushing.",
            )
            self.assertEqual(props["source_strategy"], "firstline_md")
            self.assertEqual(props["authority"], "canonical")
            self.assertEqual(props["confidence"], "definite")
            self.assertEqual(props["roles"], ["config"])

    def test_context_records_full_command_text(self) -> None:
        # The strategy stashes the file's full text in the shared context
        # so the orchestrator can post-process invocation references.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cmd_dir = root / "commands"
            cmd_dir.mkdir()
            (cmd_dir / "push.md").write_text(_HAPPY_BODY, encoding="utf-8")
            shared_ctx: dict = {}
            extract(root, {"glob": "commands/*.md"}, shared_ctx)
            self.assertIn("command_texts", shared_ctx)
            self.assertIn("command:push", shared_ctx["command_texts"])
            self.assertIn(
                "Run the local task gate", shared_ctx["command_texts"]["command:push"]
            )


class TestFirstlineMdFrontmatterAndExcludes(unittest.TestCase):
    """Frontmatter must be skipped; exclude patterns must drop matching files."""

    def test_skips_frontmatter_and_uses_body_first_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cmd_dir = root / "commands"
            cmd_dir.mkdir()
            (cmd_dir / "doc.md").write_text(_FRONTMATTER_BODY, encoding="utf-8")
            result = extract(root, {"glob": "commands/*.md"}, {})
            self.assertEqual(
                result.nodes["command:doc"]["props"]["description"],
                "Description from the body lives here.",
            )

    def test_exclude_pattern_drops_matching_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cmd_dir = root / "commands"
            cmd_dir.mkdir()
            (cmd_dir / "keep.md").write_text("Hello\n", encoding="utf-8")
            (cmd_dir / "drop.md").write_text("Goodbye\n", encoding="utf-8")
            source = {"glob": "commands/*.md", "exclude": ["drop.md"]}
            result = extract(root, source, {})
            self.assertIn("command:keep", result.nodes)
            self.assertNotIn("command:drop", result.nodes)


if __name__ == "__main__":
    unittest.main()
