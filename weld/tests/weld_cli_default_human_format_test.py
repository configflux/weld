"""End-to-end CLI tests for the ADR 0040 default-format convention.

ADR 0040 declares that every ``wd`` retrieval command defaults to
human-readable text and accepts ``--json`` for the previous JSON
envelope. This module drives the CLI in both modes for each of the
seven commands in scope (query, find, context, path, callers,
references, stats, stale, graph communities) so a regression that
flips a default back to JSON, or one that breaks the JSON envelope
schema, fails loudly here.

Pure-renderer unit coverage lives in
``weld_cli_render_helpers_test.py``.
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
from weld.cli import main as wd_main  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _write_graph(root: Path) -> None:
    """Materialize a small but realistic ``.weld/graph.json`` under *root*."""
    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    nodes: dict[str, dict] = {
        "entity:Store": {
            "type": "entity",
            "label": "Store",
            "props": {"description": "A canonical Store entity."},
        },
        "entity:Cart": {
            "type": "entity",
            "label": "Cart",
            "props": {"description": "Shopping cart aggregate."},
        },
        "file:weld/install.sh": {
            "type": "file",
            "label": "install.sh",
            "props": {"file": "weld/install.sh"},
        },
        "symbol:py:weld.shop:checkout": {
            "type": "symbol",
            "label": "shop.checkout",
            "props": {
                "file": "weld/shop.py",
                "language": "python",
                "qualified_name": "weld.shop.checkout",
            },
        },
        "symbol:py:weld.shop:total": {
            "type": "symbol",
            "label": "shop.total",
            "props": {
                "file": "weld/shop.py",
                "language": "python",
                "qualified_name": "weld.shop.total",
            },
        },
    }
    edges: list[dict] = [
        {
            "from": "entity:Store",
            "to": "entity:Cart",
            "type": "depends_on",
            "props": {},
        },
        {
            "from": "symbol:py:weld.shop:checkout",
            "to": "symbol:py:weld.shop:total",
            "type": "calls",
            "props": {},
        },
    ]
    payload = {
        "meta": {"version": 1, "schema_version": 1},
        "nodes": nodes,
        "edges": edges,
    }
    (weld_dir / "graph.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_file_index(root: Path) -> None:
    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    index = {
        "files": {
            "install.sh": ["install", "sh", "bash", "bootstrap"],
            "README.md": ["readme", "install", "guide"],
        }
    }
    (weld_dir / "file-index.json").write_text(
        json.dumps(index), encoding="utf-8",
    )


def _run_cli(*args: str) -> str:
    """Invoke the graph-namespace CLI and return captured stdout."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli_main(list(args))
    return buf.getvalue()


