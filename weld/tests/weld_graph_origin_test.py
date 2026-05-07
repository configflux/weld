"""Unit tests for ``classify_node`` (ADR 0042).

Covers ``weld._graph_origin.classify_node``:

- Modern path: ``props.origin`` set to each of the four allowed values
  is returned verbatim; an invalid value falls through.
- Legacy fallback: every branch of the ADR 0042 pseudocode, in order
  (sentinel ID prefix, ``props.resolved=False``, ``builtin`` edge,
  ``stdlib`` edge, ``authority="external"``, default ``project``).
- Determinism: missing props, ``None`` vs empty edges, multi-edge
  any-match semantics, and the explicit-field-wins precedence.
- Type-hint sanity: ``ORIGINS`` is exhaustive.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld._graph_origin import ORIGINS, classify_node  # noqa: E402


class ClassifyNodeExplicitOriginTest(unittest.TestCase):
    """``props.origin`` is read directly when set to a valid value."""

    def test_explicit_project(self) -> None:
        self.assertEqual(
            classify_node({"props": {"origin": "project"}}), "project"
        )

    def test_explicit_stdlib(self) -> None:
        self.assertEqual(
            classify_node({"props": {"origin": "stdlib"}}), "stdlib"
        )

    def test_explicit_external(self) -> None:
        self.assertEqual(
            classify_node({"props": {"origin": "external"}}), "external"
        )

    def test_explicit_unresolved(self) -> None:
        self.assertEqual(
            classify_node({"props": {"origin": "unresolved"}}), "unresolved"
        )

    def test_invalid_origin_falls_through_to_derivation(self) -> None:
        # A malformed origin value must not crash; the derivation path
        # then classifies the node by other signals.
        node = {"props": {"origin": "weird", "authority": "external"}}
        self.assertEqual(classify_node(node), "external")

    def test_explicit_origin_wins_over_legacy_signals(self) -> None:
        # An unresolved-prefix node that the strategy was confident
        # enough to tag as project keeps the explicit tag.
        node = {
            "id": "symbol:unresolved:foo",
            "props": {"origin": "project", "resolved": False},
        }
        self.assertEqual(classify_node(node), "project")


class ClassifyNodeLegacyFallbackTest(unittest.TestCase):
    """Legacy graphs without ``props.origin`` derive from existing signals."""

    def test_unresolved_id_prefix(self) -> None:
        node = {"id": "symbol:unresolved:print", "props": {}}
        self.assertEqual(classify_node(node), "unresolved")

    def test_resolved_false_marks_unresolved(self) -> None:
        node = {"id": "symbol:python:foo", "props": {"resolved": False}}
        self.assertEqual(classify_node(node), "unresolved")

    def test_incoming_edge_builtin_marks_stdlib(self) -> None:
        node = {"id": "symbol:python:print", "props": {}}
        edges = [{"props": {"resolution": "builtin"}}]
        self.assertEqual(
            classify_node(node, incoming_edges=edges), "stdlib"
        )

    def test_incoming_edge_stdlib_marks_stdlib(self) -> None:
        node = {"id": "symbol:python:os.path.join", "props": {}}
        edges = [{"props": {"resolution": "stdlib"}}]
        self.assertEqual(
            classify_node(node, incoming_edges=edges), "stdlib"
        )

    def test_authority_external_marks_external(self) -> None:
        node = {
            "id": "symbol:python:numpy.array",
            "props": {"authority": "external"},
        }
        self.assertEqual(classify_node(node), "external")

    def test_bare_project_node_defaults_to_project(self) -> None:
        node = {
            "id": "symbol:python:weld.cli.main",
            "props": {"authority": "canonical"},
        }
        self.assertEqual(classify_node(node), "project")


class ClassifyNodeEdgeCasesTest(unittest.TestCase):
    """Determinism on missing fields, ``None`` edges, and multi-edge inputs."""

    def test_missing_props_dict_defaults_to_project(self) -> None:
        # No props at all must not crash; the node is treated as a
        # bare project node by the fallback.
        self.assertEqual(classify_node({"id": "symbol:python:foo"}), "project")

    def test_incoming_edges_none_skips_edge_inspection(self) -> None:
        # Passing ``None`` (the default) must not iterate edges.
        node = {"id": "symbol:python:foo", "props": {}}
        self.assertEqual(classify_node(node, incoming_edges=None), "project")

    def test_incoming_edges_empty_list_behaves_like_none(self) -> None:
        node = {"id": "symbol:python:foo", "props": {}}
        self.assertEqual(classify_node(node, incoming_edges=[]), "project")

    def test_any_incoming_builtin_edge_wins(self) -> None:
        # The first edge is unresolved but a later one is builtin; the
        # any-match semantics promote the node to stdlib.
        node = {"id": "symbol:python:print", "props": {}}
        edges = [
            {"props": {"resolution": "unresolved"}},
            {"props": {"resolution": "builtin"}},
        ]
        self.assertEqual(
            classify_node(node, incoming_edges=edges), "stdlib"
        )

    def test_edge_without_props_is_ignored(self) -> None:
        # A malformed edge (no ``props`` dict) must not crash.
        node = {"id": "symbol:python:foo", "props": {}}
        edges = [{}, {"props": {"resolution": "builtin"}}]
        self.assertEqual(
            classify_node(node, incoming_edges=edges), "stdlib"
        )


class OriginsConstantTest(unittest.TestCase):
    """``ORIGINS`` is the exhaustive tuple of allowed values."""

    def test_origins_exhaustive(self) -> None:
        # If a fifth origin lands without amending ADR 0042 and this
        # test, the contract has drifted.
        self.assertEqual(
            set(ORIGINS), {"project", "stdlib", "external", "unresolved"}
        )

    def test_origins_has_no_duplicates(self) -> None:
        self.assertEqual(len(ORIGINS), len(set(ORIGINS)))


if __name__ == "__main__":
    unittest.main()
