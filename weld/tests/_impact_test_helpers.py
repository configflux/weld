"""Shared fixtures for ``weld.impact`` tests (Layer A).

Pulled out of the test files to keep them under the 400-line cap. Provides
a synthetic graph rich enough to exercise the tests bucket, the
unresolved-callsite count, the speculative-edge count, and the
low-capability-input warning, plus a tiny git-repo helper for the
``--from-diff`` / ``--working-tree`` / stale-gate paths.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from weld.contract import SCHEMA_VERSION

_NODES: dict[str, dict] = {
    "file:weld/graph.py": {
        "type": "file",
        "label": "weld/graph.py",
        "props": {"file": "weld/graph.py", "language": "python"},
    },
    "symbol:py:weld.graph:Graph.query": {
        "type": "symbol",
        "label": "Graph.query",
        "props": {
            "file": "weld/graph.py",
            "module": "weld.graph",
            "qualname": "Graph.query",
            "language": "python",
        },
    },
    "command:wd query": {
        "type": "command",
        "label": "wd query",
        "props": {"file": "weld/cli.py"},
    },
    "file:weld/tests/weld_graph_test.py": {
        "type": "file",
        "label": "weld_graph_test.py",
        "props": {
            "file": "weld/tests/weld_graph_test.py",
            "role": "test",
            "language": "python",
        },
    },
    "symbol:unresolved:print": {
        "type": "symbol",
        "label": "print",
        "props": {"resolved": False},
    },
    "file:weld/utils.py": {
        # File-only node, has no calls/tests/depends_on edges incident.
        # Used to validate the low-capability-inputs warning.
        "type": "file",
        "label": "weld/utils.py",
        "props": {"file": "weld/utils.py", "language": "python"},
    },
}

_EDGES: list[dict] = [
    {
        "from": "command:wd query",
        "to": "symbol:py:weld.graph:Graph.query",
        "type": "calls",
        "props": {},
    },
    {
        "from": "file:weld/tests/weld_graph_test.py",
        "to": "symbol:py:weld.graph:Graph.query",
        "type": "tests",
        "props": {},
    },
    {
        "from": "symbol:py:weld.graph:Graph.query",
        "to": "symbol:unresolved:print",
        "type": "calls",
        "props": {"resolution": "builtin", "confidence": "speculative"},
    },
]


def write_graph(
    root: Path,
    *,
    sha: str = "deadbeef",
    discovered_from: list[str] | None = None,
) -> None:
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    meta: dict[str, object] = {
        "version": SCHEMA_VERSION,
        "git_sha": sha,
        "updated_at": "2026-04-13T00:00:00+00:00",
    }
    if discovered_from is not None:
        meta["discovered_from"] = discovered_from
    (root / ".weld" / "graph.json").write_text(
        json.dumps({"meta": meta, "nodes": _NODES, "edges": _EDGES}),
        encoding="utf-8",
    )


def new_root() -> Path:
    root = Path(tempfile.mkdtemp())
    write_graph(root)
    return root


def git(args: list[str], *, cwd: Path) -> None:
    env = os.environ.copy()
    env.setdefault("GIT_AUTHOR_NAME", "test")
    env.setdefault("GIT_AUTHOR_EMAIL", "test@example.com")
    env.setdefault("GIT_COMMITTER_NAME", "test")
    env.setdefault("GIT_COMMITTER_EMAIL", "test@example.com")
    subprocess.run(
        ["git", *args], cwd=str(cwd), env=env, check=True, capture_output=True,
    )


def make_git_repo(root: Path) -> None:
    """Initialise *root* as a git repo with an initial commit on ``main``."""
    git(["init", "-b", "main"], cwd=root)
    (root / "README.md").write_text("hi\n", encoding="utf-8")
    git(["add", "README.md"], cwd=root)
    git(["commit", "-m", "init"], cwd=root)


def run_cli(argv: list[str]) -> tuple[int, str, str]:
    """Invoke ``weld.impact_cli.main`` and capture exit code, stdout, stderr.

    ``SystemExit("msg")`` carries the message in ``exc.code`` rather than
    on the stderr stream, mirroring CPython's normal exit behaviour. We
    funnel that string into the captured stderr so tests can assert
    against the user-visible message in either case.
    """
    from weld.impact_cli import main as impact_main

    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    code = 0
    try:
        with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
            rc = impact_main(argv)
        if isinstance(rc, int):
            code = rc
    except SystemExit as exc:
        rc_val = exc.code
        if rc_val is None:
            code = 0
        elif isinstance(rc_val, int):
            code = rc_val
        else:
            text = str(rc_val)
            if not text.endswith("\n"):
                text += "\n"
            stderr_buf.write(text)
            code = 1
    return code, stdout_buf.getvalue(), stderr_buf.getvalue()


def ensure_repo_root_on_syspath() -> None:
    """Tests import this helper before they depend on the ``weld`` package.

    Bazel's runfiles tree exposes ``weld`` directly, so this is a no-op there.
    Running ``python weld/tests/weld_impact_cli_test.py`` from a checkout
    needs the repo root prepended.
    """
    repo_root = str(Path(__file__).resolve().parent.parent.parent)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)


__all__ = [
    "ensure_repo_root_on_syspath",
    "git",
    "make_git_repo",
    "new_root",
    "run_cli",
    "unittest",
    "write_graph",
]
