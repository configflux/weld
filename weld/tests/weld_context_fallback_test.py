"""Tests for ``Graph.context`` and ``FederatedGraph.context`` query fallback.

tracked issue: when ``context`` is called with a free-form string that is
not an exact node id, fall back to ``query`` and
return the top match's context with a ``resolved_from`` envelope so callers
can tell the fallback fired. Prefixed child ids in the federated variant
short-circuit to the child and must NOT trigger query fallback.
"""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from weld.contract import SCHEMA_VERSION
from weld.federation import FederatedGraph, prefix_node_id
from weld.graph import Graph
from weld.workspace import ChildEntry, WorkspaceConfig, dump_workspaces_yaml

_TS = "2026-04-20T12:00:00+00:00"


def _git(repo_root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _init_repo(repo_root: Path) -> Path:
    repo_root.mkdir(parents=True, exist_ok=True)
    _git(repo_root, "init", "-q")
    _git(repo_root, "config", "user.email", "test@example.com")
    _git(repo_root, "config", "user.name", "Weld Test")
    (repo_root / "README.md").write_text("# fixture\n", encoding="utf-8")
    _git(repo_root, "add", "README.md")
    _git(repo_root, "commit", "-q", "-m", "initial")
    return repo_root


def _graph_payload(nodes: dict, edges: list[dict] | None = None, *, schema_version: int = 1) -> dict:
    return {
        "meta": {
            "version": SCHEMA_VERSION,
            "updated_at": _TS,
            "schema_version": schema_version,
        },
        "nodes": nodes,
        "edges": edges or [],
    }


def _write_graph(repo_root: Path, payload: dict) -> None:
    weld_dir = repo_root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    (weld_dir / "graph.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


class GraphContextFallbackTest(unittest.TestCase):
    """Unit tests against ``Graph.context`` fallback behavior."""

    def _make_graph(self) -> Graph:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        _write_graph(
            root,
            _graph_payload(
                {
                    "policy:clang": {
                        "type": "policy",
                        "label": "clang policy enforcement",
                        "props": {"description": "clang-tidy policy enforcement"},
                    },
                    "file:src/a.py": {
                        "type": "file",
                        "label": "alpha",
                        "props": {"file": "src/a.py"},
                    },
                },
                [
                    {
                        "from": "policy:clang",
                        "to": "file:src/a.py",
                        "type": "applies_to",
                        "props": {},
                    }
                ],
            ),
        )
        graph = Graph(root)
        graph.load()
        return graph

    def test_exact_id_match_has_no_resolved_from(self) -> None:
        graph = self._make_graph()

        ctx = graph.context("policy:clang")

        self.assertEqual(ctx["node"]["id"], "policy:clang")
        self.assertNotIn("resolved_from", ctx)
        self.assertNotIn("error", ctx)

    def test_free_form_string_resolves_to_top_match(self) -> None:
        graph = self._make_graph()

        ctx = graph.context("clang policy enforcement")

        self.assertNotIn("error", ctx)
        self.assertEqual(ctx["node"]["id"], "policy:clang")
        self.assertIn("resolved_from", ctx)
        resolved = ctx["resolved_from"]
        self.assertEqual(resolved["query"], "clang policy enforcement")
        self.assertEqual(resolved["matched_id"], "policy:clang")
        self.assertIn("score", resolved)

    def test_genuine_miss_still_returns_error(self) -> None:
        graph = self._make_graph()

        ctx = graph.context("zzzzzzz")

        self.assertIn("error", ctx)
        self.assertEqual(ctx["error"], "node not found: zzzzzzz")
        self.assertNotIn("resolved_from", ctx)

    def test_fallback_false_disables_query_even_on_hit(self) -> None:
        graph = self._make_graph()

        ctx = graph.context("clang policy enforcement", fallback=False)

        self.assertIn("error", ctx)
        self.assertEqual(ctx["error"], "node not found: clang policy enforcement")
        self.assertNotIn("resolved_from", ctx)

    def test_resolved_from_surfaces_top_match_score(self) -> None:
        """The score field must be present and numeric (from query envelope)."""
        graph = self._make_graph()

        ctx = graph.context("alpha")

        self.assertNotIn("error", ctx)
        self.assertIn("resolved_from", ctx)
        self.assertIsNotNone(ctx["resolved_from"]["score"])


class FederatedGraphContextFallbackTest(unittest.TestCase):
    """Integration tests for ``FederatedGraph.context`` fallback behavior.

    These assert the spec that a prefixed child id (display or canonical form)
    short-circuits to the child and never goes through query fallback.
    """

    def _make_federation(self) -> Path:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)

        repo_a = _init_repo(root / "repo-a")
        _init_repo(root / "repo-b")
        _write_graph(
            repo_a,
            _graph_payload(
                {
                    "policy:clang": {
                        "type": "policy",
                        "label": "clang policy enforcement",
                        "props": {"description": "clang-tidy policy enforcement"},
                    },
                    "file:src/a.py": {
                        "type": "file",
                        "label": "alpha",
                        "props": {"file": "src/a.py"},
                    },
                },
                [
                    {
                        "from": "policy:clang",
                        "to": "file:src/a.py",
                        "type": "applies_to",
                        "props": {},
                    }
                ],
            ),
        )

        root_nodes = {
            "repo:repo-a": {
                "type": "repo",
                "label": "repo-a",
                "props": {"path": "repo-a"},
            },
            "repo:repo-b": {
                "type": "repo",
                "label": "repo-b",
                "props": {"path": "repo-b"},
            },
            "doc:root-overview": {
                "type": "doc",
                "label": "root overview zeta",
                "props": {"description": "top-level overview zeta"},
            },
        }
        _write_graph(root, _graph_payload(root_nodes, [], schema_version=2))

        config = WorkspaceConfig(
            children=[
                ChildEntry(name="repo-a", path="repo-a"),
                ChildEntry(name="repo-b", path="repo-b"),
            ],
            cross_repo_strategies=[],
        )
        dump_workspaces_yaml(config, root / ".weld" / "workspaces.yaml")
        return root

    def test_prefixed_child_id_short_circuits_to_child(self) -> None:
        """Display-form prefixed id must resolve to the child node, no fallback."""
        root = self._make_federation()
        fg = FederatedGraph(root)

        ctx = fg.context("repo-a::policy:clang")

        self.assertNotIn("error", ctx)
        self.assertEqual(
            ctx["node"]["id"], prefix_node_id("repo-a", "policy:clang")
        )
        # Short-circuit path must not expose resolved_from.
        self.assertNotIn("resolved_from", ctx)

    def test_prefixed_child_id_miss_does_not_fallback(self) -> None:
        """A genuine miss under a prefixed child id returns error, no query."""
        root = self._make_federation()
        fg = FederatedGraph(root)

        ctx = fg.context("repo-a::nope:does-not-exist")

        self.assertIn("error", ctx)
        self.assertNotIn("resolved_from", ctx)

    def test_root_free_form_resolves_via_fallback(self) -> None:
        """Plain root-level free-form string falls back to query."""
        root = self._make_federation()
        fg = FederatedGraph(root)

        ctx = fg.context("zeta")

        self.assertNotIn("error", ctx)
        self.assertEqual(ctx["node"]["id"], "doc:root-overview")
        self.assertIn("resolved_from", ctx)
        self.assertEqual(ctx["resolved_from"]["query"], "zeta")
        self.assertEqual(ctx["resolved_from"]["matched_id"], "doc:root-overview")
        self.assertIn("score", ctx["resolved_from"])

    def test_root_genuine_miss_returns_error(self) -> None:
        """No match at root and not a prefixed child id → plain error."""
        root = self._make_federation()
        fg = FederatedGraph(root)

        ctx = fg.context("zzzzzzzz")

        self.assertIn("error", ctx)
        self.assertNotIn("resolved_from", ctx)

    def test_root_exact_match_no_resolved_from(self) -> None:
        """Exact-id hit at root preserves current shape (no resolved_from)."""
        root = self._make_federation()
        fg = FederatedGraph(root)

        ctx = fg.context("doc:root-overview")

        self.assertNotIn("error", ctx)
        self.assertEqual(ctx["node"]["id"], "doc:root-overview")
        self.assertNotIn("resolved_from", ctx)


if __name__ == "__main__":
    unittest.main()
