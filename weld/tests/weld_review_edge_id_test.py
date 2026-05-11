"""Edge ID mint determinism (ADR 0055).

Per ADR 0055, the review queue mints a stable 16-character edge id from
``sha1(from + "\x00" + to + "\x00" + type + "\x00" + props.source_strategy)``.
The id is stable across runs so the review-state file can address edges by id
even when the underlying edge index reshuffles. This test pins the contract.
"""
from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld._review import mint_edge_id  # noqa: E402


class MintEdgeIdTest(unittest.TestCase):
    """The id depends on (from, to, type, source_strategy) and nothing else."""

    def test_id_is_sixteen_hex_chars(self) -> None:
        edge = {
            "from": "a", "to": "b", "type": "calls",
            "props": {"source_strategy": "python_callgraph"},
        }
        eid = mint_edge_id(edge)
        self.assertEqual(len(eid), 16)
        self.assertTrue(all(c in "0123456789abcdef" for c in eid))

    def test_id_is_stable_across_calls(self) -> None:
        edge = {
            "from": "a", "to": "b", "type": "calls",
            "props": {"source_strategy": "python_callgraph"},
        }
        self.assertEqual(mint_edge_id(edge), mint_edge_id(edge))

    def test_id_matches_sha1_prefix(self) -> None:
        edge = {
            "from": "a", "to": "b", "type": "calls",
            "props": {"source_strategy": "py"},
        }
        key = b"a\x00b\x00calls\x00py"
        expected = hashlib.sha1(key, usedforsecurity=False).hexdigest()[:16]
        self.assertEqual(mint_edge_id(edge), expected)

    def test_id_differs_when_source_strategy_differs(self) -> None:
        e1 = {
            "from": "a", "to": "b", "type": "calls",
            "props": {"source_strategy": "py"},
        }
        e2 = {
            "from": "a", "to": "b", "type": "calls",
            "props": {"source_strategy": "go"},
        }
        self.assertNotEqual(mint_edge_id(e1), mint_edge_id(e2))

    def test_id_treats_missing_source_strategy_as_empty(self) -> None:
        edge = {"from": "a", "to": "b", "type": "calls", "props": {}}
        eid = mint_edge_id(edge)
        self.assertEqual(len(eid), 16)
        # Two edges that both miss source_strategy collide on id; the
        # mint is deterministic, so the second call returns the same id.
        self.assertEqual(eid, mint_edge_id(edge))

    def test_id_ignores_other_props(self) -> None:
        """Changing provenance or confidence does not alter the id."""
        e1 = {
            "from": "a", "to": "b", "type": "calls",
            "props": {
                "source_strategy": "py",
                "confidence": "speculative",
                "provenance": {"model": "old"},
            },
        }
        e2 = {
            "from": "a", "to": "b", "type": "calls",
            "props": {
                "source_strategy": "py",
                "confidence": "definite",
                "provenance": {"model": "new"},
            },
        }
        self.assertEqual(mint_edge_id(e1), mint_edge_id(e2))


if __name__ == "__main__":
    unittest.main()
