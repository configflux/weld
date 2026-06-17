"""Recurse-written child ``graph.json`` is byte-stable at a fixed commit.

ADR 0065 relocates the two volatile ``meta`` fields (``updated_at`` /
``git_sha``) out of ``graph.json`` into a gitignored ``graph-meta.json``
sidecar so two ``wd discover`` runs at a fixed commit produce a
byte-identical ``graph.json`` with no exempt field to strip.

The per-child recurse writer (``weld._discover_recurse``) discovers each
present child in-process and writes its graph. It must inherit the same
content-addressability as a standalone ``wd discover``: routed through the
paired writer ``write_graph_with_meta`` (via
``weld.discover._discover_single_repo(write_graph=True)`` ->
``finalize_single_repo``) so the volatile fields land in the child's
sidecar, never in the child's ``graph.json``.

This test pins that contract end-to-end against a real git child:

* two recurse runs at the same commit -> byte-identical child
  ``graph.json`` (the regression a legacy in-graph ``updated_at`` would
  break: every run rewrites that one line);
* the child ``graph.json`` carries neither volatile key;
* the child ``graph-meta.json`` sidecar carries the volatile payload
  (``git_sha`` here, since the child is a git checkout).
"""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from weld._discover_recurse import recurse_children
from weld._graph_meta_sidecar import (
    SIDECAR_NAME,
    VOLATILE_META_KEYS,
)
from weld.workspace import ChildEntry, WorkspaceConfig, dump_workspaces_yaml
from weld.workspace_state import build_workspace_state


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
    # A real source file so discovery emits a non-empty node set: that is
    # what makes the byte-stability claim meaningful (an empty graph is
    # trivially stable). One module is enough.
    (repo_root / "mod.py").write_text(
        "def f() -> int:\n    return 1\n", encoding="utf-8",
    )
    _git(repo_root, "add", "-A")
    _git(repo_root, "commit", "-q", "-m", "initial commit")
    return repo_root


def _write_discover_yaml(repo_root: Path) -> None:
    weld_dir = repo_root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    (weld_dir / "discover.yaml").write_text(
        "sources:\n"
        "  - glob: '*.py'\n"
        "    type: file\n"
        "    strategy: python_module\n",
        encoding="utf-8",
    )


def _write_workspaces(root: Path, children: list[ChildEntry]) -> WorkspaceConfig:
    config = WorkspaceConfig(children=children, cross_repo_strategies=[])
    dump_workspaces_yaml(config, root / ".weld" / "workspaces.yaml")
    return config


class RecurseByteIdentityTest(unittest.TestCase):
    """ADR 0065: recurse-written child graph.json is byte-stable per commit."""

    def _recurse_once(self, root: Path, config: WorkspaceConfig) -> None:
        # Rebuild the ledger each call exactly as the orchestrator does, so
        # the second run sees the child as ``present`` (it has a graph now).
        state = build_workspace_state(root, config)
        result = recurse_children(root, config, state, incremental=False)
        self.assertIn(
            "app", result.discovered,
            f"recurse must discover the child; errors={result.errors}",
        )

    def test_two_recurse_runs_are_byte_identical_at_fixed_commit(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = _init_repo(root / "app")
            _write_discover_yaml(child)
            config = _write_workspaces(root, [
                ChildEntry(name="app", path="app"),
            ])

            graph_path = child / ".weld" / "graph.json"
            sidecar_path = child / ".weld" / SIDECAR_NAME

            self._recurse_once(root, config)
            first = graph_path.read_bytes()
            self.assertNotIn(
                b"SENTINEL_CLOBBER", first,
                "real recurse output must not contain the sentinel",
            )

            # Overwrite the child graph with a VALID-but-distinct graph so
            # the child still classifies as ``present`` (a corrupt graph is
            # skipped by recurse, by design) yet the second run must fully
            # rewrite it -- proving the write is full-content, not a no-op.
            # A clobber to invalid JSON would flip the child to ``corrupt``
            # and recurse would skip it; this sentinel keeps it ``present``.
            graph_path.write_text(
                json.dumps(
                    {"meta": {}, "nodes": {"SENTINEL_CLOBBER": {}}, "edges": []},
                    indent=2,
                ) + "\n",
                encoding="utf-8",
            )

            self._recurse_once(root, config)
            second = graph_path.read_bytes()
            self.assertNotIn(
                b"SENTINEL_CLOBBER", second,
                "second recurse must fully rewrite the clobbered graph",
            )

            # The core ADR 0065 guarantee for the secondary recurse writer:
            # same commit, same source -> byte-identical graph.json.
            self.assertEqual(
                first, second,
                "recurse-written child graph.json must be byte-identical at "
                "a fixed commit (volatile meta must live in the sidecar)",
            )

            # graph.json must carry neither volatile key.
            graph = json.loads(second.decode("utf-8"))
            for key in VOLATILE_META_KEYS:
                self.assertNotIn(
                    key, graph.get("meta", {}),
                    f"volatile key {key!r} must not be in the child graph.json",
                )

            # The volatile payload lives in the sidecar. The child is a git
            # checkout, so at least ``git_sha`` is present there.
            self.assertTrue(
                sidecar_path.is_file(),
                "recurse must write the volatile-meta sidecar next to "
                "the child graph.json",
            )
            sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
            self.assertEqual(sidecar.get("version"), 1)
            self.assertIn(
                "git_sha", sidecar,
                "sidecar must carry git_sha for a git-checkout child",
            )

    def test_recurse_graph_json_equals_standalone_discover_bytes(self) -> None:
        """Recurse child graph.json is byte-equal to a standalone discover.

        ADR 0065 recurse note: the paired write makes recurse output
        byte-equivalent to a standalone child discover. A standalone
        ``_discover_single_repo(write_graph=True)`` and the recurse write
        funnel through the same ``write_graph_with_meta`` helper, so for
        the same child state the on-disk ``graph.json`` bytes must match.
        """
        from weld.discover import _discover_single_repo

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = _init_repo(root / "app")
            _write_discover_yaml(child)
            config = _write_workspaces(root, [
                ChildEntry(name="app", path="app"),
            ])
            graph_path = child / ".weld" / "graph.json"

            # Standalone full discover of the child writes graph.json via
            # the same paired writer the recurse tail uses.
            _discover_single_repo(child, incremental=False, write_graph=True)
            standalone = graph_path.read_bytes()

            # Recurse rebuilds the same child graph from the root.
            self._recurse_once(root, config)
            via_recurse = graph_path.read_bytes()

            self.assertEqual(
                standalone, via_recurse,
                "recurse child graph.json bytes must equal a standalone "
                "discover of the same child (both go through "
                "write_graph_with_meta per ADR 0065)",
            )


if __name__ == "__main__":
    unittest.main()
