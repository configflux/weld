"""Regression guard: agent-workflow.md must document all retrieval surfaces."""

from __future__ import annotations

import unittest
from pathlib import Path

# The five retrieval commands that must be documented
REQUIRED_COMMANDS = ("brief", "query", "context", "path", "find")

# Sections that must exist in the doc
REQUIRED_SECTIONS = (
    "The five retrieval surfaces",
    "Start with `brief`",
    "When to drop to low-level surfaces",
    "Typical agent workflow",
    "Decision matrix",
)

class AgentWorkflowDocTest(unittest.TestCase):
    """Validate that weld/docs/agent-workflow.md covers all retrieval surfaces."""

    def setUp(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        self.doc_path = repo_root / "weld" / "docs" / "agent-workflow.md"
        self.assertTrue(
            self.doc_path.exists(),
            f"Missing required doc: {self.doc_path}",
        )
        self.text = self.doc_path.read_text(encoding="utf-8")

    def test_doc_exists_and_not_empty(self) -> None:
        self.assertGreater(len(self.text.strip()), 100)

    def test_all_retrieval_commands_documented(self) -> None:
        missing = []
        for cmd in REQUIRED_COMMANDS:
            # Look for the command in backtick form and as a heading term
            if f"`weld {cmd}`" not in self.text and f"`{cmd}`" not in self.text:
                missing.append(cmd)
        self.assertEqual(
            missing,
            [],
            f"agent-workflow.md is missing documentation for: {missing}",
        )

    def test_required_sections_present(self) -> None:
        missing = [s for s in REQUIRED_SECTIONS if s not in self.text]
        self.assertEqual(
            missing,
            [],
            f"agent-workflow.md is missing sections: {missing}",
        )

    def test_trust_boundary_documented(self) -> None:
        self.assertIn("Trust boundary", self.text)
        self.assertIn("repositories you trust", self.text)
        self.assertIn("external_json", self.text)

    def test_readme_references_agent_workflow(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        readme = (repo_root / "weld" / "README.md").read_text(encoding="utf-8")
        self.assertIn(
            "agent-workflow.md",
            readme,
            "weld/README.md must reference docs/agent-workflow.md",
        )

    def test_glossary_brief_entry_updated(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        glossary = (repo_root / "weld" / "docs" / "glossary.md").read_text(
            encoding="utf-8"
        )
        # Brief entry should not say "planned" now that it is implemented
        self.assertNotIn(
            "The planned high-level",
            glossary,
            "Glossary Brief entry should reflect that wd brief is implemented",
        )

if __name__ == "__main__":
    unittest.main()
