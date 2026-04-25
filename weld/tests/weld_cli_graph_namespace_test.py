"""Tests for the canonical ``wd graph`` namespace and legacy aliases."""

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


def _write_graph(root: Path) -> None:
    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    (weld_dir / "graph.json").write_text(
        json.dumps(
            {
                "meta": {
                    "version": SCHEMA_VERSION,
                    "schema_version": 1,
                    "updated_at": "2026-04-25T00:00:00+00:00",
                },
                "nodes": {
                    "file:src/auth.py": {
                        "type": "file",
                        "label": "auth.py",
                        "props": {"file": "src/auth.py"},
                    }
                },
                "edges": [],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _run_cli(args: list[str]) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli_main(args)
    if rc not in (None, 0):
        raise AssertionError(f"unexpected return code {rc} for {args!r}")
    return buf.getvalue()


class GraphNamespaceTest(unittest.TestCase):
    def test_graph_stats_matches_stats_alias(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_graph(root)

            canonical = json.loads(
                _run_cli(["graph", "--root", str(root), "stats"])
            )
            alias = json.loads(_run_cli(["--root", str(root), "stats"]))

            self.assertEqual(canonical["total_nodes"], 1)
            self.assertEqual(canonical, alias)

    def test_graph_validate_matches_validate_alias(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_graph(root)

            canonical = json.loads(
                _run_cli(["graph", "--root", str(root), "validate"])
            )
            alias = json.loads(_run_cli(["--root", str(root), "validate"]))

            self.assertEqual(canonical, {"valid": True, "errors": []})
            self.assertEqual(alias, canonical)

    def test_top_level_help_names_canonical_graph_aliases(self) -> None:
        text = _run_cli(["--help"])

        self.assertIn("graph          Canonical graph namespace", text)
        self.assertIn("stats          Alias for `wd graph stats`", text)
        self.assertIn("validate       Alias for `wd graph validate`", text)


if __name__ == "__main__":
    unittest.main()
