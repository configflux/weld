"""Real checkouts, a real discover, a real read -- shared by the seed suites.

Everything the ADR 0096 §2 seeding tests do to a repository, they do for
real: ``git init`` / ``clone`` / ``worktree add`` in a temp directory with
their own identity, a real ``wd discover``, and a real graph-backed read
CLI. The claim under test is that pure git plumbing bootstraps *any*
layout, so a mocked repository would only test the mock.

Mode-specific setup lives next door: :mod:`_mode_b_fixture` builds the
``--track-graphs`` repository (gates 1-4), :mod:`_mode_a_fixture` the
default gitignored-graph one (gate 5). Only the plumbing they share is
here.
"""

from __future__ import annotations

import io
import json
import subprocess
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

# Hermetic git: own identity, no ambient user config, stable locale.
# Mirrors weld_git_worktree_test / discover_worktree_canonical_graph_test.
GIT_ENV = {"LC_ALL": "C", "PATH": "/usr/bin:/usr/local/bin:/bin"}

#: The one-source-file project every fixture repo discovers. Small on
#: purpose: these suites assert on *which* checkout answered, never on
#: extraction depth.
DISCOVER_YAML = (
    "sources:\n"
    '  - glob: "*.py"\n'
    "    type: symbol\n"
    "    strategy: python_module\n"
)

#: The gitignored volatile-meta sidecar whose absence is the whole bug.
SIDECAR = "graph-meta.json"


def git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        env=GIT_ENV,
        check=True,
    )
    return proc.stdout.strip()


def init_repo(root: Path, gitignore: str) -> None:
    """Create a git repo at *root* holding one source file and *gitignore*.

    Stops just before ``wd discover``: what each mode does with the graph
    afterwards -- commit it (Mode B) or leave it ignored (Mode A) -- is
    exactly the difference the two fixtures encode.
    """
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Weld Test")
    git(root, "checkout", "-q", "-b", "main")
    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    (weld_dir / "discover.yaml").write_text(DISCOVER_YAML, encoding="utf-8")
    (weld_dir / ".gitignore").write_text(gitignore, encoding="utf-8")
    (root / "alpha.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")


def commit_all(root: Path, message: str) -> None:
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message)


def discover(root: Path) -> None:
    """Run a real ``wd discover`` into *root*, quietly."""
    from weld.discover import main as discover_main

    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = discover_main([str(root), "--no-enrich"])
    if rc != 0:
        raise AssertionError(f"fixture discover failed: {err.getvalue()}")


def read(root: Path, term: str = "alpha", *flags: str) -> str:
    """Drive a real graph-backed read CLI against *root*; return its stderr.

    Trailing *flags* are appended verbatim, which is how a suite drives
    the same read under an opt-out such as ``--no-refresh``. This helper
    asserts the read *succeeded*; a case that expects the first-run
    guidance and its nonzero exit needs its own runner.
    """
    from weld.cli import main as cli_main

    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            cli_main(["--root", str(root), "query", term, "--json", *flags])
        except SystemExit as exc:  # first-run guidance exits non-zero
            if exc.code:
                raise AssertionError(f"read failed: {err.getvalue()}") from exc
    return err.getvalue()


def wrapped_discover():
    """The real ``_discover_single_repo``, for ``mock.patch(wraps=...)``."""
    from weld.discover import _discover_single_repo

    return _discover_single_repo


def graph_nodes(root: Path) -> set[str]:
    """Node ids in *root*'s graph -- the observable "whose content is this"."""
    graph = json.loads((root / ".weld" / "graph.json").read_text(encoding="utf-8"))
    return set(graph.get("nodes") or {})


def sidecar(root: Path) -> dict:
    return json.loads((root / ".weld" / SIDECAR).read_text(encoding="utf-8"))


def stale_info(root: Path) -> dict:
    """Freshness exactly as the read path computes it."""
    from weld._graph_meta_sidecar import read_meta_for_staleness
    from weld._staleness import compute_stale_info

    graph_path = root / ".weld" / "graph.json"
    return compute_stale_info(graph_path, read_meta_for_staleness(graph_path) or {})


def weld_listing(root: Path) -> set[str]:
    """Every name currently in ``.weld/`` -- the write-detection surface."""
    return {p.name for p in (root / ".weld").iterdir()}
