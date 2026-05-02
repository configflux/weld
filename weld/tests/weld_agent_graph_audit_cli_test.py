"""CLI tests for ``wd agents audit``."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Iterator

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.cli import main as wd_main  # noqa: E402


@contextmanager
def _cwd(path: Path) -> Iterator[None]:
    old = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


def _write(root: Path, rel_path: str, text: str = "content\n") -> None:
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _run(argv: list[str], root: Path) -> tuple[int, str, str]:
    out = io.StringIO()
    err = io.StringIO()
    with _cwd(root), redirect_stdout(out), redirect_stderr(err):
        rc = wd_main(argv)
    return rc, out.getvalue(), err.getvalue()


class AgentGraphAuditCliTest(unittest.TestCase):
    def test_agents_audit_json_reports_static_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _conflict_workspace(root)

            self.assertEqual(_run(["agents", "discover"], root)[0], 0)
            rc, stdout, stderr = _run(["agents", "audit", "--json"], root)

            self.assertEqual((rc, stderr), (0, ""))
            payload = json.loads(stdout)
            codes = {finding["code"] for finding in payload["findings"]}
            self.assertTrue({
                "broken_reference",
                "duplicate_name",
                "missing_agent",
                "missing_mcp_config",
                "path_scope_overlap",
                "permission_conflict",
                "platform_drift",
                "responsibility_overlap",
                "unsafe_hook",
                "unused_skill",
                "vague_description",
            } <= codes)
            self.assertEqual(payload["summary"]["finding_count"], len(payload["findings"]))

    def test_agents_audit_human_output_is_readable(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _conflict_workspace(root)

            self.assertEqual(_run(["agents", "discover"], root)[0], 0)
            rc, stdout, stderr = _run(["agents", "audit"], root)

            self.assertEqual((rc, stderr), (0, ""))
            self.assertIn("Agent Graph audit findings:", stdout)
            self.assertIn("Broken reference", stdout)
            self.assertIn("Tool permission conflict", stdout)


class AgentGraphAuditCanonicalRenderedSuppressionTest(unittest.TestCase):
    """ADR 0029: canonical->rendered pairs must not flag duplicate_name
    or vague_description findings."""

    def _findings_for(self, root: Path) -> list[dict]:
        self.assertEqual(_run(["agents", "discover"], root)[0], 0)
        rc, stdout, stderr = _run(["agents", "audit", "--json"], root)
        self.assertEqual((rc, stderr), (0, ""))
        return json.loads(stdout)["findings"]

    def test_canonical_rendered_pair_does_not_flag_duplicate_name(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _canonical_rendered_workspace(root)
            findings = self._findings_for(root)
            offending = [
                f for f in findings
                if f["code"] == "duplicate_name"
                and any(n["name"] == "planner" for n in f["nodes"])
            ]
            self.assertEqual(offending, [], msg=findings)

    def test_rendered_copy_with_empty_description_does_not_flag_vague(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _canonical_rendered_workspace(root)
            findings = self._findings_for(root)
            offending = [
                f for f in findings
                if f["code"] == "vague_description"
                and any(
                    ".claude/agents/planner.md" in n.get("path", "")
                    for n in f["nodes"]
                )
            ]
            self.assertEqual(offending, [], msg=findings)

    def test_unrelated_duplicate_still_flags_duplicate_name(self) -> None:
        """Suppression only applies to pairs linked by generated_from."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(
                root,
                ".github/agents/planner.agent.md",
                "---\nname: planner\ndescription: Plans implementation.\n---\n",
            )
            _write(
                root,
                ".claude/agents/planner.md",
                "---\nname: planner\ndescription: Drafts implementation.\n---\n",
            )
            findings = self._findings_for(root)
            duplicates = [f for f in findings if f["code"] == "duplicate_name"]
            self.assertTrue(
                any(
                    any(n["name"] == "planner" for n in f["nodes"])
                    for f in duplicates
                ),
                msg=findings,
            )


