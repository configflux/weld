"""Incremental == full when an ``__init__.py`` moves a tree's source root.

ADR 0143 D5. ``weld.strategies._python_source_root_import`` reads an absolute
import against the first ancestor directory that carries no ``__init__.py``, so
that marker's *presence* is part of the resolution basis for every file beneath
it -- not just for the marker file itself. Create ``src/__init__.py`` and the
``sys.path`` entry for the whole subtree moves from ``src/`` up to the
repository root; delete it and the entry moves back down. Either way the only
file whose content changed is the marker.

That is exactly the shape ADR 0008's "incremental == full" contract is blind to
without help: the dirty set is derived from file hashes, the subtree's files
hash the same before and after, and an incremental round would keep resolutions
a full discover of the post-change tree does not produce. The violation is not
new with the generalized rule -- the shipped bare-name rule had it too, and
unpinned -- and :func:`weld._discover_incremental_merge.source_root_dependents`
is what closes it, by widening ``dirty`` **and** ``stale`` with the marker's own
subtree before the first purge-and-run pass.

Both directions are asserted, because they fail differently. On the **add** the
marker is in ``dirty`` and a strategy-side widening would appear to work, so
only the equality catches an incremental round that re-parsed the subtree
without also purging its stale edges. On the **delete** the marker reaches
``stale`` and never ``dirty``, so with nothing else changed no source runs at
all -- the round produces the prior graph unchanged, and no amount of
strategy-side scoping could have seen it.

And equality alone would be satisfied by both paths agreeing on the *wrong*
answer, so each direction also names the resolution a full discover of that
tree produces: the definite symbol under the source root, or the speculative
stub when there is no source root left to reach it through.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from weld.discover import _discover_single_repo

#: The marker whose presence decides the tree's source root. ``src/`` holds the
#: package; adding this makes the repository root the source root instead.
MARKER = "src/__init__.py"

#: Resolved through the source root ``src/``: the definition the path names.
DEFINITE = "symbol:py:src.pkg.config:load"
#: What the written spelling resolves to when there is no source root prefix to
#: reach the definition through -- a speculative stub under the import spelling.
STUB = "symbol:py:pkg.config:load"
CALLER = "symbol:py:src.pkg.runner:run"

SOURCES = (
    "  - strategy: python_module\n"
    "    glob: src/**/*.py\n"
    "    type: file\n"
    "  - strategy: python_callgraph\n"
    "    glob: src/**/*.py\n"
    "    type: symbol\n"
)


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


def _write_fixture(root: Path, *, marker: bool) -> None:
    """Write the tree, with ``src/__init__.py`` present or absent."""

    def body(rel: str, text: str) -> None:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    body("src/pkg/__init__.py", "")
    body("src/pkg/config.py", "def load():\n    return {}\n")
    body(
        "src/pkg/runner.py",
        "from pkg.config import load\n\n\ndef run():\n    return load()\n",
    )
    if marker:
        body(MARKER, "")
    else:
        (root / MARKER).unlink(missing_ok=True)
    (root / ".weld").mkdir(exist_ok=True)
    (root / ".weld" / "discover.yaml").write_text(
        "sources:\n" + SOURCES, encoding="utf-8"
    )


def _strip_meta(graph: dict) -> dict:
    g = dict(graph)
    meta = dict(g.get("meta", {}))
    for key in ("discovered_from", "updated_at", "git_sha"):
        meta.pop(key, None)
    g["meta"] = meta
    return g


def _edge_set(graph: dict) -> set[tuple[str, str, str]]:
    return {(e["from"], e["to"], e["type"]) for e in graph.get("edges", [])}


def _full_graph(*, marker: bool) -> dict:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _git(root)
        _write_fixture(root, marker=marker)
        _commit(root)
        return _discover_single_repo(root, incremental=False, write_graph=True)


def _incremental_graph(*, marker: bool) -> dict:
    """Seed a full discover of the opposite tree, flip the marker, refresh."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _git(root)
        _write_fixture(root, marker=not marker)
        _commit(root)
        _discover_single_repo(root, incremental=False, write_graph=True)
        _write_fixture(root, marker=marker)
        _commit(root)
        return _discover_single_repo(root, incremental=True, write_graph=True)


class SourceRootMarkerAnswerTest(unittest.TestCase):
    """What a full discover of each tree resolves -- named, not inferred.

    Without this the equivalence below would pass on two paths agreeing that
    nothing resolves, which is the answer the bug produced.
    """

    def test_without_the_marker_src_is_the_source_root(self) -> None:
        graph = _full_graph(marker=False)
        self.assertIn((CALLER, DEFINITE, "calls"), _edge_set(graph))
        self.assertNotIn(STUB, graph["nodes"])

    def test_with_the_marker_the_tree_has_no_reachable_source_root(self) -> None:
        """``src/`` is a package now, so ``pkg.config`` names nothing here.

        The repository root becomes the source root, where a written
        ``pkg.config`` would have to be a top-level ``pkg/`` that does not
        exist -- so the rule declines and the stub is the honest answer.
        """
        graph = _full_graph(marker=True)
        self.assertIn((CALLER, STUB, "calls"), _edge_set(graph))
        self.assertNotIn((CALLER, DEFINITE, "calls"), _edge_set(graph))


class SourceRootMarkerEquivalenceTest(unittest.TestCase):
    def test_adding_the_marker_matches_full(self) -> None:
        self.assertEqual(
            _strip_meta(_incremental_graph(marker=True)),
            _strip_meta(_full_graph(marker=True)),
        )

    def test_deleting_the_marker_matches_full(self) -> None:
        self.assertEqual(
            _strip_meta(_incremental_graph(marker=False)),
            _strip_meta(_full_graph(marker=False)),
        )

    def test_the_incremental_round_re_resolves_the_clean_subtree(self) -> None:
        """The claim behind the equality, stated where a reader meets it.

        ``src/pkg/runner.py`` is byte-identical across both rounds and is never
        dirty; what changes is the module its import resolves to. If the widen
        were dropped, the two assertions below would report the resolution the
        *previous* tree produced.
        """
        self.assertIn(
            (CALLER, STUB, "calls"), _edge_set(_incremental_graph(marker=True))
        )
        self.assertIn(
            (CALLER, DEFINITE, "calls"), _edge_set(_incremental_graph(marker=False))
        )


if __name__ == "__main__":
    unittest.main()
