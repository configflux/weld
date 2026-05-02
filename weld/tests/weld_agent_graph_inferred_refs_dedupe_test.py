"""Dedupe-collision contract tests for definite vs inferred references.

Locks in the contract for what happens when the typed-prefix
`agent:reviewer` form (`_NAMED_REF_RE`, confidence=definite) and the
inferred-confidence body form `subagent_type: "reviewer"`
(`extract_inferred_references`) appear in the SAME asset and target the
same `(target_type, target_name, edge_type)` tuple.

Contract under test (see bd 6lfh, follow-up from q8rl 4-eye review):
  * Exactly one edge is emitted per dedupe key.
  * The definite-confidence reference always wins -- both for the
    typed-prefix `_NAMED_REF_RE` form and for frontmatter
    `weld.invokes_agents: [...]` declarations.
  * Outcome is order-independent: the typed-prefix wins regardless of
    whether it appears before or after the inferred form in the body.
    `_text_references` runs the `_NAMED_REF_RE` loop across all body
    lines BEFORE invoking `extract_inferred_references`, so the definite
    reference is always inserted into the dedupe stream first.
  * Provenance (`raw`) on the surviving edge points at the
    typed-prefix occurrence, not the inferred one.

These tests exercise the contract through `parse_agent_asset`; they do
NOT touch the dedupe logic itself.

Split from weld_agent_graph_inferred_refs_test.py (the parent file is
already near the 400-line cap after ukk8). Cohesion: every test here
exercises cross-pattern dedupe collisions for the same target.
"""

from __future__ import annotations

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.agent_graph_metadata import parse_agent_asset  # noqa: E402
from weld.agent_graph_metadata_utils import AgentGraphReference  # noqa: E402


def _write(root: Path, rel_path: str, text: str) -> None:
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _reviewer_invokes(refs: tuple[AgentGraphReference, ...]) -> list[AgentGraphReference]:
    return [
        r for r in refs
        if r.target_type == "agent"
        and r.target_name == "reviewer"
        and r.edge_type == "invokes_agent"
    ]


class DedupeCollisionContractTest(unittest.TestCase):
    """Definite (typed-prefix / frontmatter) vs inferred (body regex) collisions."""

    def test_definite_first_then_inferred_keeps_definite(self) -> None:
        """Body order: typed-prefix BEFORE subagent_type. Definite wins."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(
                root,
                ".claude/agents/orchestrator.md",
                textwrap.dedent(
                    """\
                    ---
                    name: orchestrator
                    ---

                    First mention: agent:reviewer in the body.
                    Later: subagent_type: "reviewer" appears too.
                    """
                ),
            )
            asset = parse_agent_asset(
                root, ".claude/agents/orchestrator.md", "agent", "claude",
            )
        matches = _reviewer_invokes(asset.references)
        self.assertEqual(len(matches), 1, matches)
        self.assertEqual(matches[0].confidence, "definite")
        # Provenance must point at the typed-prefix occurrence -- the raw
        # capture from `_NAMED_REF_RE` is exactly "agent:reviewer".
        self.assertIn("agent:reviewer", matches[0].raw)
        self.assertNotIn("subagent_type", matches[0].raw)

    def test_inferred_first_then_definite_still_keeps_definite(self) -> None:
        """Body order: subagent_type BEFORE typed-prefix. Order independent."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(
                root,
                ".claude/agents/orchestrator.md",
                textwrap.dedent(
                    """\
                    ---
                    name: orchestrator
                    ---

                    First mention: subagent_type: "reviewer" earlier.
                    Later: see agent:reviewer near the bottom.
                    """
                ),
            )
            asset = parse_agent_asset(
                root, ".claude/agents/orchestrator.md", "agent", "claude",
            )
        matches = _reviewer_invokes(asset.references)
        self.assertEqual(len(matches), 1, matches)
        self.assertEqual(matches[0].confidence, "definite")
        # The definite typed-prefix wins regardless of textual order.
        self.assertIn("agent:reviewer", matches[0].raw)
        self.assertNotIn("subagent_type", matches[0].raw)

    def test_frontmatter_invokes_agents_beats_body_subagent_type(self) -> None:
        """Frontmatter `weld.invokes_agents: [reviewer]` (definite) wins over
        body `subagent_type: "reviewer"` (inferred)."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(
                root,
                ".claude/agents/orchestrator.md",
                textwrap.dedent(
                    """\
                    ---
                    name: orchestrator
                    weld:
                      invokes_agents:
                        - reviewer
                    ---

                    Body also says: subagent_type: "reviewer".
                    """
                ),
            )
            asset = parse_agent_asset(
                root, ".claude/agents/orchestrator.md", "agent", "claude",
            )
        matches = _reviewer_invokes(asset.references)
        self.assertEqual(len(matches), 1, matches)
        self.assertEqual(matches[0].confidence, "definite")
        # Frontmatter-emitted reference uses the bare item string ("reviewer")
        # as raw, not the body subagent_type capture.
        self.assertEqual(matches[0].raw, "reviewer")
        self.assertEqual(matches[0].line, 1)


if __name__ == "__main__":
    unittest.main()