class AgentGraphAuditUnusedSkillSuppressionTest(unittest.TestCase):
    """A skill mentioned by name in a discovered agent or instruction
    file body is treated as referenced, even without an explicit
    ``uses_skill`` edge. Suppresses noise on instruction-mediated repos
    (AGENTS.md / project conventions activate skills indirectly)."""

    def _findings_for(self, root: Path) -> list[dict]:
        self.assertEqual(_run(["agents", "discover"], root)[0], 0)
        rc, stdout, stderr = _run(["agents", "audit", "--json"], root)
        self.assertEqual((rc, stderr), (0, ""))
        return json.loads(stdout)["findings"]

    def test_skill_text_mentioned_in_instruction_is_not_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(
                root, ".claude/skills/planner-helper/SKILL.md",
                "---\nname: planner-helper\n"
                "description: Helps the planner break work into steps.\n"
                "---\n",
            )
            _write(
                root, "AGENTS.md",
                "# Project conventions\n\n"
                "Always invoke planner-helper before drafting an issue.\n",
            )
            findings = self._findings_for(root)
            offending = [
                f for f in findings
                if f["code"] == "unused_skill"
                and any(n["name"] == "planner-helper" for n in f["nodes"])
            ]
            self.assertEqual(offending, [], msg=findings)
            graph = json.loads((root / ".weld" / "agent-graph.json").read_text())
            text_only_edges = [
                e for e in graph["edges"]
                if e.get("type") == "uses_skill"
                and e.get("to", "").endswith(":planner-helper")
            ]
            self.assertEqual(text_only_edges, [])

    def test_unmentioned_skill_still_flagged(self) -> None:
        """Suppression is text-driven; a truly unreferenced skill fires."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(
                root, ".claude/skills/orphan/SKILL.md",
                "---\nname: orphan\n"
                "description: Standalone skill with no callers.\n"
                "---\n",
            )
            _write(
                root, "AGENTS.md",
                "# Project conventions\n\n"
                "We mostly use planner-helper here.\n",
            )
            findings = self._findings_for(root)
            offending = [
                f for f in findings
                if f["code"] == "unused_skill"
                and any(n["name"] == "orphan" for n in f["nodes"])
            ]
            self.assertEqual(len(offending), 1, msg=findings)

    def test_short_skill_name_substring_does_not_suppress(self) -> None:
        """A short skill name like 'test' must NOT be suppressed by a
        substring match inside a larger word ('attestation'). Otherwise
        common-name skills get silently treated as referenced and real
        orphans are hidden.
        """
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(
                root, ".claude/skills/test/SKILL.md",
                "---\nname: test\n"
                "description: Runs a project test pass.\n"
                "---\n",
            )
            _write(
                root, "AGENTS.md",
                "# Project conventions\n\n"
                "Attestation evidence is required before release.\n"
                "Initialization steps must be planted in the planner.\n",
            )
            findings = self._findings_for(root)
            offending = [
                f for f in findings
                if f["code"] == "unused_skill"
                and any(n["name"] == "test" for n in f["nodes"])
            ]
            self.assertEqual(len(offending), 1, msg=findings)

    def test_short_skill_name_whole_word_still_suppresses(self) -> None:
        """The tightened check must still fire on legitimate whole-word
        mentions; suppression isn't crippled, only narrowed."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(
                root, ".claude/skills/test/SKILL.md",
                "---\nname: test\n"
                "description: Runs a project test pass.\n"
                "---\n",
            )
            _write(
                root, "AGENTS.md",
                "# Project conventions\n\n"
                "Run test before opening a PR.\n",
            )
            findings = self._findings_for(root)
            offending = [
                f for f in findings
                if f["code"] == "unused_skill"
                and any(n["name"] == "test" for n in f["nodes"])
            ]
            self.assertEqual(offending, [], msg=findings)

    def test_skill_text_mentioned_in_agent_body_is_not_flagged(self) -> None:
        """Suppression also reads agent bodies, not just instructions.

        Both ``agent`` and ``instruction`` asset types feed the
        text-mention check. This pins the agent half of the contract.
        """
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(
                root, ".claude/skills/diagram-helper/SKILL.md",
                "---\nname: diagram-helper\n"
                "description: Renders C4 diagrams from the graph.\n"
                "---\n",
            )
            _write(
                root, ".claude/agents/architect.md",
                "---\nname: architect\n"
                "description: Reviews designs and writes ADRs.\n"
                "---\n\n"
                "When sketching component boundaries, prefer "
                "diagram-helper over freehand prose.\n",
            )
            findings = self._findings_for(root)
            offending = [
                f for f in findings
                if f["code"] == "unused_skill"
                and any(n["name"] == "diagram-helper" for n in f["nodes"])
            ]
            self.assertEqual(offending, [], msg=findings)


def _canonical_rendered_workspace(root: Path) -> None:
    """Customer scratch repo: canonical Copilot agent + rendered Claude target."""
    _write(
        root,
        ".github/agents/planner.agent.md",
        "---\n"
        "name: planner\n"
        "description: Plans implementation changes.\n"
        "---\n"
        "Body of the planner agent.\n",
    )
    _write(
        root,
        ".weld/agents.yaml",
        "agents:\n"
        "  planner:\n"
        "    canonical: .github/agents/planner.agent.md\n"
        "    renders:\n"
        "      - .claude/agents/planner.md\n",
    )
    _write(
        root,
        ".claude/agents/planner.md",
        "<!-- Generated by Weld from .github/agents/planner.agent.md;"
        " do not edit by hand. -->\n"
        "<!-- Run `wd agents render` to regenerate. -->\n"
        "Body of the planner agent.\n",
    )


def _conflict_workspace(root: Path) -> None:
    _write(root, "AGENTS.md", "Use @docs/missing.md and mcp:github.\n")
    _write(
        root,
        ".github/agents/planner.agent.md",
        "---\nname: planner\ndescription: Plans changes.\n"
        "tools: [editFiles]\n---\n",
    )
    _write(
        root,
        ".claude/agents/planner.md",
        "---\nname: planner\ndescription: Drafts implementation plans.\n"
        "denied_tools: [editFiles]\n---\n",
    )
    _write(root, ".github/agents/reviewer.agent.md", "---\nname: reviewer\n"
           "description: Reviews dependencies.\n---\n")
    _write(root, ".github/instructions/cpp.instructions.md",
           "---\napplyTo: [src/**]\n---\n")
    _write(root, ".github/instructions/testing.instructions.md",
           "---\napplyTo: [src/**]\n---\n")
    _write(root, ".claude/skills/security-review/SKILL.md",
           "---\nname: security-review\ndescription: Reviews dependencies.\n---\n")
    _write(root, ".claude/skills/vague/SKILL.md",
           "---\nname: vague\ndescription: stuff\n---\n")
    _write(
        root,
        "opencode.json",
        json.dumps({
            "commands": {"deploy": {"description": "Run agent:missing-agent."}},
            "hooks": {"PostToolUse": [{}]},
        }),
    )


if __name__ == "__main__":
    unittest.main()
