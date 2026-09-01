"""Real-git workspace fixtures for the ADR 0066 child-staleness surfaces.

Two suites drive the same two user surfaces -- ``wd stale`` and ``wd
workspace status`` -- against real git repositories rather than stubbed
``git``: the staleness surfacing tests
(``weld_federation_child_staleness_surface_test``) and the ledger-drift tests
(``weld_workspace_status_drift_test``). Both need a workspace whose children
are genuinely checked out, genuinely committed, and whose graphs carry a
genuine discovered-from SHA, because that is the only way the oracle's git
tiers are exercised at all.

Extracted verbatim from the surfacing suite (ADR 0138, bd ...grxpi) so the
drift suite reuses the same workspace rather than growing a second, subtly
different one -- and so the surfacing suite stays under the 400-line cap.
"""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from weld.cli import main as cli_main
from weld.discover import discover
from weld.workspace import ChildEntry, WorkspaceConfig, dump_workspaces_yaml

__all__ = [
    "_git",
    "_init_repo",
    "_write_child_graph",
    "_commit",
    "_seed_root",
    "_run_cli",
]


def _git(repo_root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        env={"LC_ALL": "C", "PATH": "/usr/bin:/usr/local/bin:/bin"},
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
    _git(repo_root, "commit", "-q", "-m", "initial commit")
    return repo_root


def _write_child_graph(repo_root: Path, *, at_head: bool = True) -> None:
    """Write a child ``graph.json`` plus (optionally) its sidecar at HEAD.

    The discovered-from SHA lives in the ADR 0065 sidecar, matching the real
    write path. ``at_head=False`` omits the sidecar (fresh-clone shape).
    """
    weld_dir = repo_root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {"version": 1, "schema_version": 1, "discovered_from": ["."]},
        "nodes": {},
        "edges": [],
    }
    (weld_dir / "graph.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    if at_head:
        head = _git(repo_root, "rev-parse", "HEAD")
        (weld_dir / "graph-meta.json").write_text(
            json.dumps({"version": 1, "git_sha": head}, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def _commit(repo_root: Path, name: str = "feature.py") -> str:
    (repo_root / name).write_text("x = 1\n", encoding="utf-8")
    _git(repo_root, "add", name)
    _git(repo_root, "commit", "-q", "-m", f"add {name}")
    return _git(repo_root, "rev-parse", "HEAD")


def _seed_root(root: Path, children: list[ChildEntry]) -> None:
    """Commit a workspaces.yaml at *root* and federate-discover once.

    ``write_root_graph=True`` mirrors the real ``wd discover`` at a
    federated root: the meta-graph is written to ``.weld/graph.json`` (with
    its git_sha stamped to HEAD) so the root's own ``wd stale`` check is
    fresh and isolates child staleness from a missing root graph.
    """
    _init_repo(root)
    dump_workspaces_yaml(
        WorkspaceConfig(children=children, cross_repo_strategies=[]),
        root / ".weld" / "workspaces.yaml",
    )
    _git(root, "add", ".weld/workspaces.yaml")
    _git(root, "commit", "-q", "-m", "add workspaces.yaml")
    discover(root, incremental=False, write_root_graph=True)


def _run_cli(*argv: str) -> str:
    stdout = io.StringIO()
    with patch("sys.stdout", stdout):
        code = cli_main(list(argv))
    assert code == 0, f"cli {argv} exited {code}: {stdout.getvalue()}"
    return stdout.getvalue()

