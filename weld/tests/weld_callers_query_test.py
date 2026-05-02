"""Tests for ``wd callers`` and ``wd references`` graph queries.

``weld/docs/adr/0004-call-graph-schema-extension.md``.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.contract import SCHEMA_VERSION  # noqa: E402
from weld.graph import Graph  # noqa: E402

def _make_fixture_root() -> Path:
    """Write a small fixture graph to a temp dir and return its root."""
    nodes: dict[str, dict] = {
        "symbol:py:m:helper": {
            "type": "symbol",
            "label": "helper",
            "props": {
                "module": "m",
                "qualname": "helper",
                "language": "python",
            },
        },
        "symbol:py:m:caller_one": {
            "type": "symbol",
            "label": "caller_one",
            "props": {
                "module": "m",
                "qualname": "caller_one",
                "language": "python",
            },
        },
        "symbol:py:m:caller_two": {
            "type": "symbol",
            "label": "caller_two",
            "props": {
                "module": "m",
                "qualname": "caller_two",
                "language": "python",
            },
        },
        "symbol:py:m:top": {
            "type": "symbol",
            "label": "top",
            "props": {
                "module": "m",
                "qualname": "top",
                "language": "python",
            },
        },
        "symbol:unresolved:helper": {
            "type": "symbol",
            "label": "helper",
            "props": {
                "qualname": "helper",
                "language": "python",
                "resolved": False,
            },
        },
    }
    edges: list[dict] = [
        # Direct callers
        {
            "from": "symbol:py:m:caller_one",
            "to": "symbol:py:m:helper",
            "type": "calls",
            "props": {"resolved": True},
        },
        {
            "from": "symbol:py:m:caller_two",
            "to": "symbol:py:m:helper",
            "type": "calls",
            "props": {"resolved": True},
        },
        # Transitive caller (top -> caller_one -> helper)
        {
            "from": "symbol:py:m:top",
            "to": "symbol:py:m:caller_one",
            "type": "calls",
            "props": {"resolved": True},
        },
        # Unresolved sentinel reference (used by references())
        {
            "from": "symbol:py:m:top",
            "to": "symbol:unresolved:helper",
            "type": "calls",
            "props": {"resolved": False},
        },
    ]
    tmp = Path(tempfile.mkdtemp())
    (tmp / ".weld").mkdir(parents=True, exist_ok=True)
    (tmp / ".weld" / "graph.json").write_text(
        json.dumps(
            {
                "meta": {
                    "version": SCHEMA_VERSION,
                    "updated_at": "2026-04-06T00:00:00+00:00",
                },
                "nodes": nodes,
                "edges": edges,
            }
        ),
        encoding="utf-8",
    )
    (tmp / ".weld" / "file-index.json").write_text(
        json.dumps(
            {
                "meta": {"version": 1},
                "files": {
                    "m.py": ["helper", "caller_one", "caller_two", "top"],
                },
            }
        ),
        encoding="utf-8",
    )
    return tmp

class CallersQueryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = _make_fixture_root()
        self.g = Graph(self.root)
        self.g.load()

    def test_direct_callers(self) -> None:
        result = self.g.callers("symbol:py:m:helper", depth=1)
        ids = {c["id"] for c in result["callers"]}
        self.assertEqual(
            ids,
            {"symbol:py:m:caller_one", "symbol:py:m:caller_two"},
        )
        self.assertEqual(result["depth"], 1)
        self.assertEqual(result["symbol"], "symbol:py:m:helper")

    def test_transitive_callers_depth_two(self) -> None:
        result = self.g.callers("symbol:py:m:helper", depth=2)
        ids = {c["id"] for c in result["callers"]}
        # depth 2 must reach `top` via caller_one
        self.assertIn("symbol:py:m:caller_one", ids)
        self.assertIn("symbol:py:m:caller_two", ids)
        self.assertIn("symbol:py:m:top", ids)

    def test_callers_unknown_symbol(self) -> None:
        result = self.g.callers("symbol:py:m:nope")
        self.assertEqual(result["callers"], [])
        self.assertIn("error", result)

    def test_callers_resolves_bare_name(self) -> None:
        """A bare name like ``helper`` must resolve the same way
        :meth:`Graph.references` resolves it: walk symbol nodes, match on
        ``qualname``, and aggregate callers across every match.
        """
        # Bare name resolves to symbol:py:m:helper plus the unresolved
        # sentinel symbol:unresolved:helper. Aggregated callers must
        # include both direct callers (caller_one, caller_two) AND
        # ``top`` (which calls the unresolved sentinel).
        result = self.g.callers("helper", depth=1)
        ids = {c["id"] for c in result["callers"]}
        self.assertIn("symbol:py:m:caller_one", ids)
        self.assertIn("symbol:py:m:caller_two", ids)
        self.assertIn("symbol:py:m:top", ids)
        self.assertNotIn("error", result)

    def test_callers_bare_name_unique(self) -> None:
        """Bare name that uniquely resolves to one definite symbol returns
        that symbol's callers (no aggregation surprise)."""
        # ``caller_one`` resolves to symbol:py:m:caller_one only; its
        # caller is ``top``.
        result = self.g.callers("caller_one", depth=1)
        ids = {c["id"] for c in result["callers"]}
        self.assertEqual(ids, {"symbol:py:m:top"})
        self.assertNotIn("error", result)

    def test_callers_bare_name_missing(self) -> None:
        """A bare name that matches no symbol still surfaces an error."""
        result = self.g.callers("does_not_exist", depth=1)
        self.assertEqual(result["callers"], [])
        self.assertIn("error", result)

    def test_callers_full_id_still_works(self) -> None:
        """Regression: fully-qualified ids must continue to resolve directly."""
        result = self.g.callers("symbol:py:m:helper", depth=1)
        ids = {c["id"] for c in result["callers"]}
        self.assertEqual(
            ids,
            {"symbol:py:m:caller_one", "symbol:py:m:caller_two"},
        )
        self.assertNotIn("error", result)

    def test_references_combines_resolved_and_sentinel(self) -> None:
        refs = self.g.references("helper")
        # Both the resolved symbol and the unresolved sentinel match
        match_ids = {m["id"] for m in refs["matches"]}
        self.assertIn("symbol:py:m:helper", match_ids)
        self.assertIn("symbol:unresolved:helper", match_ids)
        # Aggregated callers must include the direct callers and `top`
        # (which calls the unresolved sentinel directly).
        caller_ids = {c["id"] for c in refs["callers"]}
        self.assertIn("symbol:py:m:caller_one", caller_ids)
        self.assertIn("symbol:py:m:caller_two", caller_ids)
        self.assertIn("symbol:py:m:top", caller_ids)

if __name__ == "__main__":
    unittest.main()
