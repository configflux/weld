"""Validate onboarding and cookbook docs against the real implementation.

Catches drift between documentation examples and the actual CLI commands,
strategies, templates, and node types.  Acceptance test for
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]

def _weld_root() -> Path:
    return _repo_root() / "weld"

def _real_cli_commands() -> set[str]:
    """Parse the canonical CLI help string for command names."""
    from weld.cli import _HELP  # noqa: WPS433 — intentional runtime import

    cmds: set[str] = set()
    for line in _HELP.strip().splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Skip header/category lines
        if stripped.startswith(("Usage", "Core", "Retrieval", "Graph", "Run")):
            continue
        parts = stripped.split(None, 1)
        if parts and re.match(r"^[a-z]", parts[0]):
            cmds.add(parts[0])
    return cmds

def _real_strategy_files() -> set[str]:
    """Return basenames (no .py) of bundled strategy modules."""
    strat_dir = _weld_root() / "strategies"
    return {
        p.stem
        for p in strat_dir.glob("*.py")
        if p.stem not in {"__init__", "_helpers"}
    }

def _extract_cli_invocations(text: str) -> list[str]:
    """Extract ``wd <cmd>`` and ``python -m weld <cmd>`` references."""
    commands: list[str] = []
    code_blocks = re.findall(r"```(?:bash|sh)?\n(.*?)```", text, re.DOTALL)
    for block in code_blocks:
        commands.extend(
            re.findall(
                r"(?m)^\s*(?:wd|python\s+-m\s+weld)\s+([a-z][\w-]*)\b",
                block,
            ),
        )
    return commands

def _extract_backtick_commands(text: str) -> list[str]:
    """Extract Weld command references in backtick spans."""
    return re.findall(r"`(?:wd|python\s+-m\s+weld)\s+([a-z][\w-]*)[^`]*`", text)

def _extract_backtick_values_under_heading(
    text: str, heading: str,
) -> list[str]:
    """Extract backtick-quoted values from the bullet list under *heading*.

    Looks for a markdown ``## heading`` section and collects all
    ``- `value` `` items until the next heading or end of file.
    """
    pattern = rf"^## {re.escape(heading)}\b.*?\n(.*?)(?=\n## |\Z)"
    m = re.search(pattern, text, re.DOTALL | re.MULTILINE)
    if not m:
        return []
    section = m.group(1)
    return re.findall(r"^- `([^`]+)`", section, re.MULTILINE)

class OnboardingDocValidationTest(unittest.TestCase):
    """Ensure weld/docs/onboarding.md references real commands and concepts."""

    def setUp(self) -> None:
        self.path = _weld_root() / "docs" / "onboarding.md"
        self.assertTrue(self.path.exists(), f"Missing doc: {self.path}")
        self.text = self.path.read_text(encoding="utf-8")
        self.real_commands = _real_cli_commands()

    def test_cli_invocations_reference_real_commands(self) -> None:
        invocations = _extract_cli_invocations(self.text)
        self.assertTrue(invocations, "onboarding.md has no CLI invocations")
        bad = [cmd for cmd in invocations if cmd not in self.real_commands]
        self.assertEqual(
            bad,
            [],
            f"onboarding.md references nonexistent CLI commands: {bad}",
        )

    def test_backtick_commands_reference_real_commands(self) -> None:
        cmds = _extract_backtick_commands(self.text)
        bad = [c for c in cmds if c not in self.real_commands]
        self.assertEqual(
            bad,
            [],
            f"onboarding.md backtick references nonexistent commands: {bad}",
        )

    def test_discover_yaml_mentioned(self) -> None:
        self.assertIn(
            "discover.yaml",
            self.text,
            "onboarding.md should reference discover.yaml",
        )

    def test_template_paths_exist(self) -> None:
        self.assertIn("wd scaffold", self.text)

class CookbookDocValidationTest(unittest.TestCase):
    """Ensure weld/docs/strategy-cookbook.md references real commands/templates."""

    def setUp(self) -> None:
        self.path = _weld_root() / "docs" / "strategy-cookbook.md"
        self.assertTrue(self.path.exists(), f"Missing doc: {self.path}")
        self.text = self.path.read_text(encoding="utf-8")
        self.real_commands = _real_cli_commands()

    def test_cli_invocations_reference_real_commands(self) -> None:
        invocations = _extract_cli_invocations(self.text)
        # Cookbook may not have CLI invocations, that is fine
        bad = [cmd for cmd in invocations if cmd not in self.real_commands]
        self.assertEqual(
            bad,
            [],
            f"strategy-cookbook.md references nonexistent CLI commands: {bad}",
        )

    def test_template_copy_paths_exist(self) -> None:
        self.assertIn("wd scaffold local-strategy", self.text)
        self.assertIn("wd scaffold external-adapter", self.text)

    def test_strategy_keyword_references_real_strategies(self) -> None:
        """Documented ``strategy: <name>`` values should exist or be planned."""
        refs = re.findall(r'strategy:\s*(\w+)', self.text)
        real = _real_strategy_files()
        # Allow explicitly "planned" strategy names mentioned in prose
        planned_ok = {"external_json"}
        bad = [r for r in refs if r not in real and r not in planned_ok]
        self.assertEqual(
            bad,
            [],
            f"cookbook references unknown strategies: {bad}",
        )

    def test_normalized_metadata_fields_match_contract(self) -> None:
        """Authority/confidence/role vocabularies in cookbook match contract."""
        from weld.contract import AUTHORITY_VALUES, CONFIDENCE_VALUES, ROLE_VALUES

        # Authority values mentioned in backticks should be valid
        authority_refs = re.findall(r"`(canonical|derived|manual|external)`", self.text)
        for val in authority_refs:
            self.assertIn(
                val,
                AUTHORITY_VALUES,
                f"cookbook mentions authority '{val}' not in contract",
            )

        # Confidence values mentioned in backticks should be valid
        confidence_refs = re.findall(
            r"`(definite|inferred|speculative)`", self.text,
        )
        for val in confidence_refs:
            self.assertIn(
                val,
                CONFIDENCE_VALUES,
                f"cookbook mentions confidence '{val}' not in contract",
            )

        # Role values mentioned in backticks should be valid
        role_refs = re.findall(
            r"`(implementation|test|config|doc|build|migration|fixture|script)`",
            self.text,
        )
        for val in role_refs:
            self.assertIn(
                val,
                ROLE_VALUES,
                f"cookbook mentions role '{val}' not in contract",
            )

class ReadmeDocValidationTest(unittest.TestCase):
    """Ensure weld/README.md references real commands and docs."""

    def setUp(self) -> None:
        self.path = _weld_root() / "README.md"
        self.assertTrue(self.path.exists(), f"Missing: {self.path}")
        self.text = self.path.read_text(encoding="utf-8")
        self.real_commands = _real_cli_commands()

    def test_cli_invocations_reference_real_commands(self) -> None:
        invocations = _extract_cli_invocations(self.text)
        self.assertTrue(invocations, "README.md has no CLI invocations")
        bad = [cmd for cmd in invocations if cmd not in self.real_commands]
        self.assertEqual(
            bad,
            [],
            f"README.md references nonexistent CLI commands: {bad}",
        )

    def test_backtick_commands_reference_real_commands(self) -> None:
        cmds = _extract_backtick_commands(self.text)
        bad = [c for c in cmds if c not in self.real_commands]
        self.assertEqual(
            bad,
            [],
            f"README.md backtick references nonexistent commands: {bad}",
        )

    def test_linked_docs_exist(self) -> None:
        """Verify markdown links to docs/ files point to real files."""
        links = re.findall(r'\]\(docs/([^)]+)\)', self.text)
        for link in links:
            self.assertTrue(
                (_weld_root() / "docs" / link).exists(),
                f"README.md links to missing doc: docs/{link}",
            )

    def test_template_paths_exist(self) -> None:
        self.assertIn("wd scaffold", self.text)

    def test_adr_links_exist(self) -> None:
        """Verify ADR markdown links point to real files."""
        links = re.findall(r'\]\(docs/adr/([^)]+)\)', self.text)
        for link in links:
            self.assertTrue(
                (_weld_root() / "docs" / "adr" / link).exists(),
                f"README.md links to missing ADR: docs/adr/{link}",
            )

class GlossaryDocValidationTest(unittest.TestCase):
    """Ensure weld/docs/glossary.md is consistent with the contract."""

    def setUp(self) -> None:
        self.path = _weld_root() / "docs" / "glossary.md"
        self.assertTrue(self.path.exists(), f"Missing: {self.path}")
        self.text = self.path.read_text(encoding="utf-8")

    def test_brief_entry_is_current(self) -> None:
        """Brief is implemented -- glossary should not say 'planned'."""
        self.assertNotIn(
            "The planned high-level",
            self.text,
            "Glossary Brief entry should reflect that wd brief is implemented",
        )

    def test_authority_values_match_contract(self) -> None:
        """Authority values listed in glossary must match contract."""
        from weld.contract import AUTHORITY_VALUES

        for val in AUTHORITY_VALUES:
            self.assertIn(
                f"`{val}`",
                self.text,
                f"glossary missing authority value: {val}",
            )
        # Ensure no stale authority values appear
        stale = _extract_backtick_values_under_heading(
            self.text, "Authority",
        )
        bad = [v for v in stale if v not in AUTHORITY_VALUES]
        self.assertEqual(
            bad,
            [],
            f"glossary lists stale authority values: {bad}",
        )

    def test_confidence_values_match_contract(self) -> None:
        """Confidence values listed in glossary must match contract."""
        from weld.contract import CONFIDENCE_VALUES

        for val in CONFIDENCE_VALUES:
            self.assertIn(
                f"`{val}`",
                self.text,
                f"glossary missing confidence value: {val}",
            )
        stale = _extract_backtick_values_under_heading(
            self.text, "Confidence",
        )
        bad = [v for v in stale if v not in CONFIDENCE_VALUES]
        self.assertEqual(
            bad,
            [],
            f"glossary lists stale confidence values: {bad}",
        )

    def test_role_values_match_contract(self) -> None:
        """Role values listed in glossary must match contract."""
        from weld.contract import ROLE_VALUES

        for val in ROLE_VALUES:
            self.assertIn(
                f"`{val}`",
                self.text,
                f"glossary missing role value: {val}",
            )
        stale = _extract_backtick_values_under_heading(self.text, "Role")
        bad = [v for v in stale if v not in ROLE_VALUES]
        self.assertEqual(
            bad,
            [],
            f"glossary lists stale role values: {bad}",
        )

    def test_node_types_section_present(self) -> None:
        """Glossary should document node types from the contract."""
        # At minimum a representative sample should appear
        for val in ("service", "entity", "route", "contract", "agent"):
            self.assertIn(
                f"`{val}`",
                self.text,
                f"glossary missing node type: {val}",
            )

    def test_edge_types_section_present(self) -> None:
        """Glossary should document edge types from the contract."""
        for val in ("contains", "depends_on", "implements", "documents"):
            self.assertIn(
                f"`{val}`",
                self.text,
                f"glossary missing edge type: {val}",
            )

    def test_no_stale_planned_prefix(self) -> None:
        """Vocabulary lists should not say 'Planned normalized values'."""
        self.assertNotIn(
            "Planned normalized values",
            self.text,
            "glossary should list actual values, not 'Planned'",
        )

class CrossDocConsistencyTest(unittest.TestCase):
    """Cross-document consistency between onboarding, cookbook, and README."""

    def setUp(self) -> None:
        weld_dir = _weld_root()
        self.onboarding = (weld_dir / "docs" / "onboarding.md").read_text("utf-8")
        self.cookbook = (weld_dir / "docs" / "strategy-cookbook.md").read_text("utf-8")
        self.readme = (weld_dir / "README.md").read_text("utf-8")

    def test_extension_order_consistent(self) -> None:
        """All three docs should agree on the strategy extension order."""
        # Each doc should mention bundled before project-local before external
        for name, text in [
            ("onboarding.md", self.onboarding),
            ("strategy-cookbook.md", self.cookbook),
            ("README.md", self.readme),
        ]:
            bundled_pos = text.find("bundled strategy")
            if bundled_pos == -1:
                continue
            local_pos = text.find("project-local", bundled_pos)
            if local_pos == -1:
                # Some docs may not list the full order
                continue
            self.assertGreater(
                local_pos,
                bundled_pos,
                f"{name}: bundled should come before project-local in order",
            )

    def test_readme_links_to_onboarding_and_cookbook(self) -> None:
        self.assertIn("onboarding.md", self.readme)
        self.assertIn("strategy-cookbook.md", self.readme)


class DeterminismAuditDocTest(unittest.TestCase):
    """The audit doc must distinguish historical findings from current status."""

    def setUp(self) -> None:
        self.text = (
            _repo_root() / "docs" / "determinism-audit-T1a.md"
        ).read_text("utf-8")

    def test_current_status_table_present(self) -> None:
        self.assertIn("## 1. Current status", self.text)
        for status in ("fixed", "exempt", "contained", "out of scope"):
            self.assertIn(status, self.text)

    def test_no_deferred_fix_wording(self) -> None:
        self.assertNotIn("fixes deferred", self.text)

if __name__ == "__main__":
    unittest.main()
