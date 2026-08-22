"""Provenance-keyed edge purge keeps ``test_peer`` ``tests`` edges.

Sibling of ``incremental_callgraph_provenance_purge_test`` covering the
same ADR 0074 contract for the ``test_peer`` strategy, split into its own
file only because the callgraph case already sits near the 400-line cap.

The defect this pins (bd heum): ``purge_stale_nodes`` removes the
``file:`` node for a dirty **production** module. ADR 0074's
``purge_edges_by_provenance`` retains an edge across that purge only when
the edge names the file that produced it (``props.provenance.file``).
``test_peer`` emitted no provenance, so its ``tests`` edge fell through to
the conservative endpoint-membership floor and was dropped -- and because
the *test* file was clean, ``test_peer``'s glob held no dirty file, the
strategy never re-ran, and the edge was never re-minted. Incremental and
full discovers of the same source state disagreed by exactly that edge.

The fix is the opt-in ADR 0074 already authorizes: ``test_peer`` stamps
``provenance.file`` with the test file, which *is* the producing file by
construction (the strategy derives the edge by walking the test glob and
resolving each test file's peer). The purge rule is unchanged.

These tests assert the observable contract -- byte-identity of nodes and
edges between an incremental refresh and a full discover at the same end
state -- in **both** dirty directions, plus the deletion case that proves
provenance survival cannot resurrect a dangling edge.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from weld.discover import _discover_single_repo

#: The production module and its test peer. ``resolve_peer`` matches this
#: through its grandparent probe (``lib/tests/thing_test.py`` ->
#: ``lib/thing.py``), the Bazel/Go-style layout this repo itself uses.
_PROD = "lib/thing.py"
_TEST = "lib/tests/thing_test.py"
_EDGE = ("file:lib/tests/thing_test", "file:lib/thing")


def _git(root: Path) -> None:
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@t"],
        ["git", "config", "user.name", "T"],
        ["git", "config", "commit.gpgsign", "false"],
    ):
        subprocess.run(cmd, cwd=str(root), check=True)


def _commit(root: Path) -> None:
    subprocess.run(["git", "add", "-A"], cwd=str(root), check=True)
    subprocess.run(["git", "commit", "-qm", "x"], cwd=str(root), check=True)


def _strip_meta(graph: dict) -> dict:
    """Drop volatile + path-order-volatile meta; nodes/edges must match."""
    out = {k: v for k, v in graph.items() if k != "meta"}
    meta = {
        k: v for k, v in graph.get("meta", {}).items()
        if k not in ("updated_at", "git_sha", "discovered_from")
    }
    out["meta"] = meta
    return out


def _tests_edges(graph: dict) -> list[tuple[str, str]]:
    return sorted(
        (e["from"], e["to"])
        for e in graph.get("edges", [])
        if e.get("type") == "tests"
    )


def _edge_provenance(graph: dict) -> object:
    """Return the ``provenance`` prop of the fixture's ``tests`` edge.

    Returns the sentinel ``"<no edge>"`` when the edge is absent, so a
    missing edge fails the provenance assertion with a legible value
    rather than an ``AttributeError`` on ``None``.
    """
    for e in graph.get("edges", []):
        if e.get("type") == "tests" and (e["from"], e["to"]) == _EDGE:
            return (e.get("props") or {}).get("provenance")
    return "<no edge>"


def _write_fixture(root: Path, prod_body: str, test_body: str) -> None:
    """Lay down the two-file fixture and its two-source discover.yaml.

    ``python_module`` owns the production glob and ``test_peer`` the test
    glob -- deliberately disjoint, so a dirty production file leaves the
    ``test_peer`` glob entirely clean. That disjointness is the whole
    defect: it is what stops the strategy from re-running and re-minting
    the edge the purge dropped.
    """
    lib = root / "lib"
    lib.mkdir(exist_ok=True)
    (lib / "thing.py").write_text(
        f"def run():\n    return {prod_body}\n", encoding="utf-8",
    )
    tests = lib / "tests"
    tests.mkdir(exist_ok=True)
    (tests / "thing_test.py").write_text(
        f"def test_run():\n    assert {test_body}\n", encoding="utf-8",
    )
    weld_dir = root / ".weld"
    weld_dir.mkdir(exist_ok=True)
    (weld_dir / "discover.yaml").write_text(
        "sources:\n"
        "  - glob: lib/*.py\n    type: file\n    strategy: python_module\n"
        "  - glob: lib/tests/*_test.py\n    type: file\n    strategy: test_peer\n",
        encoding="utf-8",
    )


def _seed_then_edit(prod_body: str, test_body: str, delete_prod: bool = False) -> dict:
    """Full-discover the ``"1"`` state, apply the edit, refresh incrementally."""
    with tempfile.TemporaryDirectory(prefix="tp-prov-inc-") as td:
        root = Path(td)
        _git(root)
        _write_fixture(root, "1", "True")
        _commit(root)
        _discover_single_repo(root, incremental=False, write_graph=True)
        _write_fixture(root, prod_body, test_body)
        if delete_prod:
            (root / _PROD).unlink()
        _commit(root)
        return _discover_single_repo(root, incremental=True, write_graph=True)


def _full_at(prod_body: str, test_body: str, delete_prod: bool = False) -> dict:
    """Full-discover a clean checkout of the post-edit state."""
    with tempfile.TemporaryDirectory(prefix="tp-prov-full-") as td:
        root = Path(td)
        _git(root)
        _write_fixture(root, prod_body, test_body)
        if delete_prod:
            (root / _PROD).unlink()
        _commit(root)
        return _discover_single_repo(root, incremental=False, write_graph=True)


class DirtyProductionPeerKeepsTestsEdgeTest(unittest.TestCase):
    """bd heum: edit the production peer; the test file stays clean."""

    def test_tests_edge_survives_and_graph_matches_full(self) -> None:
        g_inc = _seed_then_edit("2", "True")
        g_full = _full_at("2", "True")

        self.assertIn(
            _EDGE, _tests_edges(g_inc),
            "test_peer 'tests' edge into the dirty production peer was "
            "purged and never re-minted (the test glob holds no dirty "
            "file, so the strategy never re-runs) -- ADR 0074 provenance "
            "regression",
        )
        self.assertEqual(_tests_edges(g_inc), _tests_edges(g_full))
        self.assertEqual(
            _strip_meta(g_inc), _strip_meta(g_full),
            "incremental graph (nodes+edges, sans volatile meta) must be "
            "byte-identical to a full discover at the same source state",
        )

    def test_edge_names_the_test_file_as_its_producing_file(self) -> None:
        """The stamp itself: provenance is the *test* file, both paths.

        ADR 0074 keys purge on "the file that produced the edge". For
        ``test_peer`` that is the test file it walked, never the peer it
        resolved -- pinning the direction here is what stops a later
        change from stamping the endpoint and silently re-opening the
        defect (a peer-stamped edge is stale exactly when the peer is
        dirty, i.e. purged in precisely the case this test covers).
        """
        for label, graph in (("full", _full_at("2", "True")),
                             ("incremental", _seed_then_edit("2", "True"))):
            with self.subTest(path=label):
                self.assertEqual(
                    _edge_provenance(graph), {"file": _TEST},
                    "tests edge must name the test file it was derived "
                    "from as props.provenance.file",
                )


class DirtyTestFileMatchesFullTest(unittest.TestCase):
    """The other direction: edit the test file; the peer stays clean.

    Green before the fix (the dirty test file is inside ``test_peer``'s
    glob, so the strategy re-runs and re-mints the edge the endpoint
    purge dropped) and pinned so the provenance stamp cannot regress it
    -- under the fix the prior edge is purged *by* provenance instead,
    and the re-run must still put exactly one back.
    """

    def test_graph_matches_full(self) -> None:
        g_inc = _seed_then_edit("1", "1 == 1")
        g_full = _full_at("1", "1 == 1")

        self.assertEqual(_tests_edges(g_inc), [_EDGE])
        self.assertEqual(
            _strip_meta(g_inc), _strip_meta(g_full),
            "edit-the-test-file incremental graph must be byte-identical "
            "to a full discover at the same source state",
        )


class DeletedProductionPeerDropsTestsEdgeTest(unittest.TestCase):
    """A deleted peer must not leave a dangling ``tests`` edge.

    The safety check on the new retention: provenance keeps the edge
    through the purge, but the peer node is gone and is never re-minted,
    so the post-process dangling-edge filter must drop it -- matching a
    full discover, where ``resolve_peer`` finds nothing on disk and emits
    no edge at all.
    """

    def test_deleted_peer_matches_full(self) -> None:
        g_inc = _seed_then_edit("1", "True", delete_prod=True)
        g_full = _full_at("1", "True", delete_prod=True)

        self.assertNotIn(
            "file:lib/thing", g_inc.get("nodes", {}),
            "deleted production peer must not survive incrementally",
        )
        self.assertEqual(
            _tests_edges(g_inc), [],
            "provenance survival must not resurrect a tests edge whose "
            "target was genuinely deleted",
        )
        self.assertEqual(
            _strip_meta(g_inc), _strip_meta(g_full),
            "deleted-peer incremental graph must match a full discover",
        )


if __name__ == "__main__":
    unittest.main()
