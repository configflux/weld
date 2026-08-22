"""Branch identity across the sidecar, ``wd stale``, and the freshness object.

ADR 0096 §3: an answer that does not say which checkout it came from cannot
be checked. A read served from another worktree -- or from a graph built
before a branch switch -- looks exactly like a correct one. Branch identity
closes that by stamping the branch at discover time and reporting the live
branch on every read.

The contract these tests pin:

1. ``git_branch`` is a **volatile** meta key. It rides the gitignored
   ``graph-meta.json`` sidecar and never the graph body, which is what keeps
   ``graph.json`` byte-identical across two branches at the same commit
   (ADR 0065 content-addressability). That byte-identity is asserted here
   against a *real* git repo and a *real* discover, because it is the
   property a future "just put the branch in meta" change would silently
   break.
2. ``wd stale`` reports ``branch`` (live) beside ``graph_branch`` (recorded),
   so a wrong-branch answer is visible without ``--json``.
3. The freshness object stamped on read payloads carries the **live** branch,
   not the sidecar's -- the question it answers is "which checkout am I being
   served from right now", and only the live value exposes a wrong root.
4. Every branchless state -- detached ``HEAD``, non-git root -- degrades to
   ``None`` rather than raising or reporting a stale value.

The whitelist assertion for the freshness object's exact key set lives in
``weld_mcp_freshness_test`` (the dispatch-boundary surface); these tests own
the branch *semantics* underneath it.
"""

from __future__ import annotations

import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from weld import _mcp_read
from weld._cli_render import render_stale
from weld._git_worktree import get_git_branch
from weld._graph_meta_sidecar import (
    SIDECAR_NAME,
    VOLATILE_META_KEYS,
    load_graph_meta,
    split_volatile_meta,
    write_graph_with_meta,
)
from weld._stale_payload import branch_identity, stale_payload
from weld.contract import SCHEMA_VERSION
from weld.discover import main as discover_main

# Fixed, minimal environment for the git fixtures: mirrors
# ``discover_worktree_canonical_graph_test`` so these stay sandbox-hermetic
# (own repo, own identity, no ambient user config).
_GIT_ENV = {"LC_ALL": "C", "PATH": "/usr/bin:/usr/local/bin:/bin"}


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        env=_GIT_ENV,
        check=True,
    )
    return proc.stdout.strip()


def _init_repo(root: Path, branch: str) -> None:
    """Init a git repo with one commit, checked out on *branch*.

    The branch is created explicitly with ``checkout -b`` rather than relying
    on ``init.defaultBranch``, so the assertions do not depend on the git
    version or on ambient config.
    """
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Weld Test")
    (root / "hello.py").write_text(
        "def greet():\n    return 'hello'\n", encoding="utf-8",
    )
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    (root / ".weld" / "discover.yaml").write_text(
        'sources:\n'
        '  - glob: "*.py"\n'
        '    type: symbol\n'
        '    strategy: python_module\n',
        encoding="utf-8",
    )
    _git(root, "add", "hello.py", ".weld/discover.yaml")
    _git(root, "commit", "-q", "-m", "seed")
    _git(root, "checkout", "-q", "-b", branch)


def _graph(meta: dict) -> dict:
    return {"meta": {"version": SCHEMA_VERSION, **meta}, "nodes": {}, "edges": []}


def _seed_graph(root: Path, meta: dict) -> Path:
    """Write ``.weld/graph.json`` (+ sidecar) with *meta* and return its path."""
    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    graph_path = weld_dir / "graph.json"
    write_graph_with_meta(graph_path, _graph(meta))
    return graph_path


def _sidecar(root: Path) -> dict:
    return json.loads((root / ".weld" / SIDECAR_NAME).read_text(encoding="utf-8"))


def _graph_body_meta(root: Path) -> dict:
    data = json.loads((root / ".weld" / "graph.json").read_text(encoding="utf-8"))
    return data.get("meta", {})


def _run_discover(root: Path) -> int:
    """Invoke a bare ``wd discover <root>``, swallowing its console output."""
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        return discover_main([str(root), "--no-enrich"])


# ---------------------------------------------------------------------------
# 1. The git helper
# ---------------------------------------------------------------------------

