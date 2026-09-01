"""A mock-patch edge must survive an incremental refresh (bd ymso).

The mock edge (``file:<test> --depends_on--> symbol:<patched>``) points
*into* production the same way ``test_peer``'s ``tests`` edge does, so it
inherits the failure that bd heum cost this repo once already: edit the
production module, and ``purge_stale_nodes`` removes the symbol node the
edge lands on. ADR 0074's ``purge_edges_by_provenance`` keeps an edge across
that purge only when the edge names the file that produced it, and the
producing file here is the **test** file -- which is clean, so ``test_peer``
never re-runs and could never re-mint what the purge dropped.

Sibling of ``incremental_test_peer_provenance_purge_test``, which pins the
identical contract for the ``tests`` edge. This one is separate rather than
folded in because it needs a third source entry: the mock edge only resolves
when ``python_callgraph`` has minted the target symbol, so the fixture is not
the two-source one that file is built around.

The assertion is the observable contract -- an incremental refresh and a full
discover of the same source state must agree on nodes and edges -- checked in
both dirty directions, plus the deletion case proving provenance survival
cannot resurrect an edge whose target is genuinely gone.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from weld.discover import _discover_single_repo

_PROD = "lib/thing.py"
_MOCK_EDGE = ("file:lib/tests/thing_test", "symbol:py:lib.thing:run")


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
    out["meta"] = {
        k: v for k, v in graph.get("meta", {}).items()
        if k not in ("updated_at", "git_sha", "discovered_from")
    }
    return out


def _mock_edges(graph: dict) -> list[tuple[str, str]]:
    return sorted(
        (e["from"], e["to"])
        for e in graph.get("edges", [])
        if (e.get("props") or {}).get("resolution") == "mock_patch"
    )


def _write_fixture(root: Path, prod_body: str, keep_symbol: bool = True) -> None:
    """Lay down the fixture and its three-source discover.yaml.

    ``python_module`` and ``python_callgraph`` own the production glob and
    ``test_peer`` the test glob -- disjoint on purpose, so a dirty production
    file leaves the ``test_peer`` glob entirely clean. That disjointness is
    the whole defect: it is what stops the strategy from re-running and
    re-minting the edge the purge dropped.
    """
    lib = root / "lib"
    lib.mkdir(exist_ok=True)
    body = (
        f"def run():\n    return {prod_body}\n" if keep_symbol
        else f"def other():\n    return {prod_body}\n"
    )
    (lib / "thing.py").write_text(body, encoding="utf-8")
    tests = lib / "tests"
    tests.mkdir(exist_ok=True)
    (tests / "thing_test.py").write_text(
        'from unittest.mock import patch\n'
        '\n\ndef test_run():\n'
        '    with patch("lib.thing.run"):\n        pass\n',
        encoding="utf-8",
    )
    weld_dir = root / ".weld"
    weld_dir.mkdir(exist_ok=True)
    (weld_dir / "discover.yaml").write_text(
        "sources:\n"
        "  - glob: lib/*.py\n    type: file\n    strategy: python_module\n"
        "  - glob: lib/*.py\n    type: symbol\n    strategy: python_callgraph\n"
        "  - glob: lib/tests/*_test.py\n    type: file\n    strategy: test_peer\n",
        encoding="utf-8",
    )


def _seed_then_edit(prod_body: str, keep_symbol: bool = True) -> dict:
    """Full-discover the ``"1"`` state, apply the edit, refresh incrementally."""
    with tempfile.TemporaryDirectory(prefix="mp-prov-inc-") as td:
        root = Path(td)
        _git(root)
        _write_fixture(root, "1")
        _commit(root)
        _discover_single_repo(root, incremental=False, write_graph=True)
        _write_fixture(root, prod_body, keep_symbol=keep_symbol)
        _commit(root)
        return _discover_single_repo(root, incremental=True, write_graph=True)


def _full_at(prod_body: str, keep_symbol: bool = True) -> dict:
    """Full-discover a clean checkout of the post-edit state."""
    with tempfile.TemporaryDirectory(prefix="mp-prov-full-") as td:
        root = Path(td)
        _git(root)
        _write_fixture(root, prod_body, keep_symbol=keep_symbol)
        _commit(root)
        return _discover_single_repo(root, incremental=False, write_graph=True)


class DirtyPatchedModuleKeepsMockEdgeTest(unittest.TestCase):
    """Edit the patched production module; the test file stays clean."""

    def test_edge_survives_and_graph_matches_full(self) -> None:
        g_inc = _seed_then_edit("2")
        g_full = _full_at("2")

        self.assertIn(
            _MOCK_EDGE, _mock_edges(g_inc),
            "mock-patch edge into the dirty patched module was purged and "
            "never re-minted (the test glob holds no dirty file, so "
            "test_peer never re-runs) -- ADR 0074 provenance regression",
        )
        self.assertEqual(_mock_edges(g_inc), _mock_edges(g_full))
        self.assertEqual(
            _strip_meta(g_inc), _strip_meta(g_full),
            "incremental graph (nodes+edges, sans volatile meta) must be "
            "byte-identical to a full discover at the same source state",
        )

    def test_provenance_names_the_test_file(self) -> None:
        """Pinning ADR 0074's direction stops a later change stamping the target.

        Stamping the patched module would be exactly backwards: that file is
        stale in precisely the case this test covers, so the edge would be
        dropped every time it mattered.
        """
        g_inc = _seed_then_edit("2")
        stamps = {
            ((e.get("props") or {}).get("provenance") or {}).get("file")
            for e in g_inc["edges"]
            if (e.get("props") or {}).get("resolution") == "mock_patch"
        }
        self.assertEqual(stamps, {"lib/tests/thing_test.py"})


class DeletedPatchTargetDropsMockEdgeTest(unittest.TestCase):
    """Provenance survival must not resurrect an edge with no target left."""

    def test_edge_is_gone_when_the_patched_symbol_is_removed(self) -> None:
        g_inc = _seed_then_edit("1", keep_symbol=False)
        g_full = _full_at("1", keep_symbol=False)

        self.assertEqual(_mock_edges(g_inc), [])
        self.assertEqual(_mock_edges(g_inc), _mock_edges(g_full))
        self.assertEqual(_strip_meta(g_inc), _strip_meta(g_full))


if __name__ == "__main__":
    unittest.main()
