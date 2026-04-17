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

def _setup_ready_root(root: Path) -> Path:
    """Create a root with discover.yaml, graph.json, and file-index.json ready."""
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
    return weld_dir

def _write_file(path: Path, content: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)

class PrimeAgentIntegrationHintTest(unittest.TestCase):
    """When no agent integration files exist, suggest bootstrap."""

    def test_suggests_bootstrap_when_no_surfaces(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_ready_root(root)
            output = prime(root)
            # Zero surfaces: print a single generic hint line.
            self.assertIn("No agent integration", output)
            self.assertIn("wd bootstrap", output)

    def test_suppresses_frameworks_with_zero_surfaces(self) -> None:
        """Only frameworks with at least one surface are listed in the matrix."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_ready_root(root)
            # Only claude command present; copilot/codex should not appear.
            _write_file(root / ".claude" / "commands" / "weld.md")
            output = prime(root)
            self.assertIn("claude:", output)
            self.assertNotIn("copilot:", output)
            self.assertNotIn("codex:", output)

    def test_claude_only_command_is_complete(self) -> None:
        """Claude has a single surface (command). Present => no bootstrap hint."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_ready_root(root)
            _write_file(root / ".claude" / "commands" / "weld.md")
            output = prime(root)
            self.assertIn("claude:", output)
            self.assertIn("command yes", output)
            # No bootstrap suggestion for a complete claude surface.
            for line in output.splitlines():
                if line.strip().startswith("claude:"):
                    self.assertNotIn("wd bootstrap claude", line)

    def test_copilot_full_surface_no_hint(self) -> None:
        """Copilot with skill + instruction + MCP is complete -> no bootstrap."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_ready_root(root)
            _write_file(root / ".github" / "skills" / "weld" / "SKILL.md")
            _write_file(root / ".github" / "instructions" / "weld.instructions.md")
            _write_file(root / ".mcp.json", "{}")
            output = prime(root)
            self.assertIn("copilot:", output)
            self.assertIn("skill yes", output)
            self.assertIn("instruction yes", output)
            self.assertIn("mcp yes", output)
            for line in output.splitlines():
                if line.strip().startswith("copilot:"):
                    self.assertNotIn("wd bootstrap copilot", line)

    def test_copilot_partial_skill_only_suggests_bootstrap(self) -> None:
        """Copilot skill present but instruction + MCP missing -> suggest."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_ready_root(root)
            _write_file(root / ".github" / "skills" / "weld" / "SKILL.md")
            output = prime(root)
            self.assertIn("copilot:", output)
            self.assertIn("skill yes", output)
            self.assertIn("instruction no", output)
            self.assertIn("mcp no", output)
            self.assertIn("wd bootstrap copilot", output)

    def test_copilot_instruction_only_partial_suggests_bootstrap(self) -> None:
        """Only instruction present -> copilot listed, partial, suggest bootstrap."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_ready_root(root)
            _write_file(root / ".github" / "instructions" / "weld.instructions.md")
            output = prime(root)
            self.assertIn("copilot:", output)
            self.assertIn("skill no", output)
            self.assertIn("instruction yes", output)
            self.assertIn("wd bootstrap copilot", output)

    def test_copilot_mcp_only_is_not_a_surface(self) -> None:
        """.mcp.json alone does not count as a copilot surface (shared signal)."""
        # Rationale: .mcp.json is shared between copilot/claude; on its own it
        # is ambient infrastructure, not a copilot bootstrap artifact. Only
        # when a copilot-specific surface (skill or instruction) exists do we
        # list copilot and report mcp status.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_ready_root(root)
            _write_file(root / ".mcp.json", "{}")
            output = prime(root)
            self.assertNotIn("copilot:", output)

    def test_codex_skill_only_partial_suggests_bootstrap(self) -> None:
        """Codex skill present, MCP config missing -> suggest bootstrap."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_ready_root(root)
            _write_file(root / ".codex" / "skills" / "weld" / "SKILL.md")
            output = prime(root)
            self.assertIn("codex:", output)
            self.assertIn("skill yes", output)
            self.assertIn("mcp no", output)
            self.assertIn("wd bootstrap codex", output)

    def test_codex_full_surface_no_hint(self) -> None:
        """Codex skill + MCP config -> complete, no bootstrap hint."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_ready_root(root)
            _write_file(root / ".codex" / "skills" / "weld" / "SKILL.md")
            _write_file(root / ".codex" / "config.toml")
            output = prime(root)
            self.assertIn("codex:", output)
            self.assertIn("skill yes", output)
            self.assertIn("mcp yes", output)
            for line in output.splitlines():
                if line.strip().startswith("codex:"):
                    self.assertNotIn("wd bootstrap codex", line)

    def test_claude_plus_copilot_partial_both_listed(self) -> None:
        """Mixed setup: claude complete, copilot partial -- both appear; only copilot gets a hint."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_ready_root(root)
            _write_file(root / ".claude" / "commands" / "weld.md")
            _write_file(root / ".github" / "skills" / "weld" / "SKILL.md")
            output = prime(root)
            self.assertIn("claude:", output)
            self.assertIn("copilot:", output)
            self.assertNotIn("codex:", output)
            self.assertIn("wd bootstrap copilot", output)
            self.assertNotIn("wd bootstrap claude", output)

    def test_matrix_header_present_when_any_surface(self) -> None:
        """Any surface => Agent surfaces header and per-framework matrix."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_ready_root(root)
            _write_file(root / ".claude" / "commands" / "weld.md")
            output = prime(root)
            self.assertIn("Agent surfaces", output)

    def test_partial_setup_adds_bootstrap_to_next_steps(self) -> None:
        """Partial framework => Next steps block contains `wd bootstrap <fw>`."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_ready_root(root)
            _write_file(root / ".github" / "skills" / "weld" / "SKILL.md")
            output = prime(root)
            self.assertIn("Next steps:", output)
            self.assertIn("wd bootstrap copilot", output.split("Next steps:")[1])

    def test_complete_setup_no_next_steps_block(self) -> None:
        """Complete framework coverage => no Next steps for agent surfaces."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_ready_root(root)
            _write_file(root / ".claude" / "commands" / "weld.md")
            output = prime(root)
            # Claude is complete with just a command file; no bootstrap needed.
            self.assertNotIn("wd bootstrap", output)

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
