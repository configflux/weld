"""``wd review`` CLI surface (ADR 0055, ADR 0040).

Pins:
* ``wd review status`` works on a fresh repo (no review-state file).
* ``--json`` opt-in emits a JSON envelope; default is human text.
* ``wd review list`` returns pending edges and accepts ``--type`` /
  ``--source`` / ``--limit``.
* ``wd review accept <id>`` mutates the graph.
* ``wd review --pattern`` requires ``--yes`` for unattended runs.
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

from weld._review import mint_edge_id  # noqa: E402
from weld._review_cli import main as review_main  # noqa: E402
from weld.graph import Graph  # noqa: E402


def _seed_graph(root: Path) -> dict:
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    g = Graph(root)
    g.load()
    g.add_node("symbol:caller", "symbol", "caller", {})
    g.add_node("symbol:callee", "symbol", "callee", {})
    edge = {
        "from": "symbol:caller",
        "to": "symbol:callee",
        "type": "calls",
        "props": {
            "source_strategy": "anthropic_enrichment",
            "confidence": "speculative",
        },
    }
    g.add_edge(edge["from"], edge["to"], edge["type"], edge["props"])
    g.save()
    return edge


def _run(args: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    rc = 0
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = review_main(args) or 0
    except SystemExit as exc:
        rc = exc.code if isinstance(exc.code, int) else 1
    return rc, out.getvalue(), err.getvalue()


class StatusCliTest(unittest.TestCase):
    def test_status_on_empty_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_graph(root)
            rc, out, _ = _run(["--root", str(root), "status", "--json"])
            self.assertEqual(rc, 0)
            payload = json.loads(out)
            self.assertEqual(payload["pending"], 1)
            self.assertEqual(payload["accepted"], 0)
            self.assertEqual(payload["rejected"], 0)
            self.assertEqual(payload["stale"], 0)


class ListCliTest(unittest.TestCase):
    def test_list_returns_pending_edges_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_graph(root)
            rc, out, _ = _run(["--root", str(root), "list", "--json"])
            self.assertEqual(rc, 0)
            data = json.loads(out)
            self.assertEqual(len(data["edges"]), 1)

    def test_list_supports_human_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_graph(root)
            rc, out, _ = _run(["--root", str(root), "list"])
            self.assertEqual(rc, 0)
            self.assertIn("pending", out.lower())


class AcceptCliTest(unittest.TestCase):
    def test_accept_promotes_speculative_to_definite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            edge = _seed_graph(root)
            eid = mint_edge_id(edge)
            rc, _, _ = _run([
                "--root", str(root), "accept", eid,
                "--reason", "LGTM", "--json",
            ])
            self.assertEqual(rc, 0)
            g2 = Graph(root)
            g2.load()
            self.assertEqual(
                g2.dump()["edges"][0]["props"]["confidence"], "definite",
            )


class PatternCliTest(unittest.TestCase):
    """``--pattern`` requires ``--yes`` for non-interactive bulk."""

    def test_pattern_without_yes_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_graph(root)
            rc, _, err = _run([
                "--root", str(root), "accept",
                "--pattern", "type=calls", "--json",
            ])
            self.assertNotEqual(rc, 0)
            self.assertTrue(
                "yes" in err.lower() or "yes" in err,
                "refusal should mention --yes",
            )

    def test_pattern_with_yes_accepts_matching_edges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_graph(root)
            rc, out, _ = _run([
                "--root", str(root), "accept",
                "--pattern", "type=calls", "--yes", "--json",
            ])
            self.assertEqual(rc, 0)
            data = json.loads(out)
            self.assertEqual(data["accepted"], 1)


if __name__ == "__main__":
    unittest.main()
