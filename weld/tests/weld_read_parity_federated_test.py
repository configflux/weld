"""CLI == MCP parity for ``stale`` at a **federated** root (ADR 0100).

The single-repo half of this invariant lives in
:mod:`weld.tests.weld_read_parity_test`. It is split here because the federated
case needs three real git repositories and a workspace registry, and because
the divergence it pins was invisible to a single-repo fixture: ``weld_stale``
used to branch away from the product shaper
(:func:`weld._stale_payload.stale_payload`) at a federated root only.

The fixture is deliberately non-degenerate in the one way that matters. The
root meta-graph is **fresh** (it records the root's own HEAD and tracks only
``README.md``, so a commit inside a child repo is not a root source change)
while the child **has drifted** a commit past the graph it was discovered
from. So:

* a handler that folded no children answers ``stale=false`` and fails, which is
  the ADR 0066 §2 blind spot this pins -- an agent polling a polyrepo root
  reading "nothing to do" while a child graph has gone stale under it;
* a handler that dates the child through the wrong seam answers
  ``commits_behind=-1`` / ``reason`` absent and fails. ADR 0065 moved
  ``git_sha`` into the gitignored sidecar, and the federated child loader
  assigns a byte snapshot straight onto ``Graph._data`` rather than going
  through ``Graph.load()``, so a per-child ``Graph.stale()`` never sees it. The
  oracle reads through ``load_graph_meta``; asserting the exact
  ``commits_behind`` is what tells those two readers apart.

``missing`` is registered alongside so the lifecycle passthrough (ADR 0066 §1
rule 1: absent is not "behind") is pinned in the same projection.

This fixture runs with ``WELD_AUTO_REFRESH=0``, so it pins the *shape* of the
answer against a frozen workspace. That masking is deliberate but it is not
free: with refresh on, a healing handler would recurse the drifted child
before answering and hand back ``fresh``, defeating the fold this module
pins. That half is covered by
:mod:`weld.tests.weld_stale_refresh_exemption_test` (ADR 0102), whose
federated fixture gives the child a ``discover.yaml`` so the recurse has
something to run and the assertion has something to catch.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from weld import mcp_server
from weld._graph_cli import main as cli_main
from weld.workspace import ChildEntry, WorkspaceConfig, dump_workspaces_yaml

_TS = "2026-04-15T21:00:00+00:00"


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(root), capture_output=True, text=True,
        check=True, env={**os.environ, "LC_ALL": "C"},
    )
    return proc.stdout.strip()


def _init_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Weld Test")
    (root / "README.md").write_text("# fixture\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-q", "-m", "seed")
    return root


def _write_graph(root: Path, nodes: dict, *, tracked: list[str], sv: int = 1) -> None:
    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {"version": 1, "updated_at": _TS, "schema_version": sv,
                 "discovered_from": tracked},
        "nodes": nodes, "edges": [],
    }
    (weld_dir / "graph.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )


def _write_sidecar(root: Path, sha: str) -> None:
    """Record the discovered-from SHA where ADR 0065 puts it: the sidecar."""
    (root / ".weld" / "graph-meta.json").write_text(
        json.dumps({"version": 1, "git_sha": sha}) + "\n", encoding="utf-8",
    )


class FederatedStaleParityTest(unittest.TestCase):
    """``wd stale --json`` == ``weld_stale`` at a polyrepo root."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._tmp, True)
        self.root = Path(self._tmp) / "workspace"
        _init_repo(self.root)

        # Child that drifts: graph recorded at its HEAD, then one more commit.
        child = _init_repo(self.root / "svc-api")
        _write_graph(
            child,
            {"entity:Store": {"type": "entity", "label": "Store",
                              "props": {"file": "src/store.py"}}},
            tracked=["./"],
        )
        _write_sidecar(child, _git(child, "rev-parse", "HEAD"))
        (child / "src.py").write_text("x = 1\n", encoding="utf-8")
        _git(child, "add", "src.py")
        _git(child, "commit", "-q", "-m", "drift")

        # Root meta-graph: fresh, and tracking only README.md so the child's
        # commit above can never register as root drift.
        _write_graph(
            self.root,
            {"repo:svc-api": {"type": "repo", "label": "svc-api",
                              "props": {"path": "svc-api"}}},
            tracked=["README.md"], sv=2,
        )
        _write_sidecar(self.root, _git(self.root, "rev-parse", "HEAD"))
        dump_workspaces_yaml(
            WorkspaceConfig(
                children=[ChildEntry(name="svc-api", path="svc-api"),
                          ChildEntry(name="svc-gone", path="svc-gone")],
                cross_repo_strategies=[],
            ),
            self.root / ".weld" / "workspaces.yaml",
        )

        self._prev = os.environ.get("WELD_AUTO_REFRESH")
        os.environ["WELD_AUTO_REFRESH"] = "0"
        self.addCleanup(self._restore)
        from weld._mcp_read import clear_graph_cache
        clear_graph_cache()
        self.addCleanup(clear_graph_cache)

    def _restore(self) -> None:
        if self._prev is None:
            os.environ.pop("WELD_AUTO_REFRESH", None)
        else:
            os.environ["WELD_AUTO_REFRESH"] = self._prev

    def _cli_stale(self) -> dict:
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli_main(["--root", str(self.root), "stale", "--json"])
        return json.loads(buf.getvalue())

    @staticmethod
    def _child(payload: dict, name: str) -> dict:
        children = payload["children"]
        assert isinstance(children, list), type(children)
        return next(c for c in children if c["name"] == name)

    def test_federated_stale_cli_equals_mcp_handler(self) -> None:
        self.assertEqual(self._cli_stale(),
                         mcp_server.weld_stale(root=str(self.root)))

    def test_the_seed_cause_stays_out_of_a_federated_payload(self) -> None:
        """ADR 0100 amendment (bd kgx83), federated half.

        ``seed_blocked_reason`` is added by the same shaper this suite pins,
        so its absence here is a claim worth checking rather than an
        assumption: ADR 0096 puts polyrepo worktree reads out of scope (gate
        3 declines before the config prerequisite is ever consulted), so a
        federated root must never be told about a seed that was never
        attempted -- on either surface.
        """
        self.assertNotIn("seed_blocked_reason", self._cli_stale())
        self.assertNotIn(
            "seed_blocked_reason", mcp_server.weld_stale(root=str(self.root)),
        )

    def test_child_drift_raises_top_level_stale(self) -> None:
        """The ADR 0066 §2 fold: ``root_stale OR any(child.stale)``."""
        served = mcp_server.weld_stale(root=str(self.root))
        # The root is fresh on both of its own signals, so a true ``stale``
        # here can only have come from folding the child.
        self.assertFalse(served["root_source_stale"])
        self.assertFalse(served["root_sha_behind"])
        self.assertTrue(served["stale"])

    def test_children_use_the_cli_projection(self) -> None:
        served = mcp_server.weld_stale(root=str(self.root))
        self.assertEqual(
            self._child(served, "svc-api"),
            {"name": "svc-api", "state": "stale", "reason": "source_changed",
             "commits_behind": 1},
        )

    def test_missing_child_passes_its_lifecycle_state_through(self) -> None:
        served = mcp_server.weld_stale(root=str(self.root))
        self.assertEqual(
            self._child(served, "svc-gone"),
            {"name": "svc-gone", "state": "missing", "reason": "missing",
             "commits_behind": 0},
        )

    def test_branch_identity_survives_the_federated_path(self) -> None:
        # ADR 0096 §3 -- an answer folded from several repos is precisely one
        # where naming the checkout that answered is worth doing.
        served = mcp_server.weld_stale(root=str(self.root))
        self.assertEqual(
            served["branch"], _git(self.root, "rev-parse", "--abbrev-ref", "HEAD"),
        )
        self.assertIn("graph_branch", served)


if __name__ == "__main__":
    unittest.main()
