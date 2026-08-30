"""``wd brief`` federates at a polyrepo root (Finding 01, ...uuxaz.3).

Field eval v0.23.1, Finding 01 (High), governed by ADR 0134 ("cannot answer"
is a distinct outcome from "answered, empty"). At a federation root
(``.weld/workspaces.yaml`` present) with a root meta-graph, ``wd brief`` read
only the root graph -- the ``repo:`` nodes -- and returned ``primary: []`` /
"No matches found" for terms that ``wd query`` resolved to child nodes. brief
is weld's designated agent entry point, and ``wd bootstrap`` names it the
"Default starting point" at that same root, so the silent empty was worse than
a broken capability: it is a wrong answer to weld's primary consumer, an agent
that cannot apply a human's double-take.

The fix federates ``wd brief`` exactly as ``wd query`` and ``weld_brief`` (MCP)
already do: at a federation root it loads a
:class:`~weld.federation.FederatedGraph`, so the brief spans child repos.
``brief()`` needs only ``graph.query()`` and ``graph.dump()``, both provided by
``FederatedGraph`` with the same shape, so this is a loader swap -- not a
change to brief's emission, ranking, or relevance labeling.

This suite locks that contract:

* ``wd brief`` at a federation root returns the child matches ``wd query``
  returns (federation, not a root-meta-only empty).
* ``wd brief`` and ``wd query`` agree on the match id set at that root
  (parity: brief is not narrower than query for the same term).
* a single-repo root is unchanged (no federation regression).
* a graph-less federation root still refuses with the shared cannot-answer
  guidance (ADR 0134), unchanged by federating the present-graph case.
"""

from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from weld.brief import main as brief_main
from weld.contract import SCHEMA_VERSION
from weld.graph import main as graph_cli_main
from weld.workspace import (
    UNIT_SEPARATOR,
    ChildEntry,
    WorkspaceConfig,
    dump_workspaces_yaml,
)

