"""Shared fixtures for ADR 0058 federation-rewire tests.

Two test files use these helpers (basic federation-sqlite path and the
handle-cache/memory probes); keeping them here lets each test file
stay within the 400-line cap and avoids duplicating fixture code.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from weld import _sqlite_writer as writer
from weld.contract import SCHEMA_VERSION
from weld.serializer import dumps_graph
from weld.workspace import ChildEntry, WorkspaceConfig, dump_workspaces_yaml

_TS = "2026-04-15T20:30:00+00:00"


__all__ = [
    "child_graph",
    "git",
    "graph_payload",
    "init_repo",
    "make_workspace",
    "write_child",
    "write_child_with_sidecar",
    "write_root_graph",
    "write_workspaces",
]


def child_graph(label: str) -> dict:
    """Return a minimal one-node child graph payload."""
    return {
        "meta": {"schema_version": 1},
        "nodes": {
            f"service:{label}": {
                "type": "service",
                "label": label,
                "props": {"file": f"{label}/service.py"},
            },
        },
        "edges": [],
    }


def write_child(root: Path, name: str, label: str) -> tuple[Path, Path]:
    """Write a child graph.json + fresh sidecar; return their paths."""
    child_root = root / name
    weld_dir = child_root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    graph_path = weld_dir / "graph.json"
    graph = child_graph(label)
    body = dumps_graph(graph).encode("utf-8")
    graph_path.write_bytes(body)
    db_path = weld_dir / "graph.db"
    writer.build_sidecar_for_bytes(graph, body, db_path, generated_at="t")
    return graph_path, db_path


def git(repo_root: Path, *args: str) -> str:
    """Run a git command in *repo_root*; return stdout."""
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def init_repo(repo_root: Path) -> Path:
    """Initialise a one-commit fixture repo."""
    repo_root.mkdir(parents=True, exist_ok=True)
    git(repo_root, "init", "-q")
    git(repo_root, "config", "user.email", "test@example.com")
    git(repo_root, "config", "user.name", "Weld Test")
    (repo_root / "README.md").write_text("# fixture\n", encoding="utf-8")
    git(repo_root, "add", "README.md")
    git(repo_root, "commit", "-q", "-m", "initial commit")
    return repo_root


def graph_payload(nodes: dict, edges: list[dict] | None = None) -> dict:
    """Build a schema_version=1 child graph payload."""
    return {
        "meta": {
            "version": SCHEMA_VERSION,
            "updated_at": _TS,
            "schema_version": 1,
        },
        "nodes": nodes,
        "edges": edges or [],
    }


def write_child_with_sidecar(
    repo_root: Path, payload: dict, *, with_sidecar: bool = True,
) -> tuple[Path, Path | None]:
    """Write child graph.json + optional fresh sidecar.

    Returns the graph.json path and the db path (or None).
    """
    weld_dir = repo_root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    graph_path = weld_dir / "graph.json"
    body = dumps_graph(payload).encode("utf-8")
    graph_path.write_bytes(body)
    if not with_sidecar:
        return graph_path, None
    db_path = weld_dir / "graph.db"
    writer.build_sidecar_for_bytes(payload, body, db_path, generated_at="t")
    return graph_path, db_path


def write_workspaces(root: Path, children: list[ChildEntry]) -> None:
    """Write .weld/workspaces.yaml listing *children*."""
    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    config = WorkspaceConfig(children=children, cross_repo_strategies=[])
    dump_workspaces_yaml(config, weld_dir / "workspaces.yaml")


def write_root_graph(root: Path, children: list[str]) -> None:
    """Write the root federated graph.json that pins repo:<name> nodes."""
    nodes = {
        f"repo:{name}": {
            "type": "repo",
            "label": name,
            "props": {"path": name},
        }
        for name in children
    }
    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    payload = graph_payload(nodes)
    payload["meta"]["schema_version"] = 2  # federated root
    (weld_dir / "graph.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def make_workspace(
    root: Path,
    *,
    children: list[tuple[str, dict, bool]],
) -> None:
    """Build a federated workspace at *root* with the supplied children.

    Each child tuple is ``(name, payload, with_sidecar)``.
    """
    child_entries: list[ChildEntry] = []
    for name, payload, with_sidecar in children:
        child_dir = root / name
        init_repo(child_dir)
        write_child_with_sidecar(child_dir, payload, with_sidecar=with_sidecar)
        child_entries.append(ChildEntry(name=name, path=name))
    write_workspaces(root, child_entries)
    write_root_graph(root, [name for name, _, _ in children])
