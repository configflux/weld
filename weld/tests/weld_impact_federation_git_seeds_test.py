"""Federated ``wd impact --from-diff`` / ``--working-tree`` seed resolution.

ADR 0089 flattened the federation for ``impact``'s reverse-BFS, but the two
*git-seeded* modes (``--from-diff`` / ``--working-tree``) still shelled out to
the workspace ROOT's git only (``weld._impact_git``). In a polyrepo where the
CHILDREN are the git repos and the root is not, those modes could not resolve a
child-relative diff at all -- the root git call either failed (non-git root) or
missed every child change.

These tests lock the fan-out: at a federated root the seed discovery runs
``git diff`` / ``git status`` inside every present child, resolves each child's
paths against that child's own graph, and federation-prefixes the resulting seed
ids so they line up with the flattened graph the BFS runs over. Two children
that share a relative path (``src/app.py``) must stay disambiguated -- a change
in one child seeds only that child.

Fixture style follows ``weld_federation_trace_impact_test`` /
``weld_impact_federation_smoke_test``: a real ``git init`` per child plus a
hand-shaped ``.weld/graph.json`` so the test fails for the same reasons the
production ``FederatedGraph`` would. The root is deliberately NOT a git repo,
mirroring the polyrepo case the fix targets.
"""

from __future__ import annotations

import io
import json
import subprocess
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from weld.contract import SCHEMA_VERSION
from weld.federation_support import prefix_node_id
from weld.impact_cli import main as impact_main
from weld.workspace import ChildEntry, WorkspaceConfig, dump_workspaces_yaml

_TS = "2026-07-16T00:00:00+00:00"


def _git(repo_root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=str(repo_root),
        capture_output=True, text=True, check=True,
    )


