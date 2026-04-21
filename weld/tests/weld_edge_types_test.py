"""Tests for the edge-type vocabulary extension (ADR 0016, bd-asu).

Covers:
- The eight new edge types are present in ``VALID_EDGE_TYPES``.
- Each type passes ``validate_edge`` with a minimal payload.
- Each type round-trips through ``Graph.add_edge`` into a graph whose
  dump passes ``validate_graph`` cleanly.
- ``wd add-edge --help`` exposes the canonical ``props.source``
  provenance example so the CLI guidance stays discoverable.
"""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

# Ensure weld package is importable from the repo root
_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.contract import (  # noqa: E402
    SCHEMA_VERSION,
    VALID_EDGE_TYPES,
    validate_edge,
    validate_graph,
)
from weld.graph import Graph  # noqa: E402

# The eight new edge types this task adds. Keep this list co-located with
# the test so that a regression (an accidental removal from the enum)
# fails here with a pointed diagnostic instead of somewhere downstream.
NEW_EDGE_TYPES = (
    "owned_by",
    "gates",
    "gated_by",
    "supersedes",
    "validates",
    "generates",
    "migrates",
    "contracts",
)


class NewEdgeTypesPresentTest(unittest.TestCase):
    """Each new label must appear in ``VALID_EDGE_TYPES``."""

    def test_every_new_type_is_registered(self) -> None:
        missing = [t for t in NEW_EDGE_TYPES if t not in VALID_EDGE_TYPES]
        self.assertEqual(
            missing, [],
            f"Expected these new edge types in VALID_EDGE_TYPES: {missing}",
        )

    def test_legacy_types_still_registered(self) -> None:
        # Spot-check a handful of pre-existing labels to make sure the
        # extension did not drop anything.
        for legacy in ("contains", "depends_on", "relates_to", "calls"):
            self.assertIn(legacy, VALID_EDGE_TYPES)

    def test_valid_edge_types_is_frozen(self) -> None:
        # The ADR commits to a strict frozenset. Guard against someone
        # switching to a mutable ``set`` and adding a ``--loose`` mode
        # later without updating the ADR.
        self.assertIsInstance(VALID_EDGE_TYPES, frozenset)


class ValidateEdgeAcceptsNewTypesTest(unittest.TestCase):
    """``validate_edge`` must accept each new label on a minimal edge."""

    def test_each_new_type_passes_validate_edge(self) -> None:
        node_ids = {"concept:a", "concept:b"}
        for edge_type in NEW_EDGE_TYPES:
            edge = {
                "from": "concept:a",
                "to": "concept:b",
                "type": edge_type,
                "props": {},
            }
            errors = validate_edge(edge, node_ids)
            self.assertEqual(
                errors, [],
                f"validate_edge rejected new type {edge_type!r}: {errors}",
            )


class RoundTripThroughGraphAddEdgeTest(unittest.TestCase):
    """Round-trip: Graph.add_edge -> dump -> validate_graph."""

    def test_each_new_type_round_trips(self) -> None:
        for edge_type in NEW_EDGE_TYPES:
            with self.subTest(edge_type=edge_type):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    graph = Graph(root)
                    # Seed two neutral nodes so the edge is not dangling.
                    graph.add_node("concept:a", "concept", "A", {})
                    graph.add_node("concept:b", "concept", "B", {})
                    # Stamp props.source so the provenance convention is
                    # actually exercised end-to-end, not just in help text.
                    edge = graph.add_edge(
                        "concept:a", "concept:b", edge_type,
                        {"source": "llm"},
                    )
                    self.assertEqual(edge["type"], edge_type)
                    self.assertEqual(edge["props"], {"source": "llm"})

                    dump = graph.dump()
                    # meta must carry the current schema version so the
                    # graph-level validator accepts the document.
                    dump.setdefault("meta", {})
                    dump["meta"]["version"] = SCHEMA_VERSION
                    dump["meta"].setdefault(
                        "updated_at", "2026-04-20T00:00:00+00:00",
                    )
                    errors = validate_graph(dump)
                    self.assertEqual(
                        errors, [],
                        f"validate_graph rejected round-tripped "
                        f"{edge_type!r} graph: {errors}",
                    )

                    # The edge is actually present in the dumped graph.
                    types = {e["type"] for e in dump["edges"]}
                    self.assertIn(edge_type, types)


class CliAddEdgeHelpExposesProvenanceExampleTest(unittest.TestCase):
    """``wd add-edge --help`` must surface the props.source example.

    The help text is the first place a tool author (LLM or human) sees
    the provenance convention. If this snapshot drifts, regenerate the
    help string in _graph_cli.py and update the ADR if the policy
    changes.
    """

    def _add_edge_help(self) -> str:
        from weld._graph_cli import main

        buf = io.StringIO()
        with redirect_stdout(buf):
            # argparse raises SystemExit(0) after printing --help.
            with self.assertRaises(SystemExit) as ctx:
                main(["add-edge", "--help"])
        self.assertEqual(ctx.exception.code, 0)
        return buf.getvalue()

    def test_help_mentions_source_llm_example(self) -> None:
        help_text = self._add_edge_help()
        self.assertIn('"source":"llm"', help_text)

    def test_help_mentions_props_source_convention(self) -> None:
        help_text = self._add_edge_help()
        # The help should point users at props.source as the
        # provenance mechanism, not at dropped 0.3.0 flags.
        self.assertIn("props.source", help_text)


if __name__ == "__main__":
    unittest.main()
