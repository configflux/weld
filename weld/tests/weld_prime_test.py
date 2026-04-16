"""Tests for the wd prime command."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from weld.cli import main as cli_main
from weld.prime import prime

def _minimal_graph(nodes: dict | None = None, meta: dict | None = None) -> str:
    data = {
        "meta": meta or {},
        "nodes": nodes or {},
        "edges": [],
    }
    return json.dumps(data)

def _minimal_discover_yaml(n_sources: int = 3) -> str:
    lines = ["sources:"]
    for i in range(n_sources):
        lines.append(f'  - glob: "src{i}/**/*.py"')
        lines.append("    type: file")
        lines.append("    strategy: python_module")
    return "\n".join(lines) + "\n"

class PrimeNoWeldDirTest(unittest.TestCase):
    """When .weld/ does not exist, suggest wd init."""

    def test_suggests_init(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            output = prime(Path(td))
            self.assertIn("wd init", output)
            self.assertIn("not set up", output.lower())

class PrimeMissingGraphTest(unittest.TestCase):
    """When discover.yaml exists but graph.json is missing."""

    def test_suggests_discover(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            weld_dir = root / ".weld"
            weld_dir.mkdir()
            (weld_dir / "discover.yaml").write_text(_minimal_discover_yaml())
            output = prime(root)
            self.assertIn("wd discover", output)

class PrimeMissingFileIndexTest(unittest.TestCase):
    """When discover.yaml and graph.json exist but file-index.json is missing."""

    def test_suggests_build_index(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            weld_dir = root / ".weld"
            weld_dir.mkdir()
            (weld_dir / "discover.yaml").write_text(_minimal_discover_yaml())
            (weld_dir / "graph.json").write_text(_minimal_graph(
                nodes={"a": {"id": "a", "type": "file", "label": "a", "props": {}}} |
                      {f"n{i}": {"id": f"n{i}", "type": "file", "label": f"n{i}", "props": {}}
                       for i in range(10)},
            ))
            output = prime(root)
            self.assertIn("build-index", output)

class PrimeAllPresentTest(unittest.TestCase):
    """When all files are present, report OK."""

    def test_reports_ok(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            weld_dir = root / ".weld"
            weld_dir.mkdir()
            (weld_dir / "discover.yaml").write_text(_minimal_discover_yaml())
            nodes = {
                f"n{i}": {
                    "id": f"n{i}", "type": "file", "label": f"n{i}",
                    "props": {"description": f"desc {i}"},
                }
                for i in range(10)
            }
            (weld_dir / "graph.json").write_text(_minimal_graph(nodes=nodes))
            (weld_dir / "file-index.json").write_text("{}")
            # Also create agent integration to suppress that hint
            claude_dir = root / ".claude" / "commands"
            claude_dir.mkdir(parents=True)
            (claude_dir / "weld.md").write_text("test")
            output = prime(root)
            self.assertIn("No actions needed", output)

class PrimeSmallGraphTest(unittest.TestCase):
    """When the graph has very few nodes, warn about coverage."""

    def test_warns_about_small_graph(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            weld_dir = root / ".weld"
            weld_dir.mkdir()
            (weld_dir / "discover.yaml").write_text(_minimal_discover_yaml())
            nodes = {
                "a": {"id": "a", "type": "file", "label": "a", "props": {}},
                "b": {"id": "b", "type": "file", "label": "b", "props": {}},
            }
            (weld_dir / "graph.json").write_text(_minimal_graph(nodes=nodes))
            (weld_dir / "file-index.json").write_text("{}")
            output = prime(root)
            self.assertIn("2 node", output)

class PrimeAgentIntegrationHintTest(unittest.TestCase):
    """When no agent integration files exist, suggest bootstrap."""

    def test_suggests_bootstrap(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            weld_dir = root / ".weld"
            weld_dir.mkdir()
            (weld_dir / "discover.yaml").write_text(_minimal_discover_yaml())
            nodes = {
                f"n{i}": {
                    "id": f"n{i}", "type": "file", "label": f"n{i}",
                    "props": {"description": f"desc {i}"},
                }
                for i in range(10)
            }
            (weld_dir / "graph.json").write_text(_minimal_graph(nodes=nodes))
            (weld_dir / "file-index.json").write_text("{}")
            output = prime(root)
            self.assertIn("wd bootstrap", output)

class PrimeCliDispatchTest(unittest.TestCase):
    """Verify prime dispatch from the top-level CLI."""

    def test_cli_dispatches_prime(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            output = io.StringIO()
            with patch("sys.stdout", output):
                cli_main(["prime", "--root", td])
            self.assertGreater(len(output.getvalue()), 0)

    def test_help_mentions_prime(self) -> None:
        output = io.StringIO()
        with patch("sys.stdout", output):
            cli_main(["--help"])
        self.assertIn("prime", output.getvalue())

if __name__ == "__main__":
    unittest.main()
