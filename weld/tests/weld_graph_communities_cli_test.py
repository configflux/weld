"""CLI tests for ``wd graph communities``."""

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

from weld.cli import main as cli_main  # noqa: E402
from weld.contract import SCHEMA_VERSION  # noqa: E402
from weld.graph_communities_render import (  # noqa: E402
    COMMUNITIES_JSON,
    COMMUNITY_INDEX,
    COMMUNITY_REPORT,
)


def _write_graph(root: Path) -> Path:
    graph_path = root / ".weld" / "graph.json"
    graph_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {
            "version": SCHEMA_VERSION,
            "schema_version": 1,
            "updated_at": "2026-05-02T00:00:00+00:00",
        },
        "nodes": {
            "service:api": {"type": "service", "label": "api", "props": {"language": "python"}},
            "symbol:py:api.main": {
                "type": "symbol",
                "label": "api.main",
                "props": {"language": "python", "file": "api.py"},
            },
            "symbol:py:api.repo": {
                "type": "symbol",
                "label": "api.repo",
                "props": {"language": "python", "file": "api.py"},
            },
            "service:worker": {"type": "service", "label": "worker", "props": {"language": "go"}},
            "symbol:go:worker.main": {
                "type": "symbol",
                "label": "worker.main",
                "props": {"language": "go", "file": "worker.go"},
            },
        },
        "edges": [
            {"from": "service:api", "to": "symbol:py:api.main", "type": "contains", "props": {}},
            {"from": "symbol:py:api.main", "to": "symbol:py:api.repo", "type": "calls", "props": {}},
            {"from": "service:worker", "to": "symbol:go:worker.main", "type": "contains", "props": {}},
            {"from": "symbol:py:api.repo", "to": "symbol:go:worker.main", "type": "relates_to", "props": {}},
        ],
    }
    graph_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return graph_path


def _run_cli(args: list[str]) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli_main(args)
    if rc not in (None, 0):
        raise AssertionError(f"unexpected return code {rc} for {args!r}")
    return buf.getvalue()


class GraphCommunitiesCliTest(unittest.TestCase):
    def test_default_outputs_human_markdown_without_writing_artifacts(self) -> None:
        # Per ADR 0040, the CLI defaults to the human-readable markdown
        # report; --json (or --format json) is required to opt into the
        # JSON envelope.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_graph(root)

            out = _run_cli(["graph", "--root", str(root), "communities"])

            self.assertIn("# Graph Community Report", out)
            self.assertIn("## Health", out)
            self.assertFalse((root / ".weld" / COMMUNITIES_JSON).exists())

    def test_json_flag_outputs_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_graph(root)

            out = _run_cli(["graph", "--root", str(root), "communities", "--json"])
            payload = json.loads(out)

            self.assertEqual(payload["summary"]["total_nodes"], 5)
            self.assertIn("communities", payload)
            self.assertFalse((root / ".weld" / COMMUNITIES_JSON).exists())

    def test_format_json_legacy_flag_outputs_envelope(self) -> None:
        # The pre-ADR-0040 explicit form `--format json` keeps working so
        # any pinned consumer that already passed it does not need a
        # second migration.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_graph(root)

            out = _run_cli([
                "graph", "--root", str(root), "communities",
                "--format", "json",
            ])
            payload = json.loads(out)

            self.assertEqual(payload["summary"]["total_nodes"], 5)

    def test_markdown_format_outputs_report(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_graph(root)

            out = _run_cli([
                "graph",
                "--root",
                str(root),
                "communities",
                "--format",
                "markdown",
            ])

            self.assertIn("# Graph Community Report", out)
            self.assertIn("## Health", out)

    def test_json_payload_exposes_top_level_hubs(self) -> None:
        # Per ADR 0039, the JSON contract must include a top-level "hubs"
        # field so the documented "report hubs" behaviour is honoured.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_graph(root)

            out = _run_cli([
                "graph", "--root", str(root), "communities", "--json",
            ])
            payload = json.loads(out)

            self.assertIn("hubs", payload)
            self.assertIsInstance(payload["hubs"], list)

    def test_write_creates_artifacts_and_does_not_mutate_graph_json(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            graph_path = _write_graph(root)
            before = graph_path.read_text(encoding="utf-8")

            out = _run_cli([
                "graph", "--root", str(root), "communities",
                "--write", "--json",
            ])
            payload = json.loads(out)
            after = graph_path.read_text(encoding="utf-8")

            self.assertEqual(after, before)
            self.assertEqual(payload["summary"]["total_nodes"], 5)
            for name in (COMMUNITIES_JSON, COMMUNITY_REPORT, COMMUNITY_INDEX):
                self.assertTrue((root / ".weld" / name).is_file(), name)
            written_json = json.loads(
                (root / ".weld" / COMMUNITIES_JSON).read_text(encoding="utf-8")
            )
            self.assertEqual(written_json["assignments"], payload["assignments"])


if __name__ == "__main__":
    unittest.main()
