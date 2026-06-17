"""CLI + text-render coverage for per-language trust in ``wd stats``.

Complements :mod:`weld_stats_per_language_trust_test` (which pins the pure
aggregation): here we drive ``wd stats --json`` end-to-end so the
``per_language_trust`` block survives the CLI envelope, and we render the
human-readable ``wd stats`` text so the advisory line for a degraded
language is visible.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from weld._cli_render import render_stats
from weld._doctor_trust import UNRESOLVED_RATIO_FLOOR
from weld._graph_cli import main as cli_main


def _sym(node_id, language, origin):
    return {
        "id": node_id,
        "type": "symbol",
        "label": node_id,
        "props": {"language": language, "origin": origin},
    }


def _graph(language, *, total, unresolved):
    nodes = {}
    for i in range(total):
        origin = "unresolved" if i < unresolved else "project"
        nid = f"symbol:{language}:n{i}"
        nodes[nid] = _sym(nid, language, origin)
    # schema_version 1: the loader's version gate accepts it on every
    # build (a missing/low schema_version is the pre-federation baseline).
    return {"meta": {"schema_version": 1}, "nodes": nodes, "edges": []}


def _write_graph(root: Path, payload: dict) -> None:
    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    (weld_dir / "graph.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _run_stats_json(root: Path) -> dict:
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli_main(["--root", str(root), "stats", "--json"])
    return json.loads(buf.getvalue())


class StatsJsonCarriesTrustTest(unittest.TestCase):
    def test_json_envelope_has_per_language_trust(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_graph(root, _graph("go", total=10, unresolved=4))
            payload = _run_stats_json(root)
        self.assertIn("per_language_trust", payload)
        self.assertIn("go", payload["per_language_trust"])
        go = payload["per_language_trust"]["go"]
        self.assertEqual(go["symbols"], 10)
        self.assertEqual(go["unresolved_symbols"], 4)
        self.assertEqual(go["unresolved_symbol_ratio"], 0.4)


class StatsTextRenderTest(unittest.TestCase):
    def test_render_lists_each_language(self) -> None:
        payload = {
            "total_nodes": 3,
            "total_edges": 0,
            "per_language_trust": {
                "go": {
                    "symbols": 10,
                    "unresolved_symbols": 1,
                    "unresolved_symbol_ratio": 0.1,
                    "edges": 0,
                    "resolved_edges": 0,
                    "edge_resolution_rate": 1.0,
                    "described_symbols": 0,
                    "description_coverage_pct": 0.0,
                },
            },
        }
        text = render_stats(payload)
        self.assertIn("per_language_trust:", text)
        self.assertIn("go:", text)

    def test_render_flags_language_over_floor(self) -> None:
        over = round(UNRESOLVED_RATIO_FLOOR + 0.1, 4)
        payload = {
            "total_nodes": 10,
            "total_edges": 0,
            "per_language_trust": {
                "rust": {
                    "symbols": 10,
                    "unresolved_symbols": int(over * 10),
                    "unresolved_symbol_ratio": over,
                    "edges": 0,
                    "resolved_edges": 0,
                    "edge_resolution_rate": 1.0,
                    "described_symbols": 0,
                    "description_coverage_pct": 0.0,
                },
            },
        }
        text = render_stats(payload)
        self.assertIn("! rust", text)
        self.assertIn("floor", text)

    def test_render_no_trust_block_when_absent(self) -> None:
        text = render_stats({"total_nodes": 0, "total_edges": 0})
        self.assertNotIn("per_language_trust", text)


if __name__ == "__main__":
    unittest.main()