_TS = "2026-04-02T12:00:00+00:00"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=cwd, check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _init_repo(repo_root: Path) -> Path:
    """Create a git repo so federation child-loading has a real checkout."""
    repo_root.mkdir(parents=True, exist_ok=True)
    _git(repo_root, "init", "-q")
    _git(repo_root, "config", "user.email", "t@example.com")
    _git(repo_root, "config", "user.name", "t")
    (repo_root / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(repo_root, "add", "-A")
    _git(repo_root, "commit", "-q", "-m", "initial commit")
    return repo_root


def _graph_payload(nodes: dict, *, schema_version: int = 1) -> dict:
    return {
        "meta": {
            "version": SCHEMA_VERSION,
            "updated_at": _TS,
            "git_sha": "def456",
            "schema_version": schema_version,
        },
        "nodes": nodes,
        "edges": [],
    }


def _write_graph(repo_root: Path, payload: dict) -> None:
    weld_dir = repo_root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    (weld_dir / "graph.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )


def _entity(label: str, file: str) -> dict:
    return {
        "type": "entity",
        "label": label,
        "props": {"file": file, "exports": [label], "description": f"{label} model."},
    }


def _build_federation(root: Path) -> None:
    """Two child repos, each holding a ``Store`` entity; root registers both."""
    child_api = _init_repo(root / "services-api")
    child_auth = _init_repo(root / "services-auth")
    _write_graph(child_api, _graph_payload(
        {"entity:Store": _entity("Store", "src/services-api/store.py")}))
    _write_graph(child_auth, _graph_payload(
        {"entity:Store": _entity("Store", "src/services-auth/store.py")}))
    root_nodes = {
        f"repo:{name}": {"type": "repo", "label": name, "props": {"path": name}}
        for name in ("services-api", "services-auth")
    }
    _write_graph(root, _graph_payload(root_nodes, schema_version=2))
    dump_workspaces_yaml(
        WorkspaceConfig(
            children=[
                ChildEntry(name="services-api", path="services-api"),
                ChildEntry(name="services-auth", path="services-auth"),
            ],
            cross_repo_strategies=[],
        ),
        root / ".weld" / "workspaces.yaml",
    )


def _run_brief(root: Path, term: str) -> dict:
    buf = io.StringIO()
    with redirect_stdout(buf):
        brief_main([term, "--root", str(root), "--no-refresh"])
    return json.loads(buf.getvalue())


def _run_query_json(root: Path, term: str) -> dict:
    buf = io.StringIO()
    with redirect_stdout(buf):
        graph_cli_main(["--root", str(root), "query", term, "--json", "--no-refresh"])
    return json.loads(buf.getvalue())


def _brief_ids(brief_env: dict) -> set:
    ids: set = set()
    for bucket in ("primary", "interfaces", "docs", "build", "boundaries"):
        for node in brief_env.get(bucket, []):
            ids.add(node["id"])
    return ids


class FederatedBriefTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self.addCleanup(self._cleanup)
        self._root = Path(self._tmp)
        _build_federation(self._root)

    def _cleanup(self) -> None:
        import shutil

        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_brief_spans_child_repos(self) -> None:
        """``wd brief`` returns child matches, not a root-meta-only empty."""
        brief_env = _run_brief(self._root, "Store")
        ids = _brief_ids(brief_env)
        api_id = f"services-api{UNIT_SEPARATOR}entity:Store"
        auth_id = f"services-auth{UNIT_SEPARATOR}entity:Store"
        self.assertIn(api_id, ids, f"brief did not federate; buckets={brief_env}")
        self.assertIn(auth_id, ids, f"brief did not federate; buckets={brief_env}")
        # The silent-empty symptom the finding reported must be gone.
        self.assertNotIn(
            "No matches found for query: 'Store'",
            brief_env.get("warnings", []),
        )

    def test_brief_matches_query_id_set(self) -> None:
        """Parity: brief is not narrower than query for the same term/root."""
        query_ids = {m["id"] for m in _run_query_json(self._root, "Store")["matches"]}
        brief_ids = _brief_ids(_run_brief(self._root, "Store"))
        # brief buckets the same matches query surfaces; every query match
        # id must appear in some brief bucket.
        self.assertTrue(
            query_ids.issubset(brief_ids),
            f"brief dropped query matches: query={query_ids} brief={brief_ids}",
        )


class FederatedBriefMcpParityTest(unittest.TestCase):
    """``wd brief --json`` is byte-identical to ``weld_brief`` at a fed root.

    The MCP surface must be a thin wrapper of the product: both go through
    ``shape_brief(brief(FederatedGraph(root), term))``, so the federated CLI
    output and the MCP handler output must be the same dict. Auto-refresh is
    frozen so the comparison is not perturbed by a refresh on one path only.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self.addCleanup(
            lambda: __import__("shutil").rmtree(self._tmp, ignore_errors=True))
        self._root = Path(self._tmp)
        _build_federation(self._root)

    def test_cli_brief_equals_mcp_weld_brief(self) -> None:
        import os
        from unittest.mock import patch

        from weld import mcp_server

        with patch.dict(os.environ, {"WELD_AUTO_REFRESH": "0"}):
            cli_env = _run_brief(self._root, "Store")
            mcp_env = mcp_server.weld_brief("Store", root=self._root)
        self.assertEqual(cli_env, mcp_env)


class SingleRepoBriefNoRegressionTest(unittest.TestCase):
    """A non-federated root brief is unchanged by the federation branch."""

    def test_single_repo_brief_serves_root_graph(self) -> None:
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        root = Path(tmp)
        _write_graph(root, _graph_payload(
            {"entity:Store": _entity("Store", "src/store.py")}))
        brief_env = _run_brief(root, "Store")
        self.assertIn("entity:Store", _brief_ids(brief_env))


class GraphlessFederationBriefRefusesTest(unittest.TestCase):
    """A graph-less federation root still refuses (ADR 0134 cannot-answer)."""

    def test_no_root_graph_refuses_with_guidance(self) -> None:
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        root = Path(tmp)
        (root / ".weld").mkdir(parents=True, exist_ok=True)
        dump_workspaces_yaml(
            WorkspaceConfig(
                children=[ChildEntry(name="child", path="child")],
                cross_repo_strategies=[],
            ),
            root / ".weld" / "workspaces.yaml",
        )
        stderr_buf = io.StringIO()
        exit_code = 0
        try:
            with redirect_stdout(io.StringIO()), redirect_stderr(stderr_buf):
                brief_main(["Store", "--root", str(root), "--no-refresh"])
        except SystemExit as exc:
            exit_code = exc.code if isinstance(exc.code, int) else 1
        self.assertNotEqual(exit_code, 0)
        self.assertIn("No Weld graph found.", stderr_buf.getvalue())


if __name__ == "__main__":
    unittest.main()
