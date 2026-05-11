"""Tests for ``wd migrate --add-confidence`` backfill helper (ADR 0050).

Covers the in-process callable :func:`weld._graph_migrate.backfill_confidence`
and the CLI wiring exposed via ``wd migrate --add-confidence``.

The backfill semantics:

* For every edge whose ``props`` does not carry a ``confidence`` key,
  classify the edge by ``props.source_strategy`` against the static
  :data:`weld._confidence_defaults.STRATEGY_DEFAULT_CONFIDENCE` map.
  Strategies not in the map default to ``"speculative"``.
* Edges that already carry a valid ``confidence`` value are not
  touched. Edges with an *invalid* value are also left alone (the
  human operator must adjudicate; an automated rewrite would lose
  information).
* The function is idempotent: running it twice produces the same
  output as running it once.
* The function returns a structured report so the CLI wrapper can
  print "filled N edges; M unchanged; K invalid" without re-walking
  the edge list.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

# Ensure weld package is importable from the repo root
_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld._graph_migrate import (  # noqa: E402
    BackfillReport,
    backfill_confidence,
)
from weld.contract import SCHEMA_VERSION  # noqa: E402


_TS = "2026-05-10T12:00:00+00:00"


def _graph(edges: list[dict]) -> dict:
    return {
        "meta": {"version": SCHEMA_VERSION, "updated_at": _TS},
        "nodes": {
            "a": {"type": "service", "label": "A", "props": {}},
            "b": {"type": "service", "label": "B", "props": {}},
        },
        "edges": edges,
    }


class BackfillBasicTest(unittest.TestCase):

    def test_missing_confidence_filled_from_static_map(self) -> None:
        edges = [
            {
                "from": "a", "to": "b", "type": "depends_on",
                "props": {"source_strategy": "tree_sitter"},
            },
            {
                "from": "a", "to": "b", "type": "depends_on",
                "props": {"source_strategy": "test_peer"},
            },
            {
                "from": "a", "to": "b", "type": "depends_on",
                "props": {"source_strategy": "anthropic_enrichment"},
            },
        ]
        graph = _graph(edges)
        report = backfill_confidence(graph)
        self.assertIsInstance(report, BackfillReport)
        self.assertEqual(report.filled, 3)
        self.assertEqual(report.unchanged, 0)
        self.assertEqual(report.invalid, 0)
        out_edges = graph["edges"]
        self.assertEqual(out_edges[0]["props"]["confidence"], "definite")
        self.assertEqual(out_edges[1]["props"]["confidence"], "inferred")
        self.assertEqual(out_edges[2]["props"]["confidence"], "speculative")

    def test_missing_strategy_defaults_to_speculative(self) -> None:
        edges = [
            {"from": "a", "to": "b", "type": "depends_on", "props": {}},
        ]
        graph = _graph(edges)
        report = backfill_confidence(graph)
        self.assertEqual(report.filled, 1)
        self.assertEqual(
            graph["edges"][0]["props"]["confidence"], "speculative",
            "An edge with no source_strategy at all must default to "
            "speculative -- the producer has not declared a stance",
        )

    def test_unknown_strategy_defaults_to_speculative(self) -> None:
        edges = [
            {
                "from": "a", "to": "b", "type": "depends_on",
                "props": {"source_strategy": "imaginary_strategy_v9"},
            },
        ]
        graph = _graph(edges)
        report = backfill_confidence(graph)
        self.assertEqual(report.filled, 1)
        self.assertEqual(
            graph["edges"][0]["props"]["confidence"], "speculative",
        )

    def test_existing_valid_confidence_left_unchanged(self) -> None:
        edges = [
            {
                "from": "a", "to": "b", "type": "depends_on",
                "props": {
                    "source_strategy": "tree_sitter",
                    "confidence": "speculative",
                },
            },
        ]
        graph = _graph(edges)
        report = backfill_confidence(graph)
        self.assertEqual(report.filled, 0)
        self.assertEqual(report.unchanged, 1)
        self.assertEqual(
            graph["edges"][0]["props"]["confidence"], "speculative",
            "A pre-existing valid confidence value must not be "
            "overwritten -- the producing strategy made an explicit "
            "choice and the migration helper must respect it",
        )

    def test_invalid_confidence_left_alone_and_counted(self) -> None:
        edges = [
            {
                "from": "a", "to": "b", "type": "depends_on",
                "props": {
                    "source_strategy": "tree_sitter",
                    "confidence": "maybe",
                },
            },
        ]
        graph = _graph(edges)
        report = backfill_confidence(graph)
        self.assertEqual(report.filled, 0)
        self.assertEqual(report.invalid, 1)
        # The invalid value is preserved because an automated rewrite
        # would silently lose whatever the human / strategy intended.
        self.assertEqual(graph["edges"][0]["props"]["confidence"], "maybe")

    def test_idempotent(self) -> None:
        edges = [
            {
                "from": "a", "to": "b", "type": "depends_on",
                "props": {"source_strategy": "tree_sitter"},
            },
        ]
        graph = _graph(edges)
        first = backfill_confidence(graph)
        second = backfill_confidence(graph)
        self.assertEqual(first.filled, 1)
        # Second pass: nothing left to fill.
        self.assertEqual(second.filled, 0)
        self.assertEqual(second.unchanged, 1)

    def test_non_dict_props_treated_as_missing(self) -> None:
        # An edge with malformed props (None / non-dict) is a contract
        # violation, but the backfill helper must not crash on it. The
        # safest behaviour is to leave it alone and count it as
        # invalid, so the operator sees the count and can fix the
        # underlying producer.
        edges = [
            {"from": "a", "to": "b", "type": "depends_on", "props": None},
        ]
        graph = _graph(edges)
        report = backfill_confidence(graph)
        self.assertEqual(report.invalid + report.filled + report.unchanged, 1)


class BackfillCliTest(unittest.TestCase):
    """End-to-end smoke for ``wd migrate --add-confidence``."""

    def test_cli_writes_back_filled_graph(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".weld").mkdir()
            graph = _graph([
                {
                    "from": "a", "to": "b", "type": "depends_on",
                    "props": {"source_strategy": "tree_sitter"},
                },
            ])
            graph_path = root / ".weld" / "graph.json"
            graph_path.write_text(json.dumps(graph))

            from weld._graph_cli import main as graph_cli_main
            from contextlib import redirect_stdout
            import io
            buf = io.StringIO()
            with redirect_stdout(buf):
                graph_cli_main(
                    ["--root", str(root), "migrate", "--add-confidence"],
                )

            written = json.loads(graph_path.read_text())
            self.assertEqual(
                written["edges"][0]["props"]["confidence"], "definite",
            )
            # The CLI prints a JSON envelope with the report counts.
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload.get("filled"), 1)


if __name__ == "__main__":
    unittest.main()
