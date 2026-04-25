"""Tests for the ``--top N`` flag on ``wd stats``.

Follow-up from bd-5038-3nr.3 4-eye review: ``wd stats`` previously hard-coded
its top-authority list at five entries (see
``weld._graph_stats._TOP_AUTHORITY_LIMIT``). Power users on larger graphs
need to widen that window without rewriting their tooling. The change is
deliberately additive:

- The default remains five so existing fixtures and consumers stay green.
- ``compute_stats`` accepts a ``top`` kwarg threaded from the CLI.
- The returned payload gains a ``top`` integer reflecting the cap used,
  so JSON consumers can surface "Top N authority nodes" without rejoining
  against the original argv.
- ``wd stats --top N`` honours the requested value end-to-end (CLI -> graph
  -> compute_stats).
- Invalid values (zero, negative, non-int) are rejected by argparse so a
  bad invocation never silently returns an empty list.

Tests are layered: unit-level checks on ``compute_stats`` for the limit
logic and additive ``top`` field, plus CLI-level black-box checks driving
``wd stats --top N`` through ``cli_main``.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld._graph_cli import main as cli_main  # noqa: E402
from weld._graph_stats import compute_stats  # noqa: E402
from weld.contract import SCHEMA_VERSION  # noqa: E402


def _make_data(node_count: int) -> dict:
    """Build a synthetic graph payload with *node_count* entity nodes.

    Each ``entity:N{i}`` node points at ``entity:N0`` with ``i`` edges so
    every node ends up with a unique total degree -- which makes the
    top-N ranking deterministic and easy to count.
    """
    nodes = {
        f"entity:N{i}": {
            "type": "entity",
            "label": f"N{i}",
            "props": {},
        }
        for i in range(node_count)
    }
    edges: list[dict] = []
    for i in range(1, node_count):
        for _ in range(i):
            edges.append({
                "from": f"entity:N{i}",
                "to": "entity:N0",
                "type": "depends_on",
                "props": {},
            })
    return {"meta": {"version": 1}, "nodes": nodes, "edges": edges}


def _write_graph(root: Path, payload: dict) -> None:
    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    (weld_dir / "graph.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _run_stats(root: Path, *extra: str) -> dict:
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli_main(["--root", str(root), "stats", *extra])
    return json.loads(buf.getvalue())


class TestComputeStatsTopKwarg(unittest.TestCase):
    """Unit-level: ``compute_stats(top=N)`` honours the requested cap."""

    def test_default_top_is_five(self) -> None:
        payload = compute_stats(_make_data(20))
        self.assertEqual(len(payload["top_authority_nodes"]), 5)
        self.assertEqual(payload["top"], 5)

    def test_top_widens_authority_list(self) -> None:
        payload = compute_stats(_make_data(20), top=15)
        self.assertEqual(len(payload["top_authority_nodes"]), 15)
        self.assertEqual(payload["top"], 15)

    def test_top_narrows_authority_list(self) -> None:
        payload = compute_stats(_make_data(20), top=2)
        self.assertEqual(len(payload["top_authority_nodes"]), 2)
        self.assertEqual(payload["top"], 2)

    def test_top_larger_than_node_count_returns_all(self) -> None:
        payload = compute_stats(_make_data(3), top=50)
        # Only three nodes available -> three entries, but the cap field
        # still records what the caller asked for.
        self.assertEqual(len(payload["top_authority_nodes"]), 3)
        self.assertEqual(payload["top"], 50)


class TestStatsCliTopFlag(unittest.TestCase):
    """CLI-level: ``wd stats --top N`` is end-to-end correct."""

    def _setup_root(self, node_count: int) -> Path:
        tmp = tempfile.mkdtemp()
        root = Path(tmp)
        payload = _make_data(node_count)
        # Stamp the standard schema version so the CLI loads happily.
        payload["meta"] = {
            "version": SCHEMA_VERSION,
            "schema_version": 1,
            "updated_at": "2026-04-25T00:00:00+00:00",
        }
        _write_graph(root, payload)
        return root

    def test_default_invocation_keeps_five(self) -> None:
        root = self._setup_root(12)
        out = _run_stats(root)
        self.assertEqual(len(out["top_authority_nodes"]), 5)
        self.assertEqual(out["top"], 5)

    def test_top_flag_widens_to_twenty(self) -> None:
        root = self._setup_root(25)
        out = _run_stats(root, "--top", "20")
        self.assertEqual(len(out["top_authority_nodes"]), 20)
        self.assertEqual(out["top"], 20)

    def test_top_flag_narrows_to_one(self) -> None:
        root = self._setup_root(12)
        out = _run_stats(root, "--top", "1")
        self.assertEqual(len(out["top_authority_nodes"]), 1)
        self.assertEqual(out["top"], 1)

    def test_top_zero_is_rejected(self) -> None:
        root = self._setup_root(12)
        err_buf = io.StringIO()
        with self.assertRaises(SystemExit):
            with redirect_stderr(err_buf):
                cli_main(["--root", str(root), "stats", "--top", "0"])

    def test_top_negative_is_rejected(self) -> None:
        root = self._setup_root(12)
        err_buf = io.StringIO()
        with self.assertRaises(SystemExit):
            with redirect_stderr(err_buf):
                cli_main(["--root", str(root), "stats", "--top", "-3"])


class TestStatsCliTopAdditive(unittest.TestCase):
    """CLI-level: existing keys must remain present when ``--top`` is used."""

    def test_existing_keys_preserved_under_top(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_graph(root, {
                "meta": {
                    "version": SCHEMA_VERSION,
                    "schema_version": 1,
                },
                "nodes": {},
                "edges": [],
            })
            out = _run_stats(root, "--top", "7")
            for key in (
                "total_nodes",
                "total_edges",
                "nodes_by_type",
                "edges_by_type",
                "nodes_with_description",
                "description_coverage_pct",
                "description_coverage_by_type",
                "top_authority_nodes",
                "top",
                "stale",
            ):
                self.assertIn(key, out)


if __name__ == "__main__":
    unittest.main()
