"""Slice-3 (a6) 5i8b cross-platform proof for implicit applies_to_path edges.

ADR 0021 Amendment 2: instruction files without explicit applyTo / globs /
path_globs default to repo-wide scope and emit an inferred applies_to_path
edge to a ``**`` scope node. Explicit declarations always win and suppress
the implicit edge.

The demo fixture under ``examples/agent-graph-demo/`` exercises three
platforms with implicit scope -- claude (CLAUDE.md), gemini (GEMINI.md),
codex (AGENTS.override.md) -- and one with explicit scope (the generic
AGENTS.md declares ``applyTo: ["**"]``).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_repo_root = Path(__file__).resolve().parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from weld.agent_graph_discovery import discover_agent_graph  # noqa: E402

_DEMO_ROOT = _repo_root / "examples" / "agent-graph-demo"


class AgentGraphImplicitScopeDemoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._graph = discover_agent_graph(
            _DEMO_ROOT, git_sha="demo", updated_at="2026-04-24T00:00:00+00:00",
        )

    def test_implicit_scope_instructions_emit_inferred_edges(self) -> None:
        confidence_by_source: dict[str, list[str]] = {}
        for edge in self._graph["edges"]:
            if edge.get("type") == "applies_to_path":
                confidence_by_source.setdefault(edge["from"], []).append(
                    edge["props"]["confidence"],
                )
        for source_id in (
            "instruction:claude:claude",
            "instruction:gemini:gemini",
            "instruction:codex:agents-override",
        ):
            self.assertEqual(
                confidence_by_source.get(source_id), ["inferred"], source_id,
            )

    def test_explicit_scope_instruction_keeps_definite_edge(self) -> None:
        confidence_by_source: dict[str, list[str]] = {}
        for edge in self._graph["edges"]:
            if edge.get("type") == "applies_to_path":
                confidence_by_source.setdefault(edge["from"], []).append(
                    edge["props"]["confidence"],
                )
        # Generic AGENTS.md has explicit applyTo: ["**"] -- one definite edge.
        self.assertEqual(
            confidence_by_source.get("instruction:generic:agents"), ["definite"],
        )


if __name__ == "__main__":
    unittest.main()
