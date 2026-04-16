"""CLI integration tests for wd brief — tests dispatch and argparse wiring.

Uses direct Python imports instead of subprocess to work reliably
in both Bazel sandbox and local environments.

"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Ensure weld package is importable from the repo root
_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.brief import main as brief_main  # noqa: E402
from weld.cli import main as cli_main  # noqa: E402
from weld.contract import SCHEMA_VERSION  # noqa: E402

_TS = "2026-04-02T12:00:00+00:00"

def _setup_graph_dir(nodes: dict | None = None, edges: list | None = None) -> str:
    """Create a temp dir with a .weld/graph.json file."""
    tmpdir = tempfile.mkdtemp()
    weld_dir = os.path.join(tmpdir, ".weld")
    os.makedirs(weld_dir)
    graph = {
        "meta": {"version": SCHEMA_VERSION, "updated_at": _TS, "git_sha": "def456"},
        "nodes": nodes or {},
        "edges": edges or [],
    }
    with open(os.path.join(weld_dir, "graph.json"), "w") as f:
        json.dump(graph, f)
    return tmpdir

def _run_brief_main(root: str, term: str,
                    extra_args: list[str] | None = None) -> dict:
    """Run brief.main() capturing stdout."""
    argv = [term, "--root", root]
    if extra_args:
        argv.extend(extra_args)
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        brief_main(argv)
    return json.loads(buf.getvalue())

class BriefCliTest(unittest.TestCase):
    """Test the brief CLI entry point via direct invocation."""

    def test_cli_produces_valid_json(self) -> None:
        root = _setup_graph_dir()
        output = _run_brief_main(root, "test")
        self.assertIsInstance(output, dict)

    def test_cli_has_all_contract_keys(self) -> None:
        root = _setup_graph_dir()
        output = _run_brief_main(root, "test")
        required = {"brief_version", "query", "primary", "interfaces",
                    "docs", "build", "boundaries", "edges", "provenance",
                    "warnings"}
        self.assertEqual(set(output.keys()), required)

    def test_cli_with_populated_graph(self) -> None:
        nodes = {
            "service:api": {
                "type": "service", "label": "API",
                "props": {"authority": "canonical", "confidence": "definite"},
            },
        }
        root = _setup_graph_dir(nodes)
        output = _run_brief_main(root, "api")
        self.assertTrue(len(output["primary"]) > 0)
        self.assertEqual(output["warnings"], [])

    def test_cli_limit_flag(self) -> None:
        nodes = {}
        for i in range(20):
            nodes[f"service:s{i:02d}"] = {
                "type": "service", "label": f"Service {i}",
                "props": {},
            }
        root = _setup_graph_dir(nodes)
        output = _run_brief_main(root, "service", ["--limit", "3"])
        self.assertLessEqual(len(output["primary"]), 3)

    def test_cli_empty_graph_warns(self) -> None:
        root = _setup_graph_dir()
        output = _run_brief_main(root, "nonexistent")
        self.assertTrue(len(output["warnings"]) > 0)

    def test_cli_query_echoed(self) -> None:
        root = _setup_graph_dir()
        output = _run_brief_main(root, "my search")
        self.assertEqual(output["query"], "my search")

    def test_cli_provenance_from_meta(self) -> None:
        root = _setup_graph_dir()
        output = _run_brief_main(root, "test")
        self.assertEqual(output["provenance"]["graph_sha"], "def456")
        self.assertEqual(output["provenance"]["updated_at"], _TS)

class BriefDispatchTest(unittest.TestCase):
    """Verify brief is dispatched from cli.main()."""

    def test_cli_dispatches_brief(self) -> None:
        root = _setup_graph_dir()
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            cli_main(["brief", "test", "--root", root])
        output = json.loads(buf.getvalue())
        self.assertEqual(output["brief_version"], 2)

    def test_help_mentions_brief(self) -> None:
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            cli_main(["--help"])
        self.assertIn("brief", buf.getvalue())

if __name__ == "__main__":
    unittest.main()
