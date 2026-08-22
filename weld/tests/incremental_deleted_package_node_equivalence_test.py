"""Incremental == full when a whole python_package directory is deleted (bd g7rs).

Sibling of ``incremental_discovered_from_equivalence_test`` (bd 0t5p), which
fixed the *provenance-marker* half of this same scenario: a fully-deleted
package directory's ``meta.discovered_from`` entry used to linger forever.
That fix deliberately left the *node* half out of scope -- the package's own
``package:python:<name>`` node survived the same incremental pass with zero
edges, a shape ``python_package``'s own ``_has_anchoring_member`` docstring
calls out as broken elsewhere (issue ddsy) and ``wd lint``'s orphan-detection
rule flags (see ``weld_arch_lint_orphan_test``'s
``PackageNodeOrphanDetectionTest`` for that confirmation).

Root cause (two compounding gaps, fixed together by one mechanism -- see
``weld._discover_membership_purge`` and ``weld.discovery_state.purge_stale_nodes``):

1. ``purge_stale_nodes`` matched nodes by ``props.file`` only.
   ``python_package``'s node carries ``props.dir``, never ``props.file``, so
   deleting every member file purged the member ``file:`` nodes and their
   now-dangling ``contains`` edges (correctly, via the existing
   endpoint-membership floor) but never the package node itself.
2. The per-source rerun gate checks the CURRENT glob match against
   ``dirty`` (added|modified); a fully-deleted directory's files are in
   neither, so ``python_package`` never reruns to re-derive "this package is
   gone" from the strategy side either.

The fix does not need gap 2 at all: node removal is purge-driven (unconditional
on the caller's stale-file set), not rerun-driven-by-omission, so the tests
below prove END TO END -- through the real ``discover()`` incremental path,
not just the unit-level purge call ``discovery_state_membership_purge_test``
already pins -- that one mechanism (extending the purge) is enough for full
equivalence, with no rerun-trigger change required.
"""

from __future__ import annotations

import importlib.util
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


def _write_fixture(root: Path) -> None:
    """A real python_package-backed directory plus an unrelated sibling.

    ``src/`` mirrors this repo's own "canonical Python trio" pairing
    (``.weld/discover.yaml``'s comment on the ``python_module`` +
    ``python_package`` entries): both strategies glob the identical
    ``**/*.py`` pattern, so every member file gets a ``file:`` anchor AND
    a ``package:python:*`` parent -- the pairing the real repro needs,
    since a package's ``contains`` edges only exist to purge if
    ``python_module`` (or an equivalent file-anchor strategy) is wired
    over the same glob. ``other/`` is untouched by every scenario below --
    it exists so a whole-graph comparison is non-trivial (an empty diff
    would trivially pass) and so partial-delete has an unrelated node to
    prove is undisturbed.

    ``src/__init__.py`` exports a real function rather than staying empty:
    ``python_module`` skips an export-less ``__init__.py`` entirely (no
    ``file:`` anchor, ADR 0041's ``yields_file_anchor`` rule), which would
    leave it with no ``contains`` edge of its own regardless of deletion --
    the partial-delete test below needs a member that is genuinely
    anchored and genuinely survives, not one that was never anchored to
    begin with.
    """
    src = root / "src"
    src.mkdir(exist_ok=True)
    (src / "__init__.py").write_text(
        "def init_fn():\n    return 0\n", encoding="utf-8",
    )
    (src / "mod.py").write_text("def mod_fn():\n    return 1\n", encoding="utf-8")

    other = root / "other"
    other.mkdir(exist_ok=True)
    (other / "__init__.py").write_text("", encoding="utf-8")
    (other / "thing.py").write_text("def thing_fn():\n    return 2\n", encoding="utf-8")

    weld_dir = root / ".weld"
    weld_dir.mkdir(exist_ok=True)
    (weld_dir / "discover.yaml").write_text(
        "sources:\n"
        '  - strategy: python_module\n    glob: "**/*.py"\n    type: file\n'
        '  - strategy: python_package\n    glob: "**/*.py"\n',
        encoding="utf-8",
    )


def _delete_package(root: Path) -> None:
    """Remove the whole ``src/`` package directory -- every member file
    disappears from disk, not just from the glob match."""
    shutil.rmtree(root / "src")


