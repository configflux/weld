"""Federated ``wd impact`` ``warnings.low_capability_inputs`` for child seeds.

ADR 0089 fanned the git-seeded modes (``--from-diff`` / ``--working-tree``) out
per child and gave each resolved seed a child-dir-prefixed *display* path
(``services/svc/src/app.py``) for provenance. The low-capability diagnostic,
however, matched every input path against the FLATTENED union graph, where a
child keeps its child-relative ``props.file`` (``src/app.py``). A child-prefixed
display path therefore never matched, so ``warnings.low_capability_inputs``
stayed empty for child seeds even when the seed had only file-level evidence.

These tests lock the fix: low-capability is computed per child (each child's
own paths against that child's own graph) and re-noted with the child prefix,
mirroring how the seed fan-out itself resolves per child. A genuinely
low-capability child seed now surfaces (child-prefixed); a well-covered child
seed does not. A single-repo run is asserted byte-identical (the un-prefixed
flattened-match path is unchanged).

Fixture style follows ``weld_impact_federation_git_seeds_test``: a real
``git init`` per repo plus a hand-shaped ``.weld/graph.json``.
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


def _init_repo(repo_root: Path, sources: list[str], payload: dict) -> Path:
    """git-init *repo_root*, commit *sources* + the graph, return the dir."""
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


def _covered_child_payload() -> dict:
    """One low-capability file (no edges) + one well-covered file (a dependent).

    ``src/lonely.py`` has only its ``file:`` node -- file-level evidence only,
    so it is low-capability. ``src/covered.py`` is the target of a ``depends_on``
    edge, so it has non-file-level evidence and must NOT be flagged.
    """
    return _graph_payload(
        {
            "file:src/lonely.py": _file_node("src/lonely.py"),
            "file:src/covered.py": _file_node("src/covered.py"),
            "symbol:py:svc:caller": _sym_node("svc.caller", "src/caller.py"),
        },
        edges=[{"from": "symbol:py:svc:caller", "to": "file:src/covered.py",
                "type": "depends_on", "props": {}}],
    )


def _build_workspace(root: Path) -> dict:
    """Non-git root + one child git repo at a NESTED path (name != path).

    The nested path (``services/svc`` for child ``svc``) pins that the child
    prefix used for the low-capability display path is the child *path*, not the
    child *name* -- the same prefix the seed fan-out uses.
    """
    svc = _init_repo(
        root / "services" / "svc",
        ["src/lonely.py", "src/covered.py", "src/caller.py"],
        _covered_child_payload(),
    )
    _write_graph(root, _graph_payload(
        {"repo:svc": {"type": "repo", "label": "svc",
                      "props": {"path": "services/svc"}}},
        [], sv=2,
    ))
    dump_workspaces_yaml(
        WorkspaceConfig(
            children=[ChildEntry(name="svc", path="services/svc")],
            cross_repo_strategies=[]),
        root / ".weld" / "workspaces.yaml")
    return {
        "svc_dir": svc,
        "lonely_display": "services/svc/src/lonely.py",
        "covered_display": "services/svc/src/covered.py",
        "lonely_seed": prefix_node_id("svc", "file:src/lonely.py"),
        "covered_seed": prefix_node_id("svc", "file:src/covered.py"),
    }


def _run_impact_json(argv: list[str]) -> dict:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = impact_main([*argv, "--allow-stale", "--no-refresh", "--json"])
    assert rc == 0, f"impact exited {rc}"
    return json.loads(buf.getvalue())


class FederatedLowCapabilityTest(unittest.TestCase):
    """Child-prefixed low-capability seeds surface; covered ones do not."""

    def test_working_tree_low_capability_child_seed_surfaces(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            ids = _build_workspace(root)
            # Dirty BOTH the low-capability and the well-covered child file.
            (ids["svc_dir"] / "src" / "lonely.py").write_text("x\n", encoding="utf-8")
            (ids["svc_dir"] / "src" / "covered.py").write_text("x\n", encoding="utf-8")

            result = _run_impact_json(["--root", str(root), "--working-tree"])

            # Both child seeds resolved (child-prefixed ids in the union).
            resolved = set(result["target"]["resolved_nodes"])
            self.assertIn(ids["lonely_seed"], resolved)
            self.assertIn(ids["covered_seed"], resolved)

            low = result["warnings"]["low_capability_inputs"]
            # The low-capability seed surfaces with its child-prefixed path...
            self.assertEqual(low, [ids["lonely_display"]])
            # ...and the well-covered seed is NOT flagged.
            self.assertNotIn(ids["covered_display"], low)

    def test_working_tree_low_capability_is_deterministic(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            ids = _build_workspace(root)
            (ids["svc_dir"] / "src" / "lonely.py").write_text("x\n", encoding="utf-8")
            first = _run_impact_json(["--root", str(root), "--working-tree"])
            second = _run_impact_json(["--root", str(root), "--working-tree"])
            self.assertEqual(
                first["warnings"]["low_capability_inputs"],
                second["warnings"]["low_capability_inputs"],
            )

    def test_from_diff_low_capability_child_seed_surfaces(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            ids = _build_workspace(root)
            svc = ids["svc_dir"]
            base = _rev_parse(svc)
            (svc / "src" / "lonely.py").write_text("changed\n", encoding="utf-8")
            _git(svc, "add", "src/lonely.py")
            _git(svc, "commit", "-q", "-m", "change lonely")

            result = _run_impact_json(["--root", str(root), "--from-diff", base])

            self.assertEqual(result["target"]["kind"], "from-diff")
            self.assertEqual(
                result["warnings"]["low_capability_inputs"],
                [ids["lonely_display"]],
            )


class SingleRepoLowCapabilityUnchangedTest(unittest.TestCase):
    """No ``workspaces.yaml`` -> the un-prefixed flattened-match path is intact."""

    def test_single_repo_working_tree_low_capability_unchanged(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            # A single git repo (root IS the git repo; NO workspaces.yaml).
            _init_repo(
                root,
                ["app.py", "lib.py", "caller.py"],
                _graph_payload({
                    "file:app.py": _file_node("app.py"),
                    "file:lib.py": _file_node("lib.py"),
                    "symbol:py:m:caller": _sym_node("m.caller", "caller.py"),
                }, edges=[{"from": "symbol:py:m:caller", "to": "file:lib.py",
                           "type": "depends_on", "props": {}}]),
            )
            (root / "app.py").write_text("x\n", encoding="utf-8")
            (root / "lib.py").write_text("x\n", encoding="utf-8")

            result = _run_impact_json(["--root", str(root), "--working-tree"])

            low = result["warnings"]["low_capability_inputs"]
            # Un-prefixed (single repo), covered file excluded -- unchanged path.
            self.assertEqual(low, ["app.py"])
            self.assertNotIn("lib.py", low)


if __name__ == "__main__":
    unittest.main()
