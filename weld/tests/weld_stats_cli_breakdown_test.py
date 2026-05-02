"""CLI-level tests for the extended ``wd stats`` breakdown (tracked issue).

PM audit requires ``wd stats`` to surface, in addition to node/edge counts:

- Top authority (most-connected) nodes -- asserted by
  :mod:`weld_stats_top_authority_test` at the graph level.
- Graph staleness so consumers can see whether the graph is up to date
  without running ``wd stale`` separately.
- A workspace breakdown when the current root is a polyrepo workspace, so
  the demo command shows per-child context.

These tests drive the CLI plumbing in :mod:`weld._graph_cli`. They are
black-box over stdout JSON to keep the contract explicit.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld._graph_cli import main as cli_main  # noqa: E402
from weld.contract import SCHEMA_VERSION  # noqa: E402
from weld.workspace import (  # noqa: E402
    ChildEntry,
    WorkspaceConfig,
    dump_workspaces_yaml,
)


def _write_graph(root: Path, payload: dict) -> None:
    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    (weld_dir / "graph.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _run_stats(root: Path) -> dict:
    """Run ``wd stats --json`` and parse the JSON envelope.

    Per ADR 0040 the CLI defaults to human text; tests asking for
    structured fields opt in via ``--json``.
    """
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli_main(["--root", str(root), "stats", "--json"])
    return json.loads(buf.getvalue())


class TestStatsCliBaseline(unittest.TestCase):
    def test_single_repo_stats_contains_pm_breakdown_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = {
                "meta": {
                    "version": SCHEMA_VERSION,
                    "updated_at": "2026-04-24T12:00:00+00:00",
                    "schema_version": 1,
                },
                "nodes": {
                    "entity:Store": {
                        "type": "entity",
                        "label": "Store",
                        "props": {},
                    },
                    "entity:Order": {
                        "type": "entity",
                        "label": "Order",
                        "props": {},
                    },
                },
                "edges": [
                    {
                        "from": "entity:Order",
                        "to": "entity:Store",
                        "type": "depends_on",
                        "props": {},
                    },
                ],
            }
            _write_graph(root, payload)
            out = _run_stats(root)

            # PM required breakdown keys.
            self.assertIn("nodes_by_type", out)
            self.assertIn("edges_by_type", out)
            self.assertIn("top_authority_nodes", out)
            self.assertIn("stale", out)
            self.assertIn("description_coverage_pct", out)

            # Staleness payload is the same dict Graph.stale() returns; the
            # stats command must not invent its own schema here.
            self.assertIsInstance(out["stale"], dict)

    def test_single_repo_stats_omits_workspaces_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = {
                "meta": {"version": SCHEMA_VERSION, "schema_version": 1},
                "nodes": {},
                "edges": [],
            }
            _write_graph(root, payload)
            out = _run_stats(root)
            self.assertNotIn("workspaces", out)


class TestStatsCliBackwardCompat(unittest.TestCase):
    def test_existing_keys_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_graph(root, {
                "meta": {"version": SCHEMA_VERSION, "schema_version": 1},
                "nodes": {},
                "edges": [],
            })
            out = _run_stats(root)
            for key in (
                "total_nodes",
                "total_edges",
                "nodes_by_type",
                "edges_by_type",
                "nodes_with_description",
                "description_coverage_pct",
                "description_coverage_by_type",
            ):
                self.assertIn(key, out)


class TestStatsCliWorkspaceSummary(unittest.TestCase):
    def test_polyrepo_stats_includes_workspaces_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Write the root graph.
            _write_graph(root, {
                "meta": {"version": SCHEMA_VERSION, "schema_version": 1},
                "nodes": {},
                "edges": [],
            })
            # Register two children in workspaces.yaml (no child graphs yet,
            # so the summary should just count them and mark status).
            cfg = WorkspaceConfig(
                children=[
                    ChildEntry(name="alpha", path="services/alpha"),
                    ChildEntry(name="beta", path="services/beta"),
                ],
            )
            (root / ".weld").mkdir(parents=True, exist_ok=True)
            dump_workspaces_yaml(cfg, root / ".weld" / "workspaces.yaml")

            out = _run_stats(root)
            self.assertIn("workspaces", out)
            ws = out["workspaces"]
            self.assertIsInstance(ws, dict)
            self.assertEqual(ws.get("count"), 2)
            self.assertIn("children", ws)
            self.assertIsInstance(ws["children"], list)
            names = sorted(entry["name"] for entry in ws["children"])
            self.assertEqual(names, ["alpha", "beta"])
            for entry in ws["children"]:
                self.assertIn("status", entry)


if __name__ == "__main__":
    unittest.main()
