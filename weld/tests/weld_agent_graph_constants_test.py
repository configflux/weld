"""Pin the canonical content of shared agent-graph audit constants.

`weld._agent_graph_constants` is the single source of truth for the
description-vagueness rule used by both `weld.agent_graph_audit` and
`weld._agent_graph_strict` (which sits on the inverse side of the same
ADR 0029 suppression). Drift between the two callers used to mean two
identical literal blocks; consolidating into one module removes that
drift surface, and this test pins the exact content so that future
edits are deliberate.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld import _agent_graph_constants as constants  # noqa: E402
from weld import _agent_graph_strict as strict_mod  # noqa: E402
from weld import agent_graph_audit as audit_mod  # noqa: E402


class AgentGraphConstantsTest(unittest.TestCase):
    """Pin the exact content and identity of the shared constants."""

    def test_clear_description_types_canonical_content(self) -> None:
        self.assertEqual(
            constants._CLEAR_DESCRIPTION_TYPES,
            {"agent", "skill", "subagent"},
        )

    def test_vague_descriptions_canonical_content(self) -> None:
        self.assertEqual(
            constants._VAGUE_DESCRIPTIONS,
            {"content", "todo", "tbd", "misc", "general", "helper"},
        )

    def test_audit_module_reuses_shared_constants(self) -> None:
        """`agent_graph_audit` must import the shared constants, not
        define its own copies. Identity check catches accidental
        re-introduction of a duplicate literal."""
        self.assertIs(
            audit_mod._CLEAR_DESCRIPTION_TYPES,
            constants._CLEAR_DESCRIPTION_TYPES,
        )
        self.assertIs(
            audit_mod._VAGUE_DESCRIPTIONS,
            constants._VAGUE_DESCRIPTIONS,
        )

    def test_strict_module_reuses_shared_constants(self) -> None:
        """`_agent_graph_strict` must import the shared constants, not
        define its own copies. Identity check catches accidental
        re-introduction of a duplicate literal."""
        self.assertIs(
            strict_mod._CLEAR_DESCRIPTION_TYPES,
            constants._CLEAR_DESCRIPTION_TYPES,
        )
        self.assertIs(
            strict_mod._VAGUE_DESCRIPTIONS,
            constants._VAGUE_DESCRIPTIONS,
        )


if __name__ == "__main__":
    unittest.main()
