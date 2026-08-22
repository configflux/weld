"""Incremental == full when an external package's sole importer is deleted
(bd pkz2s).

Sibling of ``incremental_deleted_package_node_equivalence_test`` (bd g7rs),
which fixed the producer-side mirror of this same orphan-survival shape: a
fully-deleted ``python_package``/``csharp_package`` directory's own node used
to survive incremental discovery with zero *outgoing* ``contains`` edges.
This is the consumer-side half g7rs deliberately left out of scope --
``weld.graph_closure._ensure_package_node`` mints an external placeholder
(``props.source_strategy == "graph_closure"``, ``props.authority ==
"external"``) for every unresolvable import, anchored purely by inbound
``depends_on`` edges from its importer(s). When the last importer is
deleted, the edge is correctly purged but the node itself used to linger
with zero *inbound* edges, because ``purge_stale_nodes`` matched nodes to
purge by ``props.file`` alone and this node carries none.

Reported repro (this bd issue): Go's ``sample_go`` tier1 fixture,
``shapes.go`` -- the sole importer of stdlib ``strings`` in that tree --
deleted; incremental retained ``package:go:strings``, a fresh full discover
of the same post-delete tree did not. Reproduced here with a minimal
purpose-built fixture (mirroring ``incremental_inherits_provenance_purge_test``'s
Go section) rather than mutating the shared tier1 fixture in place.

Fixed by extending :func:`weld.discovery_state.purge_stale_nodes` to also
purge a closure-minted external package node once every inbound
``depends_on`` edge it had is gone post-purge
(:mod:`weld._discover_external_package_purge`) -- unconditional on the
caller's stale-file set, the same purge-driven (not rerun-driven) shape
g7rs's fix took, so no rerun-trigger change is needed either. The tests
below prove END TO END -- through the real ``discover()`` incremental path,
not just the unit-level purge call
``discovery_state_external_package_purge_test`` already pins -- that this
one purge extension is enough for full node+edge equivalence, with no
over-purge when a second importer survives.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from weld.discover import _discover_single_repo  # noqa: E402


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


def _write_fixture(root: Path, *, with_second_importer: bool) -> None:
    """A sole (or doubled) importer of stdlib ``strings``, plus an unrelated
    sibling package that imports ``math`` instead -- present in every round
    so a whole-graph comparison is non-trivial and so the deletion round has
    an unrelated node to prove is undisturbed, mirroring
    ``incremental_deleted_package_node_equivalence_test``'s ``other/``.
    """
    a = root / "a"
    a.mkdir(exist_ok=True)
    (a / "a.go").write_text(
        'package a\n\nimport "strings"\n\n'
        "func Norm(s string) string {\n\treturn strings.TrimSpace(s)\n}\n",
        encoding="utf-8",
    )

    other = root / "other"
    other.mkdir(exist_ok=True)
    (other / "other.go").write_text(
        'package other\n\nimport "math"\n\n'
        "func Sq(x float64) float64 {\n\treturn math.Pow(x, 2)\n}\n",
        encoding="utf-8",
    )

    if with_second_importer:
        b = root / "b"
        b.mkdir(exist_ok=True)
        (b / "b.go").write_text(
            'package b\n\nimport "strings"\n\n'
            "func Upper(s string) string {\n\treturn strings.ToUpper(s)\n}\n",
            encoding="utf-8",
        )

    (root / "go.mod").write_text("module example.com/sample\n\ngo 1.22\n", encoding="utf-8")

    weld_dir = root / ".weld"
    weld_dir.mkdir(exist_ok=True)
    (weld_dir / "discover.yaml").write_text(
        "sources:\n"
        '  - glob: "**/*.go"\n    type: file\n    strategy: tree_sitter\n'
        "    language: go\n",
        encoding="utf-8",
    )


def _strip_meta(graph: dict) -> dict:
    """Drop volatile keys, plus ``discovered_from`` -- its ORDER (not set)
    legitimately differs between the two construction paths (bd 8084), an
    orthogonal concern already covered by
    ``incremental_discovered_from_equivalence_test`` and not duplicated
    here."""
    out = {k: v for k, v in graph.items() if k != "meta"}
    out["meta"] = {
        k: v for k, v in graph.get("meta", {}).items()
        if k not in ("updated_at", "git_sha", "discovered_from")
    }
    return out


def _node_ids(graph: dict) -> set[str]:
    return set(graph.get("nodes", {}).keys())


def _edge_set(graph: dict) -> set[tuple[str, str, str]]:
    return {(e["from"], e["type"], e["to"]) for e in graph.get("edges", [])}


class SoleImporterDeletedEquivalenceTest(unittest.TestCase):
    """The exact bd pkz2s repro: delete the only file that imports
    ``strings``; a full discover of the post-delete tree never mints
    ``package:go:strings``, and incremental must match."""

    def test_incremental_matches_full_after_deleting_the_sole_importer(self) -> None:
        with tempfile.TemporaryDirectory(prefix="go-extpkg-inc-") as td:
            root = Path(td)
            _git(root)
            _write_fixture(root, with_second_importer=False)
            _commit(root)
            g_baseline = _discover_single_repo(root, incremental=False, write_graph=True)
            self.assertIn(
                "package:go:strings", _node_ids(g_baseline),
                "fixture setup assumption broken: the baseline full run must "
                "mint package:go:strings for the delete round to actually "
                "exercise anything",
            )
            shutil.rmtree(root / "a")
            _commit(root)
            g_inc = _discover_single_repo(root, incremental=True, write_graph=True)
            # Determinism: a second incremental pass over the same
            # (now-unchanged) tree must report the identical set, not merely
            # a first-run coincidence.
            g_inc_again = _discover_single_repo(root, incremental=True, write_graph=True)

        with tempfile.TemporaryDirectory(prefix="go-extpkg-full-") as td:
            root = Path(td)
            _git(root)
            _write_fixture(root, with_second_importer=False)
            shutil.rmtree(root / "a")
            _commit(root)
            g_full = _discover_single_repo(root, incremental=False, write_graph=True)

        inc_nodes, full_nodes = _node_ids(g_inc), _node_ids(g_full)
        self.assertEqual(
            inc_nodes, full_nodes,
            f"incremental node set diverged from a full discover after "
            f"deleting the sole importer (full-only="
            f"{sorted(full_nodes - inc_nodes)}, "
            f"inc-only={sorted(inc_nodes - full_nodes)})",
        )
        inc_edges, full_edges = _edge_set(g_inc), _edge_set(g_full)
        self.assertEqual(
            inc_edges, full_edges,
            f"incremental edge set diverged from a full discover after "
            f"deleting the sole importer (full-only="
            f"{sorted(full_edges - inc_edges)}, "
            f"inc-only={sorted(inc_edges - full_edges)})",
        )
        self.assertEqual(
            _strip_meta(g_inc), _strip_meta(g_full),
            "deleting the sole importer diverged the incremental graph from "
            "a full discover beyond just nodes/edges",
        )
        self.assertNotIn(
            "package:go:strings", inc_nodes,
            "a closure-minted external package placeholder survived "
            "incremental discovery as a zero-inbound-edge orphan after its "
            "sole importer was deleted (bd pkz2s)",
        )
        self.assertEqual(
            inc_nodes, _node_ids(g_inc_again),
            "node survival must be deterministic across repeated "
            "incremental passes over an unchanged tree, not merely correct "
            "on the first",
        )
        # The untouched sibling package's own external dependency must be
        # wholly unaffected -- proves this is not a blanket sweep.
        self.assertIn("package:go:math", inc_nodes)
        self.assertIn(
            ("file:other/other", "depends_on", "package:go:math"), inc_edges,
        )


class NonSoleImporterDeletedTest(unittest.TestCase):
    """No over-purge: two files import the same external package; deleting
    ONE of them must leave the placeholder alive, carrying only the
    surviving importer's edge -- matching what a full run over the same
    partially-emptied tree would still emit."""

    def test_incremental_keeps_the_placeholder_after_deleting_one_of_two_importers(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="go-extpkg-partial-inc-") as td:
            root = Path(td)
            _git(root)
            _write_fixture(root, with_second_importer=True)
            _commit(root)
            g_baseline = _discover_single_repo(root, incremental=False, write_graph=True)
            self.assertIn(
                "package:go:strings", _node_ids(g_baseline),
                "fixture setup assumption broken: the baseline full run must "
                "mint package:go:strings with two importers wired",
            )
            shutil.rmtree(root / "a")
            _commit(root)
            g_inc = _discover_single_repo(root, incremental=True, write_graph=True)

        inc_nodes, inc_edges = _node_ids(g_inc), _edge_set(g_inc)
        self.assertIn(
            "package:go:strings", inc_nodes,
            "an external package placeholder with a surviving importer "
            "must keep its node",
        )
        self.assertIn(
            ("file:b/b", "depends_on", "package:go:strings"), inc_edges,
        )
        self.assertNotIn(
            ("file:a/a", "depends_on", "package:go:strings"), inc_edges,
            "the deleted importer's depends_on edge must not survive",
        )
        self.assertNotIn("file:a/a", inc_nodes)

        with tempfile.TemporaryDirectory(prefix="go-extpkg-partial-full-") as td:
            root = Path(td)
            _git(root)
            _write_fixture(root, with_second_importer=True)
            shutil.rmtree(root / "a")
            _commit(root)
            g_full = _discover_single_repo(root, incremental=False, write_graph=True)

        self.assertEqual(
            inc_nodes, _node_ids(g_full),
            "partial delete: incremental node set diverged from a full "
            "discover over the identical post-delete tree",
        )
        self.assertEqual(
            inc_edges, _edge_set(g_full),
            "partial delete: incremental edge set diverged from a full "
            "discover over the identical post-delete tree",
        )


if __name__ == "__main__":
    unittest.main()
