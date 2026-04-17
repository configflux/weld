"""Tests for sentinel-aware federation guidance in wd bootstrap.

When ``.weld/workspaces.yaml`` exists at the bootstrap target root the
generated guidance files must include a federation paragraph pointing
agents at ``wd workspace status`` before querying. When the sentinel is
absent the single-repo variant (no federation paragraph) is emitted.

Applies symmetrically to: copilot skill + instruction, codex skill, and
claude command. The codex MCP config file (``.codex/config.toml``) is
topology-agnostic and must not carry the paragraph. The ``.weld/README.md``
is stable reference content and must not be mutated either.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld.bootstrap import bootstrap


def _write_workspaces_sentinel(root: Path) -> None:
    """Create a minimal ``.weld/workspaces.yaml`` at *root*."""
    sentinel = root / ".weld" / "workspaces.yaml"
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text("children: []\n", encoding="utf-8")


def _federation_markers() -> tuple[str, ...]:
    """Content markers expected in a federation paragraph.

    Drawn from the plan's required phrasing for this issue: workspaces.yaml
    sentinel, federated root discovery, and the ``wd workspace status``
    entry point for picking a child.
    """
    return (
        ".weld/workspaces.yaml",
        "wd workspace status",
        "polyrepo",
        "federated",
    )


def _assert_has_federation(test: unittest.TestCase, content: str) -> None:
    for marker in _federation_markers():
        test.assertIn(
            marker,
            content,
            f"federation marker {marker!r} missing from generated content",
        )


def _assert_no_federation(test: unittest.TestCase, content: str) -> None:
    # The stable instruction template already includes the word
    # ``workspaces.yaml`` and ``federation`` in its body as part of the
    # sentinel awareness note. The federation paragraph we append is
    # identified by the ``wd workspace status`` handoff, which is unique to
    # the appended section -- absence of that phrase is the reliable signal
    # that federation mode was not triggered.
    test.assertNotIn(
        "wd workspace status",
        content,
        "federation paragraph leaked into single-repo output",
    )


class CopilotFederationTest(unittest.TestCase):
    """Copilot emits both skill and instruction; both must carry federation."""

    def test_skill_has_federation_when_sentinel_present(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_workspaces_sentinel(root)
            bootstrap("copilot", root, force=True)
            skill = root / ".github" / "skills" / "weld" / "SKILL.md"
            _assert_has_federation(self, skill.read_text(encoding="utf-8"))

    def test_instructions_have_federation_when_sentinel_present(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_workspaces_sentinel(root)
            bootstrap("copilot", root, force=True)
            instructions = (
                root / ".github" / "instructions" / "weld.instructions.md"
            )
            _assert_has_federation(
                self, instructions.read_text(encoding="utf-8")
            )

    def test_skill_has_no_federation_when_sentinel_absent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("copilot", root, force=True)
            skill = root / ".github" / "skills" / "weld" / "SKILL.md"
            _assert_no_federation(self, skill.read_text(encoding="utf-8"))

    def test_instructions_have_no_federation_when_sentinel_absent(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("copilot", root, force=True)
            instructions = (
                root / ".github" / "instructions" / "weld.instructions.md"
            )
            _assert_no_federation(
                self, instructions.read_text(encoding="utf-8")
            )


class CodexFederationTest(unittest.TestCase):
    """Codex skill must carry federation; config.toml must not."""

    def test_skill_has_federation_when_sentinel_present(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_workspaces_sentinel(root)
            bootstrap("codex", root, force=True)
            skill = root / ".codex" / "skills" / "weld" / "SKILL.md"
            _assert_has_federation(self, skill.read_text(encoding="utf-8"))

    def test_skill_has_no_federation_when_sentinel_absent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("codex", root, force=True)
            skill = root / ".codex" / "skills" / "weld" / "SKILL.md"
            _assert_no_federation(self, skill.read_text(encoding="utf-8"))

    def test_mcp_config_is_not_mutated_by_federation(self) -> None:
        """Federation is topology guidance; MCP server config is not."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_workspaces_sentinel(root)
            bootstrap("codex", root, force=True)
            config = root / ".codex" / "config.toml"
            self.assertTrue(config.is_file())
            content = config.read_text(encoding="utf-8")
            self.assertNotIn("wd workspace status", content)
            self.assertNotIn("polyrepo", content)


class ClaudeFederationTest(unittest.TestCase):
    """Claude command must carry federation when sentinel present."""

    def test_command_has_federation_when_sentinel_present(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_workspaces_sentinel(root)
            bootstrap("claude", root, force=True)
            cmd = root / ".claude" / "commands" / "weld.md"
            _assert_has_federation(self, cmd.read_text(encoding="utf-8"))

    def test_command_has_no_federation_when_sentinel_absent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bootstrap("claude", root, force=True)
            cmd = root / ".claude" / "commands" / "weld.md"
            _assert_no_federation(self, cmd.read_text(encoding="utf-8"))


class FederationComposesWithCliOnlyTest(unittest.TestCase):
    """Federation paragraph must compose with --cli-only (opt-out variant).

    The implementation appends the federation paragraph in code rather than
    shipping federation.* template variants, so federation + any opt-out
    flag combination must still produce a file that carries the paragraph.
    """

    def test_copilot_cli_only_with_sentinel_has_federation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_workspaces_sentinel(root)
            bootstrap("copilot", root, force=True, cli_only=True)
            skill = root / ".github" / "skills" / "weld" / "SKILL.md"
            instructions = (
                root / ".github" / "instructions" / "weld.instructions.md"
            )
            for path in (skill, instructions):
                _assert_has_federation(
                    self, path.read_text(encoding="utf-8")
                )
            # And --cli-only still did its job: no MCP / enrich mentions.
            for path in (skill, instructions):
                content = path.read_text(encoding="utf-8").lower()
                self.assertNotIn("mcp", content)
                self.assertNotIn("wd enrich", content)

    def test_codex_cli_only_with_sentinel_has_federation_and_drops_mcp(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_workspaces_sentinel(root)
            bootstrap("codex", root, force=True, cli_only=True)
            skill = root / ".codex" / "skills" / "weld" / "SKILL.md"
            _assert_has_federation(self, skill.read_text(encoding="utf-8"))
            self.assertFalse(
                (root / ".codex" / "config.toml").is_file(),
                "codex --cli-only must still drop config.toml",
            )


class ReadmeNotMutatedByFederationTest(unittest.TestCase):
    """`.weld/README.md` is reference content and must not carry federation.

    Only topology-dependent markdown assets (skill, instructions, command)
    get the federation paragraph. The README describes `.weld/` semantics
    at a level that is orthogonal to single-repo vs polyrepo layout.
    """

    def test_readme_has_no_federation_paragraph(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_workspaces_sentinel(root)
            bootstrap("claude", root, force=True)
            readme = root / ".weld" / "README.md"
            self.assertTrue(readme.is_file())
            self.assertNotIn(
                "wd workspace status",
                readme.read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
