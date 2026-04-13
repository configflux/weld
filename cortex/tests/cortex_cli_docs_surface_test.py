"""Regression guard for the canonical cortex CLI and package ownership docs."""

from __future__ import annotations

import unittest
from pathlib import Path

ACTIVE_DOCS = (
    "AGENTS.md",
    "CLAUDE.md",
    ".claude/settings.json",
    ".claude/agents/cortex.md",
    ".claude/agents/tdd.md",
    ".claude/agents/worker.md",
    ".claude/commands/cortex.md",
    ".claude/commands/execute.md",
    ".claude/commands/cycle.md",
    ".claude/commands/enrich-cortex.md",
    "cortex/README.md",
    "cortex/docs/onboarding.md",
    "cortex/docs/agent-workflow.md",
    "docs/cortex-metadata-contract.md",
    "cortex/templates/external_adapter.py",
    "cortex/_yaml.py",
)

FORBIDDEN_PATTERNS = (
    "tools/kg",
    "kg_graph.py",
    "kg_discover.py",
    "kg_file_index.py",
    "kg_init.py",
)

class CortexCliDocsSurfaceTest(unittest.TestCase):
    def test_active_docs_do_not_teach_tools_kg_entrypoints(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        findings: list[str] = []
        checked = 0

        for rel_path in ACTIVE_DOCS:
            full_path = repo_root / rel_path
            if not full_path.exists():
                continue  # file not present in this repo context
            text = full_path.read_text(encoding="utf-8")
            checked += 1
            for pattern in FORBIDDEN_PATTERNS:
                if pattern in text:
                    findings.append(f"{rel_path}: found forbidden reference {pattern!r}")

        self.assertGreater(checked, 0, "No ACTIVE_DOCS files found at all")
        self.assertEqual(findings, [])

if __name__ == "__main__":
    unittest.main()
