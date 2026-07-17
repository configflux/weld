"""Shared fixtures for the federation descent tests (ADR 0081 / ADR 0091).

Builds the on-disk polyrepo scaffolding the descent tests read: a git-backed
child repo with a ``.weld/graph.json``, a root ``workspaces.yaml``, and a root
``graph.json`` of ``repo:<name>`` meta-nodes. Extracted into a ``_*.py`` module
(listed in each test target's ``srcs``) so both
``weld_federation_descent_test.py`` and ``weld_federation_descent_cycle_test.py``
share one copy without either file breaching the 400-line cap.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from weld.contract import SCHEMA_VERSION
from weld.workspace import ChildEntry, WorkspaceConfig, dump_workspaces_yaml

_TS = "2026-07-08T00:00:00+00:00"


def _git(repo_root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo_root), capture_output=True, text=True, check=True)


def _init_repo(repo_root: Path) -> Path:
    repo_root.mkdir(parents=True, exist_ok=True)
    _git(repo_root, "init", "-q")
    _git(repo_root, "config", "user.email", "test@example.com")
    _git(repo_root, "config", "user.name", "Weld Test")
    (repo_root / "README.md").write_text("# fixture\n", encoding="utf-8")
    _git(repo_root, "add", "README.md")
    _git(repo_root, "commit", "-q", "-m", "initial commit")
    return repo_root


def _child_payload(nodes: dict, edges: list[dict] | None = None) -> dict:
    return {
        "meta": {"version": SCHEMA_VERSION, "updated_at": _TS, "schema_version": 1},
        "nodes": nodes,
        "edges": edges or [],
    }


def _write_graph(repo_root: Path, payload: dict) -> None:
    weld_dir = repo_root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    (weld_dir / "graph.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )


def _write_workspaces(root: Path, children: list[ChildEntry]) -> None:
    config = WorkspaceConfig(children=children, cross_repo_strategies=[])
    dump_workspaces_yaml(config, root / ".weld" / "workspaces.yaml")


def _write_root_graph(
    root: Path, children: list[str], edges: list[dict] | None = None,
) -> None:
    nodes = {
        f"repo:{name}": {"type": "repo", "label": name, "props": {"path": name}}
        for name in children
    }
    _write_graph(
        root,
        {
            "meta": {"version": SCHEMA_VERSION, "updated_at": _TS, "schema_version": 2},
            "nodes": nodes,
            "edges": edges or [],
        },
    )