class GetGitBranchTest(unittest.TestCase):
    def test_reports_the_checked_out_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            _init_repo(root, "feature-x")
            self.assertEqual(get_git_branch(root), "feature-x")

    def test_slashed_branch_name_is_returned_verbatim(self) -> None:
        # ``--short`` must not truncate at the slash: ``feature/login`` is one
        # branch, and a truncated identity is worse than none.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            _init_repo(root, "feature/login")
            self.assertEqual(get_git_branch(root), "feature/login")

    def test_detached_head_is_none(self) -> None:
        # ``symbolic-ref`` fails on a detached HEAD -- the reason we do not use
        # ``rev-parse --abbrev-ref``, which would print the literal "HEAD".
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            _init_repo(root, "main")
            _git(root, "checkout", "-q", "--detach")
            self.assertIsNone(get_git_branch(root))

    def test_non_git_directory_is_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(get_git_branch(Path(tmp)))

    def test_missing_directory_is_none(self) -> None:
        # ``cwd`` pointing at a non-existent path raises from subprocess; the
        # helper must absorb it (it runs on the read path).
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(get_git_branch(Path(tmp) / "nope"))


# ---------------------------------------------------------------------------
# 2. Sidecar round-trip
# ---------------------------------------------------------------------------

class SidecarGitBranchRoundTripTest(unittest.TestCase):
    def test_git_branch_is_registered_volatile(self) -> None:
        self.assertIn("git_branch", VOLATILE_META_KEYS)

    def test_split_moves_git_branch_out_of_the_graph_body(self) -> None:
        on_disk, volatile = split_volatile_meta(
            _graph({"git_sha": "deadbeef", "git_branch": "feature-x"}),
        )
        self.assertEqual(volatile.get("git_branch"), "feature-x")
        self.assertNotIn("git_branch", on_disk["meta"])

    def test_write_then_read_round_trips_the_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            graph_path = _seed_graph(
                root, {"git_sha": "deadbeef", "git_branch": "feature-x"},
            )
            self.assertNotIn("git_branch", _graph_body_meta(root))
            self.assertEqual(_sidecar(root).get("git_branch"), "feature-x")
            # The read seam overlays it back, so consumers see meta as before.
            self.assertEqual(load_graph_meta(graph_path).get("git_branch"), "feature-x")


# ---------------------------------------------------------------------------
# 3. Discover stamps the branch -- and graph.json stays byte-identical
# ---------------------------------------------------------------------------

