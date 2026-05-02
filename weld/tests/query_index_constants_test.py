"""Regression: ``node_tokens`` must surface ``props.constants``.

Pairs with the file-index and python_module strategy tests. The
``wd query`` path narrows candidate nodes via the inverted index built
from ``node_tokens``. If module-level constants live only on
``props.constants`` (and not also in ``props.exports``), they must
still tokenize so the query path returns the file node that owns
them. This is the load-bearing acceptance for the dogfood gap:
``wd query _NAMED_REF_RE`` must return at least one match.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.query_index import build_index, node_tokens  # noqa: E402


def _file_node(constants: list[str]) -> dict:
    return {
        "type": "file",
        "label": "agent_graph_metadata",
        "props": {
            "file": "weld/agent_graph_metadata.py",
            "exports": [],
            "constants": constants,
        },
    }


class QueryIndexConstantsTest(unittest.TestCase):
    """``node_tokens`` must include ``props.constants`` lowercased."""

    def test_constant_tokens_present(self) -> None:
        """A constant like ``_NAMED_REF_RE`` must produce a lowercased
        token in the returned token list.
        """
        tokens = set(node_tokens(
            "file:agent_graph_metadata", _file_node(["_NAMED_REF_RE"]),
        ))
        self.assertIn("_named_ref_re", tokens)

    def test_constants_indexed_for_query(self) -> None:
        """``build_index`` must put the node behind every constant token
        -- otherwise ``wd query <CONST>`` cannot find it.
        """
        node = _file_node(["_NAMED_REF_RE", "PUBLIC_CONST"])
        nodes = {"file:agent_graph_metadata": node}
        index = build_index(nodes)
        # _split_field translates underscore to whitespace and splits.
        # We expect the full lowercase form AND each split part to be
        # indexed back to this node.
        self.assertIn("_named_ref_re", index)
        self.assertIn("file:agent_graph_metadata", index["_named_ref_re"])
        self.assertIn("public_const", index)

    def test_non_string_constants_skipped(self) -> None:
        """Defensive: a malformed graph with non-string entries must
        not raise -- only string entries should be tokenized.
        """
        node = _file_node([])
        node["props"]["constants"] = ["GOOD", 42, None, "ALSO_GOOD"]
        tokens = set(node_tokens("file:x", node))
        self.assertIn("good", tokens)
        self.assertIn("also_good", tokens)


if __name__ == "__main__":
    unittest.main()
