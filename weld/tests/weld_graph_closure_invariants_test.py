"""Tests for ``weld._graph_closure_invariants`` (ADR 0041 Layer 3).

Each test exercises one of the three new structural lint rules:
``canonical-id-uniqueness``, ``file-anchor-symmetry``, and
``strategy-pair-consistency``. Tests pass node-dict / graph-dict fixtures
directly to the rule functions so they remain independent of the
``weld.arch_lint`` runner machinery -- the runner integration is covered
by ``weld_arch_lint_test`` once the rules are wired in.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)


# ---------------------------------------------------------------------------
# Rule 1: canonical-id-uniqueness
# ---------------------------------------------------------------------------


class CanonicalIdUniquenessTest(unittest.TestCase):
    """Two non-aliased nodes that share a canonical base must violate."""

    def test_violates_when_two_nodes_share_canonical_base(self) -> None:
        from weld._graph_closure_invariants import (
            check_canonical_id_uniqueness,
        )

        # Both IDs slug to the same ``skill:generic:architecture-decision``
        # canonical base; neither lists the other in ``aliases``.
        nodes = {
            "skill:generic:architecture-decision": {
                "type": "skill",
                "label": "architecture-decision",
                "props": {"aliases": []},
            },
            "skill:generic:architecture--decision": {
                "type": "skill",
                "label": "architecture-decision",
                "props": {"aliases": []},
            },
        }
        violations = list(check_canonical_id_uniqueness(nodes))
        # One violation per colliding node so editors can navigate to
        # both halves; both must reference the canonical base in the
        # message and carry the rule id.
        self.assertEqual(len(violations), 2)
        ids = sorted(v.node_id for v in violations)
        self.assertEqual(
            ids,
            [
                "skill:generic:architecture--decision",
                "skill:generic:architecture-decision",
            ],
        )
        for v in violations:
            self.assertEqual(v.rule, "canonical-id-uniqueness")
            self.assertIn("architecture-decision", v.message)

    def test_passes_when_one_node_lists_the_other_as_alias(self) -> None:
        from weld._graph_closure_invariants import (
            check_canonical_id_uniqueness,
        )

        nodes = {
            "skill:generic:architecture-decision": {
                "type": "skill",
                "label": "architecture-decision",
                "props": {
                    "aliases": ["skill:generic:architecture-decision:abc12345"],
                },
            },
            "skill:generic:architecture-decision:abc12345": {
                "type": "skill",
                "label": "architecture-decision",
                "props": {"aliases": []},
            },
        }
        # The two share a canonical base BUT one lists the other as
        # an alias, so they merge logically and the rule passes.
        violations = list(check_canonical_id_uniqueness(nodes))
        self.assertEqual(violations, [])

    def test_passes_when_canonical_bases_differ(self) -> None:
        from weld._graph_closure_invariants import (
            check_canonical_id_uniqueness,
        )

        nodes = {
            "skill:generic:foo": {
                "type": "skill", "label": "foo", "props": {"aliases": []},
            },
            "skill:generic:bar": {
                "type": "skill", "label": "bar", "props": {"aliases": []},
            },
        }
        violations = list(check_canonical_id_uniqueness(nodes))
        self.assertEqual(violations, [])

    def test_violation_is_deterministic_across_calls(self) -> None:
        from weld._graph_closure_invariants import (
            check_canonical_id_uniqueness,
        )

        nodes = {
            "skill:generic:architecture-decision": {
                "type": "skill",
                "label": "architecture-decision",
                "props": {"aliases": []},
            },
            "skill:generic:architecture--decision": {
                "type": "skill",
                "label": "architecture-decision",
                "props": {"aliases": []},
            },
        }
        a = [v.to_dict() for v in check_canonical_id_uniqueness(nodes)]
        b = [v.to_dict() for v in check_canonical_id_uniqueness(nodes)]
        self.assertEqual(a, b)


# ---------------------------------------------------------------------------
# Rule 2: file-anchor-symmetry
# ---------------------------------------------------------------------------


def _file_with_outgoing_only_graph() -> dict:
    """A ``file:`` node with one outgoing ``contains`` edge and no inbound."""
    return {
        "nodes": {
            "file:weld/strategies/_ros2_py": {
                "type": "file",
                "label": "_ros2_py",
                "props": {"file": "weld/strategies/_ros2_py.py"},
            },
            "symbol:py:weld.strategies._ros2_py:foo": {
                "type": "symbol",
                "label": "foo",
                "props": {},
            },
        },
        "edges": [
            {
                "from": "file:weld/strategies/_ros2_py",
                "to": "symbol:py:weld.strategies._ros2_py:foo",
                "type": "contains",
                "props": {},
            },
        ],
    }


class FileAnchorSymmetryTest(unittest.TestCase):
    """``file:`` anchors with outgoing children must have an inbound edge or
    a documented exception."""

    def test_violates_when_file_has_outgoing_contains_only(self) -> None:
        from weld._graph_closure_invariants import (
            check_file_anchor_symmetry,
        )

        graph = _file_with_outgoing_only_graph()
        violations = list(check_file_anchor_symmetry(graph))
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].rule, "file-anchor-symmetry")
        self.assertEqual(
            violations[0].node_id, "file:weld/strategies/_ros2_py"
        )

    def test_passes_when_file_has_inbound_edge(self) -> None:
        from weld._graph_closure_invariants import (
            check_file_anchor_symmetry,
        )

        graph = _file_with_outgoing_only_graph()
        graph["edges"].append(
            {
                "from": "package:python:weld.strategies",
                "to": "file:weld/strategies/_ros2_py",
                "type": "contains",
                "props": {},
            }
        )
        graph["nodes"]["package:python:weld.strategies"] = {
            "type": "package", "label": "weld.strategies", "props": {},
        }
        self.assertEqual(
            list(check_file_anchor_symmetry(graph)), [],
        )

    def test_passes_when_role_is_entrypoint(self) -> None:
        from weld._graph_closure_invariants import (
            check_file_anchor_symmetry,
        )

        graph = _file_with_outgoing_only_graph()
        graph["nodes"]["file:weld/strategies/_ros2_py"]["props"]["roles"] = [
            "entrypoint"
        ]
        self.assertEqual(
            list(check_file_anchor_symmetry(graph)), [],
        )

    def test_passes_for_builtin_entrypoint_basenames(self) -> None:
        from weld._graph_closure_invariants import (
            check_file_anchor_symmetry,
        )

        # Vary the file path to one that the basename allow-list covers.
        for path, fid in (
            ("pkg/__main__.py", "file:pkg/__main__"),
            ("pkg/cli.py", "file:pkg/cli"),
            ("pkg/foo_cli.py", "file:pkg/foo_cli"),
        ):
            graph = {
                "nodes": {
                    fid: {
                        "type": "file",
                        "label": fid.rsplit("/", 1)[-1],
                        "props": {"file": path},
                    },
                    "symbol:py:bar": {
                        "type": "symbol", "label": "bar", "props": {},
                    },
                },
                "edges": [
                    {
                        "from": fid,
                        "to": "symbol:py:bar",
                        "type": "contains",
                        "props": {},
                    }
                ],
            }
            self.assertEqual(
                list(check_file_anchor_symmetry(graph)), [],
                msg=f"basename {path!r} should be allow-listed",
            )

    def test_passes_when_path_in_repo_allowlist(self) -> None:
        from weld._graph_closure_invariants import (
            check_file_anchor_symmetry,
        )

        graph = _file_with_outgoing_only_graph()
        allowlist = [
            {
                "path": "weld/strategies/_ros2_py.py",
                "reason": "lazy-import blind spot",
            }
        ]
        self.assertEqual(
            list(check_file_anchor_symmetry(graph, allowlist=allowlist)),
            [],
        )

    def test_passes_when_file_has_no_outgoing_contains(self) -> None:
        from weld._graph_closure_invariants import (
            check_file_anchor_symmetry,
        )

        # Pure leaf file with no children -- not in scope for this rule.
        graph = {
            "nodes": {
                "file:pkg/leaf": {
                    "type": "file", "label": "leaf",
                    "props": {"file": "pkg/leaf.py"},
                },
            },
            "edges": [],
        }
        self.assertEqual(list(check_file_anchor_symmetry(graph)), [])

    def test_violations_are_byte_deterministic(self) -> None:
        from weld._graph_closure_invariants import (
            check_file_anchor_symmetry,
        )

        graph = _file_with_outgoing_only_graph()
        a = [v.to_dict() for v in check_file_anchor_symmetry(graph)]
        b = [v.to_dict() for v in check_file_anchor_symmetry(graph)]
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