def _rev_parse(repo_root: Path, ref: str = "HEAD") -> str:
    return subprocess.run(
        ["git", "rev-parse", ref], cwd=str(repo_root),
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _graph_payload(nodes: dict, edges: list[dict] | None = None, *, sv: int = 1) -> dict:
    return {
        "meta": {"version": SCHEMA_VERSION, "updated_at": _TS, "schema_version": sv},
        "nodes": nodes, "edges": edges or [],
    }


def _write_graph(repo_root: Path, payload: dict) -> None:
    weld_dir = repo_root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    (weld_dir / "graph.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _file_node(fp: str) -> dict:
    return {"type": "file", "label": fp, "props": {"file": fp, "language": "python"}}


def _sym_node(qual: str, fp: str) -> dict:
    return {"type": "symbol", "label": qual.split(".")[-1],
            "props": {"qualname": qual, "file": fp, "language": "python"}}


def _init_child(repo_root: Path, sources: list[str], payload: dict) -> Path:
    """git-init *repo_root*, commit *sources* + the graph, return the dir.

    Committing ``.weld/graph.json`` alongside the sources keeps the working
    tree clean, so a later single-file edit is the only thing ``git status`` /
    ``git diff`` report (no untracked bookkeeping noise in the assertions).
    """
    repo_root.mkdir(parents=True, exist_ok=True)
    _git(repo_root, "init", "-q")
    _git(repo_root, "config", "user.email", "test@example.com")
    _git(repo_root, "config", "user.name", "Weld Test")
    for rel in sources:
        p = repo_root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("original\n", encoding="utf-8")
    _write_graph(repo_root, payload)
    _git(repo_root, "add", "-A")
    _git(repo_root, "commit", "-q", "-m", "init")
    return repo_root


def _build_workspace(root: Path) -> dict:
    """Non-git root + two child git repos that SHARE ``src/app.py``.

    child ``svc-a`` lives at a *nested* path (``services/svc-a``) so name != path
    -- this pins that the git fan-out shells out in the child *path* while the
    resolved seed ids are prefixed with the child *name* (the id-space the
    flattened graph uses). child ``child-b`` carries an identically-named
    ``src/app.py``; a change in ``svc-a`` must seed only ``svc-a``.
    """
    a = _init_child(
        root / "services" / "svc-a",
        ["src/app.py", "src/caller.py"],
        _graph_payload(
            {
                "file:src/app.py": _file_node("src/app.py"),
                "symbol:py:a:caller": _sym_node("a.caller", "src/caller.py"),
            },
            edges=[{"from": "symbol:py:a:caller", "to": "file:src/app.py",
                    "type": "depends_on", "props": {}}],
        ),
    )
    _init_child(
        root / "child-b",
        ["src/app.py", "src/other.py"],
        _graph_payload(
            {
                "file:src/app.py": _file_node("src/app.py"),
                "symbol:py:b:other": _sym_node("b.other", "src/other.py"),
            },
            edges=[{"from": "symbol:py:b:other", "to": "file:src/app.py",
                    "type": "depends_on", "props": {}}],
        ),
    )
    # Root meta-graph: repo nodes only, schema_version=2. Root is NOT git.
    _write_graph(root, _graph_payload(
        {
            "repo:svc-a": {"type": "repo", "label": "svc-a",
                           "props": {"path": "services/svc-a"}},
            "repo:child-b": {"type": "repo", "label": "child-b",
                             "props": {"path": "child-b"}},
        },
        [], sv=2,
    ))
    dump_workspaces_yaml(
        WorkspaceConfig(
            children=[ChildEntry(name="svc-a", path="services/svc-a"),
                      ChildEntry(name="child-b", path="child-b")],
            cross_repo_strategies=[]),
        root / ".weld" / "workspaces.yaml")
    return {
        "a_dir": a,
        "a_path": "services/svc-a",
        "a_file": prefix_node_id("svc-a", "file:src/app.py"),
        "a_caller": prefix_node_id("svc-a", "symbol:py:a:caller"),
        "b_file": prefix_node_id("child-b", "file:src/app.py"),
        "b_caller": prefix_node_id("child-b", "symbol:py:b:other"),
    }


def _run_impact_json(argv: list[str]) -> dict:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = impact_main([*argv, "--allow-stale", "--no-refresh", "--json"])
    assert rc == 0, f"impact exited {rc}"
    return json.loads(buf.getvalue())


class WorkingTreeFederationTest(unittest.TestCase):
    """``--working-tree`` at a non-git polyrepo root fans out to children."""

    def test_working_tree_seeds_only_the_changed_child(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            ids = _build_workspace(root)
            # Dirty ONLY child-a's shared-name file (tracked -> shows in status).
            (ids["a_dir"] / "src" / "app.py").write_text("changed\n", encoding="utf-8")

            result = _run_impact_json(["--root", str(root), "--working-tree"])

            self.assertEqual(result["target"]["kind"], "working-tree")
            resolved = set(result["target"]["resolved_nodes"])
            self.assertIn(ids["a_file"], resolved)
            # Disambiguation: child-b's identically-named file must NOT resolve.
            self.assertNotIn(ids["b_file"], resolved)

            direct = {d["id"] for d in result["direct_dependents"]}
            self.assertIn(ids["a_caller"], direct)   # child-internal dependent
            self.assertNotIn(ids["b_caller"], direct)

    def test_working_tree_is_deterministic(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            ids = _build_workspace(root)
            (ids["a_dir"] / "src" / "app.py").write_text("changed\n", encoding="utf-8")
            first = _run_impact_json(["--root", str(root), "--working-tree"])
            second = _run_impact_json(["--root", str(root), "--working-tree"])
            self.assertEqual(
                json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))


class FromDiffFederationTest(unittest.TestCase):
    """``--from-diff <ref>`` fans out per child; a ref absent in a child skips it."""

    def test_from_diff_seeds_from_child_history(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            ids = _build_workspace(root)
            a = ids["a_dir"]
            base = _rev_parse(a)             # exists ONLY in child-a's history
            (a / "src" / "app.py").write_text("changed\n", encoding="utf-8")
            _git(a, "add", "src/app.py")
            _git(a, "commit", "-q", "-m", "change app")

            result = _run_impact_json(["--root", str(root), "--from-diff", base])

            self.assertEqual(result["target"]["kind"], "from-diff")
            resolved = set(result["target"]["resolved_nodes"])
            self.assertIn(ids["a_file"], resolved)
            # child-b lacks this SHA -> tolerant skip, no false seed.
            self.assertNotIn(ids["b_file"], resolved)
            direct = {d["id"] for d in result["direct_dependents"]}
            self.assertIn(ids["a_caller"], direct)

    def test_from_diff_display_paths_are_child_prefixed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            ids = _build_workspace(root)
            a = ids["a_dir"]
            base = _rev_parse(a)
            (a / "src" / "app.py").write_text("changed\n", encoding="utf-8")
            _git(a, "add", "src/app.py")
            _git(a, "commit", "-q", "-m", "change app")

            result = _run_impact_json(["--root", str(root), "--from-diff", base])
            # target.input echoes the changed paths, prefixed by the child dir
            # (name != path here) so the origin child is unambiguous.
            self.assertIn("services/svc-a/src/app.py", result["target"]["input"])


if __name__ == "__main__":
    unittest.main()
