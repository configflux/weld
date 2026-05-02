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
    "agents_md_only",
    "skill_md_only",
    "mcp_only",
)


def _canonical_graph(graph: dict) -> str:
    return json.dumps(graph, sort_keys=True, separators=(",", ":"))


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

    def test_fixture_discovery_is_deterministic(self) -> None:
        for name in _FIXTURES:
            with self.subTest(name=name):
                fixture = _FIXTURE_ROOT / name
                first = discover_agent_graph(
                    fixture,
                    git_sha="fixture",
                    updated_at="2026-04-24T00:00:00+00:00",
                )
                second = discover_agent_graph(
                    fixture,
                    git_sha="fixture",
                    updated_at="2026-04-24T00:00:00+00:00",
                )
                self.assertEqual(
                    _canonical_graph(first),
                    _canonical_graph(second),
                )

    def test_agents_md_only_fixture_discovers_generic_instruction(self) -> None:
        graph = discover_agent_graph(
            _FIXTURE_ROOT / "agents_md_only",
            git_sha="fixture",
            updated_at="2026-04-24T00:00:00+00:00",
        )
        nodes = list(graph["nodes"].values())
        node_kinds = {(n["type"], n["props"].get("platform")) for n in nodes}
        # Generic AGENTS.md is discovered as an instruction on the generic platform.
        self.assertIn(("instruction", "generic"), node_kinds)
        # The bundled SKILL.md is discovered as a generic skill.
        self.assertIn(("skill", "generic"), node_kinds)
        # The Markdown reference to docs/style.md resolves to a real file.
        file_nodes = [n for n in nodes if n["type"] == "file"]
        self.assertTrue(any(
            n["props"].get("file", "").endswith("docs/style.md")
            and n["props"].get("exists") is True
            for n in file_nodes
        ))

    def test_skill_md_only_fixture_discovers_multiple_generic_skills(self) -> None:
        graph = discover_agent_graph(
            _FIXTURE_ROOT / "skill_md_only",
            git_sha="fixture",
            updated_at="2026-04-24T00:00:00+00:00",
        )
        skill_names = {
            n["props"].get("name") for n in graph["nodes"].values()
            if n["type"] == "skill" and n["props"].get("platform") == "generic"
        }
        self.assertEqual(
            skill_names,
            {"architecture-decision", "release-notes"},
        )

    def test_mcp_only_fixture_discovers_generic_mcp_servers(self) -> None:
        graph = discover_agent_graph(
            _FIXTURE_ROOT / "mcp_only",
            git_sha="fixture",
            updated_at="2026-04-24T00:00:00+00:00",
        )
        nodes = list(graph["nodes"].values())
        mcp_names = {
            n["props"].get("name") for n in nodes
            if n["type"] == "mcp-server" and n["props"].get("platform") == "generic"
        }
        # Both servers declared in .mcp.json must be derived as nodes.
        self.assertTrue({"filesystem", "github"} <= mcp_names)
        # The generic config node itself is also present.
        config_nodes = [n for n in nodes if n["type"] == "config"]
        self.assertTrue(any(
            n["props"].get("file", "").endswith(".mcp.json")
            for n in config_nodes
        ))

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
        # non-published `.claude/skills/agent-system-maintainer/SKILL.md`
        # is excluded by `.publishignore`, so asserting it would break
        # public CI.
        self.assertTrue({
            ".agents/skills/agent-system-maintainer/SKILL.md",
            ".github/agents/agent-architect.agent.md",
            ".github/skills/agent-system-maintainer/SKILL.md",
        } <= paths)


# Slice 2/3 cross-platform demo coverage assertions live in the sibling
# weld_agent_graph_demo_coverage_test.py module.


if __name__ == "__main__":
    unittest.main()
