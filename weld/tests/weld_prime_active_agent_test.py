"""Tests for the ``wd prime --agent`` selector.

These tests cover the active-agent override added for tracked issue: when the
caller (or the environment) identifies the current agent, ``wd prime`` must
force that framework's row into the surface matrix even if no
framework-specific files are configured yet, so a Codex user does not see
``claude: command yes`` and miss the fact that Codex itself is missing.

The generic / complete / partial / zero-surface cases remain covered in
``weld_prime_test.py`` -- this file focuses strictly on the selector.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from weld.cli import main as cli_main
from weld.prime import prime

def _minimal_discover_yaml(n_sources: int = 3) -> str:
    lines = ["sources:"]
    for i in range(n_sources):
        lines.append(f'  - glob: "src{i}/**/*.py"')
        lines.append("    type: file")
        lines.append("    strategy: python_module")
    return "\n".join(lines) + "\n"

def _minimal_graph() -> str:
    nodes = {
        f"n{i}": {
            "id": f"n{i}", "type": "file", "label": f"n{i}",
            "props": {"description": f"desc {i}"},
        }
        for i in range(10)
    }
    return json.dumps({"meta": {}, "nodes": nodes, "edges": []})

def _setup_ready_root(root: Path) -> Path:
    weld_dir = root / ".weld"
    weld_dir.mkdir()
    (weld_dir / "discover.yaml").write_text(_minimal_discover_yaml())
    (weld_dir / "graph.json").write_text(_minimal_graph())
    (weld_dir / "file-index.json").write_text("{}")
    return weld_dir

def _write_file(path: Path, content: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)

def _scrub_codex_env() -> None:
    """Remove any ``CODEX_*`` env vars so auto-detection is deterministic."""
    for var in list(os.environ):
        if var.startswith("CODEX_"):
            del os.environ[var]

class PrimeActiveAgentTest(unittest.TestCase):
    """--agent surfaces missing frameworks that the active agent would use."""

    def test_codex_active_forces_codex_row_when_absent(self) -> None:
        """With only Claude configured and active agent=codex, codex row appears."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_ready_root(root)
            _write_file(root / ".claude" / "commands" / "weld.md")
            output = prime(root, active_agent="codex")
            # Both frameworks must be listed -- claude (has a surface) and codex
            # (forced in because it is the active agent).
            self.assertIn("claude:", output)
            self.assertIn("codex:", output)
            # Codex surfaces are absent, so the matrix row reports them as no.
            codex_line = next(
                line for line in output.splitlines()
                if line.strip().startswith("codex:")
            )
            self.assertIn("skill no", codex_line)
            self.assertIn("mcp no", codex_line)
            self.assertIn("wd bootstrap codex", codex_line)

    def test_claude_active_forces_claude_row_when_absent(self) -> None:
        """With only codex configured and active agent=claude, claude row appears."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_ready_root(root)
            _write_file(root / ".codex" / "skills" / "weld" / "SKILL.md")
            _write_file(root / ".codex" / "config.toml")
            output = prime(root, active_agent="claude")
            self.assertIn("claude:", output)
            self.assertIn("codex:", output)
            claude_line = next(
                line for line in output.splitlines()
                if line.strip().startswith("claude:")
            )
            self.assertIn("command no", claude_line)
            self.assertIn("wd bootstrap claude", claude_line)

    def test_all_forces_all_rows(self) -> None:
        """--agent all lists copilot/codex/claude even when all surfaces absent."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_ready_root(root)
            output = prime(root, active_agent="all")
            self.assertIn("copilot:", output)
            self.assertIn("codex:", output)
            self.assertIn("claude:", output)
            # Each framework is absent, so each row has a bootstrap hint.
            self.assertIn("wd bootstrap copilot", output)
            self.assertIn("wd bootstrap codex", output)
            self.assertIn("wd bootstrap claude", output)

    def test_auto_detects_codex_from_env(self) -> None:
        """active_agent=None with CODEX_* env var auto-detects codex."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_ready_root(root)
            _write_file(root / ".claude" / "commands" / "weld.md")
            with patch.dict(os.environ, {"CODEX_HOME": "/tmp/foo"}, clear=False):
                # Scrub any stray CODEX_* vars other than the one we just set.
                for var in list(os.environ):
                    if var.startswith("CODEX_") and var != "CODEX_HOME":
                        del os.environ[var]
                output = prime(root, active_agent=None)
            self.assertIn("claude:", output)
            self.assertIn("codex:", output)

    def test_auto_no_env_falls_back_to_existing_behavior(self) -> None:
        """No CODEX_* env vars -> auto leaves current behavior unchanged."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_ready_root(root)
            _write_file(root / ".claude" / "commands" / "weld.md")
            with patch.dict(os.environ, {}, clear=False):
                _scrub_codex_env()
                output = prime(root, active_agent=None)
            self.assertIn("claude:", output)
            # Without a CODEX_* signal, codex should not be forced into the matrix.
            self.assertNotIn("codex:", output)

    def test_explicit_agent_overrides_auto(self) -> None:
        """Explicit --agent copilot lists copilot even without env signals."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _setup_ready_root(root)
            output = prime(root, active_agent="copilot")
            self.assertIn("copilot:", output)
            self.assertIn("wd bootstrap copilot", output)

class PrimeCliAgentFlagTest(unittest.TestCase):
    """CLI surface for the --agent flag."""

    def test_cli_accepts_agent_flag(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _setup_ready_root(Path(td))
            output = io.StringIO()
            with patch("sys.stdout", output):
                cli_main(["prime", "--root", td, "--agent", "codex"])
            self.assertIn("codex:", output.getvalue())

    def test_cli_agent_all(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            _setup_ready_root(Path(td))
            output = io.StringIO()
            with patch("sys.stdout", output):
                cli_main(["prime", "--root", td, "--agent", "all"])
            text = output.getvalue()
            self.assertIn("copilot:", text)
            self.assertIn("codex:", text)
            self.assertIn("claude:", text)

    def test_cli_agent_invalid_value_errors(self) -> None:
        """Unknown --agent value exits non-zero via argparse."""
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(SystemExit):
                with patch("sys.stderr", io.StringIO()):
                    cli_main(["prime", "--root", td, "--agent", "bogus"])

if __name__ == "__main__":
    unittest.main()
