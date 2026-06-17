"""Bootstrap root ``graph.json`` is content-addressable via the sidecar.

ADR 0065 relocates the volatile ``meta`` fields (``updated_at`` /
``git_sha``) out of ``graph.json`` into a gitignored ``graph-meta.json``
sidecar. The one-shot polyrepo bootstrap orchestrator
(:func:`weld._workspace_bootstrap.bootstrap_workspace`) writes the root
meta-graph; that write must route through the same paired writer the
standalone federated-root discover uses so the bootstrap root graph is
byte-stable / content-addressable too, not a legacy full-meta graph.

This test pins that the bootstrap root ``graph.json`` carries no volatile
key and that the always-stamped volatile field (``updated_at`` -- a wall
clock value :func:`weld.federation_root.build_root_meta_graph` always
emits) lands in the root ``graph-meta.json`` sidecar instead.
"""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from weld._graph_meta_sidecar import SIDECAR_NAME, VOLATILE_META_KEYS
from weld._workspace_bootstrap import bootstrap_workspace


def _git(repo_root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        env={"LC_ALL": "C", "PATH": "/usr/bin:/usr/local/bin:/bin"},
        check=True,
    )


def _init_child(repo_root: Path) -> None:
    repo_root.mkdir(parents=True, exist_ok=True)
    _git(repo_root, "init", "-q")
    _git(repo_root, "config", "user.email", "test@example.com")
    _git(repo_root, "config", "user.name", "Weld Test")
    (repo_root / "README.md").write_text("# fixture\n", encoding="utf-8")
    _git(repo_root, "add", "README.md")
    _git(repo_root, "commit", "-q", "-m", "initial commit")


class BootstrapRootSidecarTest(unittest.TestCase):
    """ADR 0065: bootstrap root graph.json has no volatile meta; sidecar does."""

    def test_bootstrap_root_graph_routes_volatile_meta_to_sidecar(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            # A polyrepo container with two real git children. The container
            # root itself is deliberately not a git repo (bootstrap's design),
            # so git_sha may be absent -- updated_at is always stamped.
            _init_child(root / "services" / "api")
            _init_child(root / "libs" / "shared")

            result = bootstrap_workspace(root)
            self.assertEqual(
                result.children_present,
                ["libs-shared", "services-api"],
                f"bootstrap must reach present for both children; "
                f"errors={result.errors}",
            )

            graph_path = root / ".weld" / "graph.json"
            sidecar_path = root / ".weld" / SIDECAR_NAME
            graph = json.loads(graph_path.read_text(encoding="utf-8"))

            # The bootstrap root graph must be a real federated meta-graph...
            self.assertIn("repo:services-api", graph["nodes"])
            # ...with no volatile key left in graph.json.
            for key in VOLATILE_META_KEYS:
                self.assertNotIn(
                    key, graph.get("meta", {}),
                    f"bootstrap root graph.json must not carry {key!r}",
                )

            # The volatile payload lives in the sidecar (updated_at is always
            # stamped by build_root_meta_graph).
            self.assertTrue(
                sidecar_path.is_file(),
                "bootstrap must write the root volatile-meta sidecar",
            )
            sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
            self.assertEqual(sidecar.get("version"), 1)
            self.assertIn(
                "updated_at", sidecar,
                "root sidecar must carry the always-stamped updated_at",
            )


if __name__ == "__main__":
    unittest.main()