def _delete_one_member(root: Path) -> None:
    """Remove only ``src/mod.py``, leaving ``src/__init__.py`` in place."""
    (root / "src" / "mod.py").unlink()


def _strip_meta(graph: dict) -> dict:
    """Drop volatile keys, plus ``discovered_from`` -- its ORDER (not set)
    legitimately differs between the two construction paths (bd 8084); the
    set equality for the directory-marker shape this scenario also touches
    is ``incremental_discovered_from_equivalence_test``'s job, already
    covered there (bd 0t5p) and not duplicated here."""
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


class DeletedPackageNodeEquivalenceTest(unittest.TestCase):
    def test_incremental_matches_full_after_deleting_a_whole_package(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _git(root)
            _write_fixture(root)
            _commit(root)
            g_baseline = _discover_single_repo(root, incremental=False, write_graph=True)
            self.assertIn(
                "package:python:src", _node_ids(g_baseline),
                "fixture setup assumption broken: the baseline full run must "
                "mint package:python:src for the pre-delete round to "
                "actually exercise anything",
            )
            _delete_package(root)
            _commit(root)
            g_inc = _discover_single_repo(root, incremental=True, write_graph=True)
            # Determinism: a second incremental pass over the same
            # (now-unchanged) tree must report the identical set, not merely
            # a first-run coincidence.
            g_inc_again = _discover_single_repo(root, incremental=True, write_graph=True)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _git(root)
            _write_fixture(root)
            _delete_package(root)
            _commit(root)
            g_full = _discover_single_repo(root, incremental=False, write_graph=True)

        inc_nodes, full_nodes = _node_ids(g_inc), _node_ids(g_full)
        self.assertEqual(
            inc_nodes, full_nodes,
            f"incremental node set diverged from a full discover after "
            f"deleting a whole package (full-only="
            f"{sorted(full_nodes - inc_nodes)}, "
            f"inc-only={sorted(inc_nodes - full_nodes)})",
        )
        inc_edges, full_edges = _edge_set(g_inc), _edge_set(g_full)
        self.assertEqual(
            inc_edges, full_edges,
            f"incremental edge set diverged from a full discover after "
            f"deleting a whole package (full-only="
            f"{sorted(full_edges - inc_edges)}, "
            f"inc-only={sorted(inc_edges - full_edges)})",
        )
        self.assertEqual(
            _strip_meta(g_inc), _strip_meta(g_full),
            "deleting a whole package diverged the incremental graph from a "
            "full discover beyond just nodes/edges",
        )
        self.assertNotIn(
            "package:python:src", inc_nodes,
            "a fully-deleted package's node survived incremental discovery "
            "as a zero-edge orphan (bd g7rs)",
        )
        self.assertEqual(
            inc_nodes, _node_ids(g_inc_again),
            "node survival must be deterministic across repeated "
            "incremental passes over an unchanged tree, not merely correct "
            "on the first",
        )
        # The untouched sibling package must be wholly unaffected.
        self.assertIn("package:python:other", inc_nodes)
        self.assertIn(
            ("package:python:other", "contains", "file:other/thing"),
            inc_edges,
        )

    def test_incremental_keeps_package_node_after_partial_delete(self) -> None:
        """No over-purge: a package that loses only SOME of its members
        keeps its node, with exactly the edge to the file that is still
        there -- the observable behaviour
        ``discovery_state_membership_purge_test`` pins at the unit level,
        proven here through the real incremental discover() pipeline."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _git(root)
            _write_fixture(root)
            _commit(root)
            _discover_single_repo(root, incremental=False, write_graph=True)
            _delete_one_member(root)
            _commit(root)
            g_inc = _discover_single_repo(root, incremental=True, write_graph=True)

        inc_nodes, inc_edges = _node_ids(g_inc), _edge_set(g_inc)
        self.assertIn(
            "package:python:src", inc_nodes,
            "a package with a surviving member must keep its node",
        )
        self.assertIn(
            ("package:python:src", "contains", "file:src/__init__"), inc_edges,
        )
        self.assertNotIn(
            ("package:python:src", "contains", "file:src/mod"), inc_edges,
            "the deleted member's contains edge must not survive",
        )
        self.assertNotIn("file:src/mod", inc_nodes)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _git(root)
            _write_fixture(root)
            _delete_one_member(root)
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


def _write_csharp_fixture(root: Path) -> None:
    """A real csharp_package-backed namespace, file-anchored by the
    ``tree_sitter`` strategy (the C# file-anchor authority, ADR 0060's
    ``csharp_package`` docstring) over the identical glob -- the C#
    counterpart of ``_write_fixture``'s python_module/python_package
    pairing."""
    ns = root / "ns"
    ns.mkdir(exist_ok=True)
    (ns / "Foo.cs").write_text(
        "namespace MyNs;\npublic class Foo { public void Bar() {} }\n",
        encoding="utf-8",
    )
    (ns / "Baz.cs").write_text(
        "namespace MyNs;\npublic class Baz { public void Qux() {} }\n",
        encoding="utf-8",
    )
    weld_dir = root / ".weld"
    weld_dir.mkdir(exist_ok=True)
    (weld_dir / "discover.yaml").write_text(
        "sources:\n"
        '  - glob: "**/*.cs"\n    type: file\n    strategy: tree_sitter\n'
        "    language: csharp\n"
        '  - glob: "**/*.cs"\n    strategy: csharp_package\n',
        encoding="utf-8",
    )


#: Independent of anything csharp_package or tree_sitter's discover-path
#: strategy produce -- a plain import probe, so "is the grammar installed"
#: is never inferred from the same node-membership fact the test then goes
#: on to assert (that would make the assertion tautological with its own
#: precondition).
_CSHARP_GRAMMAR_AVAILABLE = importlib.util.find_spec("tree_sitter_c_sharp") is not None


@unittest.skipUnless(
    _CSHARP_GRAMMAR_AVAILABLE,
    "tree-sitter-c-sharp is an optional extra not in third_party/python/"
    "requirements_lock.txt (only tree-sitter-python/go/javascript/rust/cpp/"
    "typescript are Bazel-locked); csharp_package's own purge-mechanism "
    "coverage lives at the unit level in discovery_state_membership_purge_test "
    "and does not depend on this grammar being present",
)
class DeletedCsharpNamespaceNodeEquivalenceTest(unittest.TestCase):
    """The csharp_package measurement the dispatch brief asked for: the same
    mechanism (``props.roles`` contains ``"package"`` + zero surviving
    ``contains`` out-edges), unchanged, reaches csharp_package's node shape
    -- which carries neither ``props.dir`` nor ``props.file`` at all -- with
    no strategy-specific branch. Skipped rather than run vacuously when the
    optional C# grammar is not importable, so a green run here is always a
    real proof, never a trivially-satisfied empty-set comparison."""

    def test_incremental_matches_full_after_deleting_a_whole_namespace(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _git(root)
            _write_csharp_fixture(root)
            _commit(root)
            g_baseline = _discover_single_repo(root, incremental=False, write_graph=True)
            self.assertIn(
                "package:csharp:myns", _node_ids(g_baseline),
                "fixture setup assumption broken: the baseline full run "
                "must mint package:csharp:myns for the pre-delete round to "
                "actually exercise anything",
            )
            shutil.rmtree(root / "ns")
            _commit(root)
            g_inc = _discover_single_repo(root, incremental=True, write_graph=True)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _git(root)
            _write_csharp_fixture(root)
            shutil.rmtree(root / "ns")
            _commit(root)
            g_full = _discover_single_repo(root, incremental=False, write_graph=True)

        inc_nodes, full_nodes = _node_ids(g_inc), _node_ids(g_full)
        self.assertEqual(
            inc_nodes, full_nodes,
            f"incremental node set diverged from a full discover after "
            f"deleting a whole C# namespace (full-only="
            f"{sorted(full_nodes - inc_nodes)}, "
            f"inc-only={sorted(inc_nodes - full_nodes)})",
        )
        self.assertNotIn(
            "package:csharp:myns", inc_nodes,
            "a fully-deleted namespace's package node survived incremental "
            "discovery as a zero-edge orphan",
        )


if __name__ == "__main__":
    unittest.main()