class DiscoverBranchStampTest(unittest.TestCase):
    def test_discover_stamps_branch_in_sidecar_not_in_graph_body(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            _init_repo(root, "branch-one")

            self.assertEqual(_run_discover(root), 0)

            self.assertEqual(_sidecar(root).get("git_branch"), "branch-one")
            self.assertNotIn("git_branch", _graph_body_meta(root))

    def test_graph_json_is_byte_identical_across_branches_at_one_commit(self) -> None:
        # The ADR 0065 pin. Two branches, one commit, identical sources: the
        # content-addressable body must not move even though the recorded
        # branch does. Keeping ``git_branch`` volatile is the only reason this
        # holds -- a plain ``meta`` field would fail here.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            _init_repo(root, "branch-one")
            graph_json = root / ".weld" / "graph.json"

            self.assertEqual(_run_discover(root), 0)
            first_bytes = graph_json.read_bytes()
            first_branch = _sidecar(root).get("git_branch")

            _git(root, "checkout", "-q", "-b", "branch-two")
            self.assertEqual(_run_discover(root), 0)
            second_bytes = graph_json.read_bytes()
            second_branch = _sidecar(root).get("git_branch")

            self.assertEqual(
                first_bytes, second_bytes,
                "graph.json must be byte-identical across two branches at the "
                "same commit (ADR 0065): branch identity belongs in the sidecar",
            )
            self.assertEqual(first_branch, "branch-one")
            self.assertEqual(
                second_branch, "branch-two",
                "the second discover must re-stamp the branch even though no "
                "source changed -- otherwise wd stale names the branch we left",
            )

    def test_detaching_head_clears_the_recorded_branch(self) -> None:
        # A stale branch is worse than no branch: once HEAD is detached the
        # previously recorded value must be dropped, not carried forward.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            _init_repo(root, "branch-one")
            self.assertEqual(_run_discover(root), 0)
            self.assertEqual(_sidecar(root).get("git_branch"), "branch-one")

            _git(root, "checkout", "-q", "--detach")
            self.assertEqual(_run_discover(root), 0)

            self.assertIsNone(_sidecar(root).get("git_branch"))


# ---------------------------------------------------------------------------
# 4. ``wd stale``: live branch beside recorded branch
# ---------------------------------------------------------------------------

class StalePayloadBranchTest(unittest.TestCase):
    def test_reports_live_and_recorded_branch_when_they_diverge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            _init_repo(root, "live-branch")
            _seed_graph(root, {"git_sha": "deadbeef", "git_branch": "recorded-branch"})

            payload = stale_payload(root, {"stale": False, "commits_behind": 0})

            self.assertEqual(payload["branch"], "live-branch")
            self.assertEqual(payload["graph_branch"], "recorded-branch")
            # Additive only: the staleness signals pass through untouched.
            self.assertFalse(payload["stale"])
            self.assertEqual(payload["commits_behind"], 0)

    def test_branch_identity_is_none_outside_git_and_without_a_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity = branch_identity(Path(tmp))
            self.assertEqual(identity, {"branch": None, "graph_branch": None})

    def test_unreadable_sidecar_degrades_to_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            _init_repo(root, "live-branch")
            (root / ".weld").mkdir(parents=True, exist_ok=True)
            (root / ".weld" / SIDECAR_NAME).write_text("{not json", encoding="utf-8")

            identity = branch_identity(root)

            self.assertEqual(identity["branch"], "live-branch")
            self.assertIsNone(identity["graph_branch"])

    def test_human_render_shows_both_branch_lines(self) -> None:
        rendered = render_stale({
            "stale": False,
            "commits_behind": 0,
            "graph_branch": "recorded-branch",
            "branch": "live-branch",
        })
        self.assertIn("graph_branch: recorded-branch", rendered)
        self.assertIn("branch: live-branch", rendered)


# ---------------------------------------------------------------------------
# 5. Freshness object: the branch is live, never the recorded one
# ---------------------------------------------------------------------------

class FreshnessBranchTest(unittest.TestCase):
    def setUp(self) -> None:
        _mcp_read.clear_graph_cache()
        self.addCleanup(_mcp_read.clear_graph_cache)

    def test_freshness_reports_the_live_branch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            _init_repo(root, "feature-x")
            _seed_graph(root, {"git_sha": _git(root, "rev-parse", "HEAD")})

            self.assertEqual(_mcp_read.freshness_for(root)["branch"], "feature-x")

    def test_freshness_branch_is_live_not_the_recorded_one(self) -> None:
        # The whole point: a graph built elsewhere must not be able to claim
        # this read came from its branch.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            _init_repo(root, "live-branch")
            _seed_graph(
                root,
                {
                    "git_sha": _git(root, "rev-parse", "HEAD"),
                    "git_branch": "recorded-branch",
                },
            )

            self.assertEqual(_mcp_read.freshness_for(root)["branch"], "live-branch")

    def test_freshness_branch_is_none_on_detached_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            _init_repo(root, "main")
            _seed_graph(root, {"git_sha": _git(root, "rev-parse", "HEAD")})
            _git(root, "checkout", "-q", "--detach")

            self.assertIsNone(_mcp_read.freshness_for(root)["branch"])

    def test_freshness_branch_is_none_outside_git(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _seed_graph(root, {})

            self.assertIsNone(_mcp_read.freshness_for(root)["branch"])

    def test_key_set_is_invariant_on_the_degraded_path(self) -> None:
        # No graph at all: freshness still answers with the same three keys, so
        # a consumer never has to probe for presence.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            _init_repo(root, "feature-x")

            fresh = _mcp_read.freshness_for(root)

            self.assertEqual(set(fresh), {"stale", "commits_behind", "branch"})
            self.assertEqual(fresh["branch"], "feature-x")

    def test_freshness_leaks_neither_the_root_path_nor_the_recorded_sha(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            _init_repo(root, "feature-x")
            _seed_graph(
                root,
                {"git_sha": "deadbeefcafe", "token": "BRANCH-FRESHNESS-SECRET-XYZ"},
            )

            serialized = json.dumps(_mcp_read.freshness_for(root))

            self.assertNotIn(str(root), serialized)
            self.assertNotIn("deadbeefcafe", serialized)
            self.assertNotIn("BRANCH-FRESHNESS-SECRET-XYZ", serialized)


if __name__ == "__main__":
    unittest.main()
