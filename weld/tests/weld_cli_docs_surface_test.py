"""Regression guard for the canonical Weld CLI and package ownership docs."""

from __future__ import annotations

import unittest
from pathlib import Path

ACTIVE_DOCS = (
    "AGENTS.md",
    "CLAUDE.md",
    ".claude/settings.json",
    ".claude/agents/weld.md",
    ".claude/agents/tdd.md",
    ".claude/agents/worker.md",
    ".claude/commands/weld.md",
    ".claude/commands/execute.md",
    ".claude/commands/cycle.md",
    ".claude/commands/enrich-weld.md",
    "weld/README.md",
    "weld/docs/onboarding.md",
    "weld/docs/agent-workflow.md",
    "docs/weld-metadata-contract.md",
    "weld/templates/external_adapter.py",
    "weld/_yaml.py",
)

def _legacy(*parts: str) -> str:
    return "".join(parts)


FORBIDDEN_PATTERNS = (
    f"tools/{_legacy('k', 'g')}",
    _legacy("k", "g", "_graph.py"),
    _legacy("k", "g", "_discover.py"),
    _legacy("k", "g", "_file_index.py"),
    _legacy("k", "g", "_init.py"),
)

SPEC_DRIVEN_DOCS = (
    "CLAUDE.md",
    ".claude/commands/execute.md",
)

FORBIDDEN_TDD_MANDATES = (
    "red-green-refactor",
    "Follow your TDD phases",
    "ADR gate -> TDD -> QA -> Security",
)

class WeldCliDocsSurfaceTest(unittest.TestCase):
    def test_active_docs_do_not_teach_legacy_entrypoints(self) -> None:
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

    def test_spec_driven_docs_replace_universal_tdd_language(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        findings: list[str] = []
        checked = 0

        for rel_path in SPEC_DRIVEN_DOCS:
            full_path = repo_root / rel_path
            if not full_path.exists():
                continue
            text = full_path.read_text(encoding="utf-8")
            checked += 1
            lowered = text.lower()
            if "spec-driven" not in lowered and "spec-driven" not in text:
                findings.append(f"{rel_path}: missing spec-driven language")
            for pattern in FORBIDDEN_TDD_MANDATES:
                if pattern in text:
                    findings.append(f"{rel_path}: still contains {pattern!r}")

        if checked == 0:
            self.skipTest("No SPEC_DRIVEN_DOCS files found in this repo context")
        self.assertEqual(findings, [])

if __name__ == "__main__":
    unittest.main()
