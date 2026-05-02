"""CLI tests for ``wd agents discover`` diagnostics surfacing."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import textwrap
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Iterator

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.cli import main as wd_main  # noqa: E402


@contextmanager
def _cwd(path: Path) -> Iterator[None]:
    old = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


def _write(root: Path, rel_path: str, text: str = "content\n") -> None:
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _run(argv: list[str], root: Path) -> tuple[int, str]:
    out = io.StringIO()
    err = io.StringIO()
    with _cwd(root), redirect_stdout(out), redirect_stderr(err):
        rc = wd_main(argv)
    return rc, out.getvalue()


_BROKEN_REF_ASSET = textwrap.dedent(
    """\
    ---
    name: planner
    description: Plans changes.
    ---

    See [missing](docs/missing.md) and [other](docs/other.md).
    """
)


class AgentGraphDiscoverDiagnosticsTest(unittest.TestCase):
    """Cover the diagnostics summary and ``--show-diagnostics`` surface."""

    def test_text_summary_includes_diagnostic_code_breakdown(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, ".github/agents/planner.agent.md", _BROKEN_REF_ASSET)
            rc, stdout = _run(["agents", "discover"], root)
            self.assertEqual(rc, 0)
            self.assertRegex(
                stdout, r"Diagnostics: \d+ \(\d+ agent_graph_broken_reference\)",
            )
            self.assertNotIn("Referenced file does not exist", stdout)

    def test_show_diagnostics_dumps_full_list_inline(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, ".github/agents/planner.agent.md", _BROKEN_REF_ASSET)
            rc, stdout = _run(
                ["agents", "discover", "--show-diagnostics"], root,
            )
            self.assertEqual(rc, 0)
            self.assertIn("agent_graph_broken_reference", stdout)
            self.assertIn("Referenced file does not exist", stdout)
            self.assertIn(".github/agents/planner.agent.md", stdout)
            self.assertGreaterEqual(stdout.count("warning"), 2)

    def test_json_mode_unchanged_no_breakdown_line(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, ".github/agents/planner.agent.md", _BROKEN_REF_ASSET)
            rc, stdout = _run(["agents", "discover", "--json"], root)
            self.assertEqual(rc, 0)
            graph = json.loads(stdout)
            diags = graph["meta"]["diagnostics"]
            self.assertTrue(any(
                d["code"] == "agent_graph_broken_reference" for d in diags
            ))
            self.assertNotIn("Diagnostics:", stdout)
            self.assertNotIn("Agent Graph discovery", stdout)

    def test_error_severity_diagnostic_drives_nonzero_exit(self) -> None:
        import weld.agent_graph_cli as cli_mod
        original = cli_mod.discover_agent_graph

        def _fake(root, **kwargs):
            graph = original(root, **kwargs)
            graph["meta"]["diagnostics"] = list(
                graph["meta"].get("diagnostics") or []
            ) + [{
                "severity": "error", "code": "synthetic_test_error",
                "path": "AGENTS.md", "message": "fabricated for exit-code test",
            }]
            return graph

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "AGENTS.md")
            cli_mod.discover_agent_graph = _fake
            try:
                rc, stdout = _run(["agents", "discover"], root)
            finally:
                cli_mod.discover_agent_graph = original
            self.assertNotEqual(rc, 0)
            self.assertIn("synthetic_test_error", stdout)


if __name__ == "__main__":
    unittest.main()
