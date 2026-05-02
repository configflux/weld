"""Tests for the frontmatter_md discovery strategy.

The strategy walks markdown files and parses a leading YAML frontmatter
block (``---``-fenced) to extract ``name``, ``description``, and
``model``. Each file becomes one ``agent:<name>`` node, with the file
stem as the fallback name when the frontmatter is missing or malformed.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld.strategies._helpers import StrategyResult
from weld.strategies.frontmatter_md import extract


_HAPPY_AGENT = """\
---
name: scout
description: Reads source and surfaces facts.
model: sonnet
---

# Scout agent

Body content does not affect node properties.
"""

_NO_FRONTMATTER = """\
# Plain markdown

This file has no frontmatter at all.
"""

_UNCLOSED_FRONTMATTER = """\
---
name: incomplete
description: never gets a closing fence

Body merges with the (broken) frontmatter.
"""


class TestFrontmatterMdEmptyAndMissing(unittest.TestCase):
    """Missing parent dir and empty dirs must yield well-formed empty results."""

    def test_missing_parent_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = extract(root, {"glob": "agents/*.md"}, {})
            self.assertIsInstance(result, StrategyResult)
            self.assertEqual(result.nodes, {})
            self.assertEqual(result.edges, [])

    def test_directory_with_no_markdown_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "agents").mkdir()
            (root / "agents" / "ignore.txt").write_text("not md\n")
            result = extract(root, {"glob": "agents/*.md"}, {})
            self.assertEqual(result.nodes, {})


class TestFrontmatterMdHappyPath(unittest.TestCase):
    """Canonical frontmatter parsing must populate name/description/model."""

    def test_extracts_agent_node_with_frontmatter_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agents = root / "agents"
            agents.mkdir()
            (agents / "scout.md").write_text(_HAPPY_AGENT, encoding="utf-8")
            result = extract(root, {"glob": "agents/*.md"}, {})
            self.assertIn("agent:scout", result.nodes)
            node = result.nodes["agent:scout"]
            self.assertEqual(node["type"], "agent")
            self.assertEqual(node["label"], "scout")
            props = node["props"]
            self.assertEqual(props["file"], "agents/scout.md")
            self.assertEqual(
                props["description"], "Reads source and surfaces facts."
            )
            self.assertEqual(props["model"], "sonnet")
            self.assertEqual(props["source_strategy"], "frontmatter_md")
            self.assertEqual(props["authority"], "canonical")
            self.assertEqual(props["confidence"], "definite")
            self.assertEqual(props["roles"], ["config"])

    def test_frontmatter_name_overrides_filename_stem(self) -> None:
        # The frontmatter ``name`` field takes precedence over the file
        # stem so renames in the file system do not silently fork the id.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agents = root / "agents"
            agents.mkdir()
            (agents / "old_filename.md").write_text(
                _HAPPY_AGENT, encoding="utf-8"
            )
            result = extract(root, {"glob": "agents/*.md"}, {})
            self.assertIn("agent:scout", result.nodes)
            self.assertNotIn("agent:old_filename", result.nodes)


class TestFrontmatterMdEdgeCases(unittest.TestCase):
    """Files lacking or with malformed frontmatter must degrade gracefully."""

    def test_file_without_frontmatter_uses_stem_and_blank_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agents = root / "agents"
            agents.mkdir()
            (agents / "plain.md").write_text(_NO_FRONTMATTER, encoding="utf-8")
            result = extract(root, {"glob": "agents/*.md"}, {})
            self.assertIn("agent:plain", result.nodes)
            props = result.nodes["agent:plain"]["props"]
            self.assertEqual(props["description"], "")
            self.assertEqual(props["model"], "")

    def test_unclosed_frontmatter_falls_back_to_stem(self) -> None:
        # No closing ``---`` means the parser cannot trust any field
        # inside; the only safe identity is the file stem.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agents = root / "agents"
            agents.mkdir()
            (agents / "broken.md").write_text(
                _UNCLOSED_FRONTMATTER, encoding="utf-8"
            )
            result = extract(root, {"glob": "agents/*.md"}, {})
            self.assertIn("agent:broken", result.nodes)
            # Name was NOT pulled from the unclosed frontmatter.
            self.assertNotIn("agent:incomplete", result.nodes)


if __name__ == "__main__":
    unittest.main()