def _run_top_cli(*args: str) -> str:
    """Invoke the top-level ``wd`` CLI (covers ``wd graph communities``)."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        wd_main(list(args))
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Per-command default-vs-json tests
# ---------------------------------------------------------------------------


class _CliFormatBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self.root = Path(self._tmp)
        _write_graph(self.root)
        _write_file_index(self.root)
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        import shutil

        shutil.rmtree(self._tmp, ignore_errors=True)

    def assert_human(self, out: str, *needles: str) -> None:
        # Human form starts with the convention header and is not parseable
        # as JSON.
        self.assertTrue(out.startswith("#"), f"not a human header: {out[:80]!r}")
        with self.assertRaises(ValueError):
            json.loads(out)
        for needle in needles:
            self.assertIn(needle, out)


class QueryCliFormatTest(_CliFormatBase):
    def test_default_is_human(self) -> None:
        out = _run_cli("--root", str(self.root), "query", "Store")
        self.assert_human(out, "# query: Store", "entity:Store")

    def test_json_flag_emits_envelope(self) -> None:
        out = _run_cli("--root", str(self.root), "query", "Store", "--json")
        payload = json.loads(out)
        self.assertEqual(payload["query"], "Store")
        self.assertTrue(any(m["id"] == "entity:Store" for m in payload["matches"]))


class FindCliFormatTest(_CliFormatBase):
    def test_default_is_table(self) -> None:
        out = _run_cli("--root", str(self.root), "find", "install")
        self.assertIn("# find: install", out)
        self.assertIn("path", out)
        self.assertIn("score", out)
        self.assertIn("install.sh", out)

    def test_json_flag_emits_envelope(self) -> None:
        out = _run_cli("--root", str(self.root), "find", "install", "--json")
        payload = json.loads(out)
        self.assertEqual(payload["query"], "install")
        self.assertTrue(any(f["path"] == "install.sh" for f in payload["files"]))


class ContextCliFormatTest(_CliFormatBase):
    def test_default_is_human(self) -> None:
        out = _run_cli("--root", str(self.root), "context", "entity:Store")
        self.assert_human(out, "# context: entity:Store", "depends_on", "entity:Cart")

    def test_json_flag_emits_envelope(self) -> None:
        out = _run_cli(
            "--root", str(self.root), "context", "entity:Store", "--json",
        )
        payload = json.loads(out)
        self.assertEqual(payload["node"]["id"], "entity:Store")


class PathCliFormatTest(_CliFormatBase):
    def test_default_is_chain(self) -> None:
        out = _run_cli(
            "--root", str(self.root), "path", "entity:Store", "entity:Cart",
        )
        self.assertIn("entity:Store -> entity:Cart", out)

    def test_json_flag_emits_envelope(self) -> None:
        out = _run_cli(
            "--root", str(self.root), "path",
            "entity:Store", "entity:Cart", "--json",
        )
        payload = json.loads(out)
        self.assertIsNotNone(payload.get("path"))


class CallersCliFormatTest(_CliFormatBase):
    def test_default_is_human(self) -> None:
        out = _run_cli(
            "--root", str(self.root), "callers", "symbol:py:weld.shop:total",
        )
        self.assert_human(out, "# callers: symbol:py:weld.shop:total")

    def test_json_flag_emits_envelope(self) -> None:
        out = _run_cli(
            "--root", str(self.root), "callers",
            "symbol:py:weld.shop:total", "--json",
        )
        payload = json.loads(out)
        self.assertEqual(payload["symbol"], "symbol:py:weld.shop:total")


class ReferencesCliFormatTest(_CliFormatBase):
    def test_default_is_human(self) -> None:
        out = _run_cli("--root", str(self.root), "references", "total")
        self.assert_human(out, "# references: total")

    def test_json_flag_emits_envelope(self) -> None:
        out = _run_cli(
            "--root", str(self.root), "references", "total", "--json",
        )
        payload = json.loads(out)
        self.assertEqual(payload["symbol"], "total")
        self.assertIn("matches", payload)
        self.assertIn("files", payload)


class StatsCliFormatTest(_CliFormatBase):
    def test_default_is_human(self) -> None:
        out = _run_cli("--root", str(self.root), "stats")
        self.assert_human(out, "# stats", "total_nodes:")

    def test_json_flag_emits_envelope(self) -> None:
        out = _run_cli("--root", str(self.root), "stats", "--json")
        payload = json.loads(out)
        self.assertIn("total_nodes", payload)
        self.assertIn("nodes_by_type", payload)


class StaleCliFormatTest(_CliFormatBase):
    def test_default_is_human(self) -> None:
        out = _run_cli("--root", str(self.root), "stale")
        self.assert_human(out, "# stale", "stale:")

    def test_json_flag_emits_envelope(self) -> None:
        out = _run_cli("--root", str(self.root), "stale", "--json")
        payload = json.loads(out)
        self.assertIn("stale", payload)
        self.assertIn("source_stale", payload)


class GraphCommunitiesCliFormatTest(_CliFormatBase):
    def test_default_is_markdown_report(self) -> None:
        out = _run_top_cli(
            "graph", "--root", str(self.root), "communities",
        )
        # Markdown headers, not JSON.
        with self.assertRaises(ValueError):
            json.loads(out)
        self.assertIn("# Graph Community Report", out)

    def test_json_flag_emits_envelope(self) -> None:
        out = _run_top_cli(
            "graph", "--root", str(self.root), "communities", "--json",
        )
        payload = json.loads(out)
        self.assertIn("communities", payload)
        self.assertIn("hubs", payload)

    def test_format_json_legacy_flag_still_works(self) -> None:
        out = _run_top_cli(
            "graph", "--root", str(self.root), "communities",
            "--format", "json",
        )
        payload = json.loads(out)
        self.assertIn("communities", payload)


# ---------------------------------------------------------------------------
# JSON-schema preservation (backward compatibility)
# ---------------------------------------------------------------------------


class JsonSchemaPreservedTest(_CliFormatBase):
    """The JSON envelope shape must not change under ADR 0040."""

    def test_query_json_has_canonical_keys(self) -> None:
        out = _run_cli("--root", str(self.root), "query", "Store", "--json")
        payload = json.loads(out)
        for key in ("query", "matches", "neighbors", "edges"):
            self.assertIn(key, payload)

    def test_find_json_has_canonical_keys(self) -> None:
        out = _run_cli("--root", str(self.root), "find", "install", "--json")
        payload = json.loads(out)
        self.assertIn("query", payload)
        self.assertIn("files", payload)
        self.assertTrue(all("path" in f and "score" in f for f in payload["files"]))

    def test_stats_json_has_canonical_keys(self) -> None:
        out = _run_cli("--root", str(self.root), "stats", "--json")
        payload = json.loads(out)
        for key in (
            "total_nodes",
            "total_edges",
            "nodes_by_type",
            "edges_by_type",
            "description_coverage_pct",
            "top_authority_nodes",
        ):
            self.assertIn(key, payload)


if __name__ == "__main__":
    unittest.main()
