"""Agent Graph fixture matrix and demo-flow tests."""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Iterator

_repo_root = Path(__file__).resolve().parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from weld.agent_graph_audit import audit_graph  # noqa: E402
from weld.agent_graph_discovery import (  # noqa: E402
    discover_agent_assets,
    discover_agent_graph,
)
from weld.cli import main as wd_main  # noqa: E402

_FIXTURE_ROOT = _repo_root / "weld" / "tests" / "fixtures" / "agent_graph"
_DEMO_ROOT = _repo_root / "examples" / "agent-graph-demo"

_FIXTURES = (
    "copilot_only",
    "claude_only",
    "opencode_only",
    "codex_only",
    "cursor_only",
    "gemini_only",
    "mixed_workspace",
    "conflicts",
    "polyrepo",
)


@contextmanager
def _cwd(path: Path) -> Iterator[None]:
    old = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


def _run(argv: list[str], root: Path) -> tuple[int, str, str]:
    out = io.StringIO()
    err = io.StringIO()
    with _cwd(root), redirect_stdout(out), redirect_stderr(err):
        rc = wd_main(argv)
    return rc, out.getvalue(), err.getvalue()


class AgentGraphFixtureMatrixTest(unittest.TestCase):
    def test_fixture_matrix_exists_and_discovers_assets(self) -> None:
        for name in _FIXTURES:
            with self.subTest(name=name):
                fixture = _FIXTURE_ROOT / name
                self.assertTrue(fixture.is_dir())
                graph = discover_agent_graph(
                    fixture,
                    git_sha="fixture",
                    updated_at="2026-04-24T00:00:00+00:00",
                )
                self.assertGreater(len(graph["meta"]["discovered_from"]), 0)
                self.assertGreater(len(graph["nodes"]), 0)

    def test_conflict_fixture_reports_expected_audit_codes(self) -> None:
        graph = discover_agent_graph(
            _FIXTURE_ROOT / "conflicts",
            git_sha="fixture",
            updated_at="2026-04-24T00:00:00+00:00",
        )
        codes = {finding["code"] for finding in audit_graph(graph)["findings"]}

        self.assertTrue({
            "broken_reference",
            "duplicate_name",
            "missing_agent",
            "missing_mcp_config",
            "path_scope_overlap",
            "permission_conflict",
            "platform_drift",
            "responsibility_overlap",
            "unsafe_hook",
            "vague_description",
        } <= codes)


class AgentGraphDemoFlowTest(unittest.TestCase):
    def test_demo_flow_runs_and_audit_finds_known_issues(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            demo = Path(td) / "agent-graph-demo"
            shutil.copytree(_DEMO_ROOT, demo)

            self.assertEqual(_run(["agents", "discover"], demo)[0], 0)
            for argv in (
                ["agents", "list"],
                ["agents", "explain", "planner"],
                ["agents", "impact", ".github/agents/planner.agent.md"],
                [
                    "agents",
                    "plan-change",
                    "planner should always include test strategy",
                ],
            ):
                with self.subTest(argv=argv):
                    rc, _stdout, stderr = _run(argv, demo)
                    self.assertEqual((rc, stderr), (0, ""))

            rc, stdout, stderr = _run(["agents", "audit", "--json"], demo)

            self.assertEqual((rc, stderr), (0, ""))
            codes = {finding["code"] for finding in json.loads(stdout)["findings"]}
            self.assertTrue({
                "broken_reference",
                "duplicate_name",
                "missing_agent",
                "missing_mcp_config",
                "path_scope_overlap",
                "permission_conflict",
                "platform_drift",
                "rendered_copy_drift",
                "responsibility_overlap",
                "unsafe_hook",
                "vague_description",
            } <= codes)


class AgentGraphMaintainerAssetTest(unittest.TestCase):
    def test_repo_maintainer_assets_are_discoverable(self) -> None:
        assets = discover_agent_assets(_repo_root)
        paths = {asset.path for asset in assets}

        # Paths shipped in both internal and public overlays. The
        # internal-only `.claude/skills/agent-system-maintainer/SKILL.md`
        # is excluded by `.publishignore`, so asserting it would break
        # public CI.
        self.assertTrue({
            ".agents/skills/agent-system-maintainer/SKILL.md",
            ".github/agents/agent-architect.agent.md",
            ".github/skills/agent-system-maintainer/SKILL.md",
        } <= paths)


if __name__ == "__main__":
    unittest.main()
