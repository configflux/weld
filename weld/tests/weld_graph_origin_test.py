"""Unit tests for ``classify_node`` (ADR 0042).

Covers ``weld._graph_origin.classify_node`` after the Phase-7 removal
of the transitional legacy-graph derivation:

- Modern path: ``props.origin`` set to each of the four allowed values
  is returned verbatim.
- Missing-or-invalid path: when ``props.origin`` is absent or carries a
  value outside the four-element vocabulary, the function returns
  ``"unresolved"`` deterministically. This is the safe answer when
  provenance cannot be established at the graph layer -- it surfaces
  the gap to downstream consumers (viz, ranking, brief) instead of
  silently inventing a category.
- Type-hint sanity: ``ORIGINS`` is exhaustive.
"""

from __future__ import annotations

import unittest


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

    def test_explicit_origin_overrides_other_signals(self) -> None:
        # An unresolved-prefix node that the strategy was confident
        # enough to tag as project keeps the explicit tag.
        node = {
            "id": "symbol:unresolved:foo",
            "props": {"origin": "project", "resolved": False},
        }
        self.assertEqual(classify_node(node), "project")


class ClassifyNodeMissingOrInvalidOriginTest(unittest.TestCase):
    """Without a valid ``props.origin`` tag the answer is deterministic.

    Phase 7 of the origin-taxonomy plan removed the legacy-graph
    derivation. A node that reaches this function without
    ``props.origin`` (or with a value outside :data:`ORIGINS`) is the
    symptom of a strategy that has not yet shipped origin tagging or
    of a hand-crafted graph snapshot; in both cases the safe answer is
    ``"unresolved"`` because we cannot establish provenance and must
    not invent one.
    """

    def test_missing_origin_returns_unresolved(self) -> None:
        node = {"id": "symbol:python:foo", "props": {}}
        self.assertEqual(classify_node(node), "unresolved")

    def test_missing_props_dict_returns_unresolved(self) -> None:
        # No props at all must not crash; the node has no tag, so the
        # safe answer is unresolved.
        self.assertEqual(
            classify_node({"id": "symbol:python:foo"}), "unresolved"
        )

    def test_invalid_origin_returns_unresolved(self) -> None:
        # A malformed origin value (typo, drift from a future taxonomy
        # entry, hand-edited graph) must not crash and must not pass
        # through. The contract is closed-vocabulary.
        node = {"props": {"origin": "weird", "authority": "external"}}
        self.assertEqual(classify_node(node), "unresolved")

    def test_non_string_origin_returns_unresolved(self) -> None:
        # ``props.origin`` is typed as a string in the schema; an int /
        # None / dict slipped in by a buggy emitter still falls through
        # to the deterministic default.
        for bad in (None, 7, ["project"], {"value": "project"}):
            with self.subTest(origin=bad):
                self.assertEqual(
                    classify_node({"props": {"origin": bad}}), "unresolved"
                )

    def test_authority_external_no_origin_returns_unresolved(self) -> None:
        # Pre-Phase-7 the legacy fallback would have derived this to
        # "external" from authority. Post-Phase-7 the absence of an
        # explicit origin tag is the signal, and we no longer guess.
        node = {
            "id": "symbol:python:numpy.array",
            "props": {"authority": "external"},
        }
        self.assertEqual(classify_node(node), "unresolved")


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
