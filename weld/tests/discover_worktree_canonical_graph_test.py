"""Regression: a bare ``wd discover`` must leave ``.weld/graph.json`` on disk.

Reproduces the linked-worktree dogfood gap (bd ck0w): inside a
fresh ``.weld/`` (a linked git worktree, or any fresh checkout that has never
run an explicit ``wd discover --output .weld/graph.json``), a bare
``wd discover`` returned exit 0 and wrote every *derived* sidecar keyed to the
canonical graph -- ``graph.db`` (ADR 0058), ``query_state.bin`` (ADR 0031),
``file-index.json`` -- but **not** ``.weld/graph.json`` itself (the canonical
JSON was sent to stdout). Every graph read (``query`` / ``context`` / ``stats``)
then resolved 0 nodes because ``Graph.open`` keys off ``.weld/graph.json`` and
the orphaned sidecars cannot be validated against a JSON that never landed.

These are black-box tests against the CLI entry point
(:func:`weld.discover.main`) plus the read surface (:meth:`weld.graph.Graph.open`)
-- no mocks. The fix (persist ``.weld/graph.json`` for a bare single-repo
discover, coherent with the sidecars ``finalize_single_repo`` already wrote)
must satisfy all of them while preserving the ADR 0019 stdout contract.
"""

from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from weld.discover import main as discover_main
from weld.graph import Graph


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


def _seed_python_repo(repo_root: Path) -> None:
    """Init a git repo whose *committed* tree carries a discoverable source.

    ``.weld/discover.yaml`` and ``hello.py`` are committed so a linked worktree
    checks them out; ``.weld/graph.json`` is never created here, mirroring a
    fresh checkout that has not yet run an explicit ``--output`` discover.
    """
    repo_root.mkdir(parents=True, exist_ok=True)
    _git(repo_root, "init", "-q")
    _git(repo_root, "config", "user.email", "test@example.com")
    _git(repo_root, "config", "user.name", "Weld Test")
    (repo_root / ".weld").mkdir(parents=True, exist_ok=True)
    (repo_root / ".weld" / "discover.yaml").write_text(
        'sources:\n'
        '  - glob: "*.py"\n'
        '    type: symbol\n'
        '    strategy: python_module\n',
        encoding="utf-8",
    )
    (repo_root / "hello.py").write_text(
        "def greet():\n    return 'hello'\n", encoding="utf-8"
    )
    _git(repo_root, "add", ".weld/discover.yaml", "hello.py")
    _git(repo_root, "commit", "-q", "-m", "seed discover config + source")


def _run_bare_discover(root: Path) -> tuple[int, str, str]:
    """Invoke ``wd discover <root>`` (no --output) capturing stdout/stderr."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = discover_main([str(root), "--no-enrich"])
    return rc, out.getvalue(), err.getvalue()


def _resolved_node_ids(root: Path) -> set[str]:
    """Node ids the read surface resolves for *root* after discovery."""
    graph = Graph.open(root)
    try:
        return {n["id"] for n in graph.list_nodes()}
    finally:
        close = getattr(graph, "close", None)
        if callable(close):
            close()


class BareDiscoverPersistsCanonicalGraphTests(unittest.TestCase):
    """A bare single-repo ``wd discover`` leaves a queryable ``.weld/``."""

    def test_bare_discover_writes_graph_json_and_resolves_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            _seed_python_repo(root)
            graph_json = root / ".weld" / "graph.json"
            self.assertFalse(
                graph_json.exists(),
                "precondition: fresh checkout has no canonical graph.json",
            )

            rc, stdout, _stderr = _run_bare_discover(root)

            self.assertEqual(rc, 0)
            # The fix: the canonical store must land on disk, not only stdout.
            self.assertTrue(
                graph_json.is_file(),
                "bare `wd discover` must persist .weld/graph.json so the "
                "connected-structure read surface works",
            )
            # The read surface must now resolve the discovered source (before
            # the fix Graph.open sees no graph.json and returns 0 nodes).
            ids = _resolved_node_ids(root)
            self.assertTrue(ids, "graph read resolved 0 nodes after discover")
            self.assertTrue(
                any("hello" in nid for nid in ids),
                f"discovered source 'hello' absent from resolved nodes: {ids}",
            )
            # ADR 0019 contract preserved: stdout still carries the graph JSON.
            self.assertGreater(len(stdout), 0, "stdout graph JSON must persist")
            self.assertIn("nodes", json.loads(stdout))

    def test_sqlite_sidecar_is_coherent_with_written_graph_json(self) -> None:
        """The sidecar ``finalize`` wrote must validate against the new JSON.

        Guards ADR 0058: if the persisted ``graph.json`` bytes drift from the
        bytes the sidecar was hashed against, ``sidecar_freshness`` rejects the
        cache. A coherent write means the fast path is actually usable (not
        just that JSON fallback masks the gap).
        """
        from weld._sqlite_reader import sidecar_freshness

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            _seed_python_repo(root)

            rc, _stdout, _stderr = _run_bare_discover(root)
            self.assertEqual(rc, 0)

            fresh, _meta = sidecar_freshness(root / ".weld" / "graph.json")
            self.assertTrue(
                fresh,
                "graph.db must be coherent with the graph.json a bare "
                "discover writes (ADR 0058 source_json_sha match)",
            )


class BareDiscoverInLinkedWorktreeTests(unittest.TestCase):
    """The exact bug scenario: a real linked git worktree."""

    def test_worktree_discover_resolves_nodes_without_cross_leakage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            main_repo = Path(tmp) / "main"
            _seed_python_repo(main_repo)
            worktree = Path(tmp) / "wt"
            _git(main_repo, "worktree", "add", "-q", str(worktree), "HEAD")

            wt_graph = worktree / ".weld" / "graph.json"
            main_graph = main_repo / ".weld" / "graph.json"
            self.assertTrue(
                (worktree / ".weld" / "discover.yaml").is_file(),
                "linked worktree must check out the tracked discover.yaml",
            )
            self.assertFalse(
                wt_graph.exists(),
                "precondition: fresh linked worktree has no graph.json",
            )

            rc, _stdout, _stderr = _run_bare_discover(worktree)
            self.assertEqual(rc, 0)

            # Fix: the worktree's own canonical graph is written and queryable.
            self.assertTrue(
                wt_graph.is_file(),
                "bare discover in a linked worktree must write its graph.json",
            )
            ids = _resolved_node_ids(worktree)
            self.assertTrue(
                ids, "worktree graph read resolved 0 nodes after discover"
            )
            self.assertTrue(
                any("hello" in nid for nid in ids),
                f"worktree query resolved no discovered nodes: {ids}",
            )
            # Security: no cross-worktree graph leakage -- discovering in the
            # worktree must not create/populate the main checkout's graph.json.
            self.assertFalse(
                main_graph.exists(),
                "worktree discover leaked a graph.json into the main checkout",
            )


if __name__ == "__main__":
    unittest.main()
