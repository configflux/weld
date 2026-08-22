"""Provenance-keyed edge purge keeps markdown's ``documents`` code-citation
edges (ADR 0128, bd ziv1).

Sibling of ``incremental_test_peer_provenance_purge_test`` and
``incremental_markdown_provenance_purge_test``, but distinct from the
latter in the one way that matters: the existing ``relates_to`` inter-doc
link pass only ever links *within* the ``markdown`` strategy's own glob
(both endpoints must resolve inside the same ``extract()`` call), so a
purged endpoint always sits inside a source entry that is about to
re-run anyway. The new ``documents`` code-citation pass has no such
luxury -- a citing doc (``docs/*.md``, owned by ``markdown``) and a cited
module (``lib/*.py``, owned by ``python_module``) are *always* two
disjoint source entries. This is exactly the ``test_peer`` (bd heum)
shape: dirty the cited code, the citing doc's own glob stays entirely
clean, and only the ``props.provenance.file`` stamp keeps the edge from
falling to the conservative endpoint-membership purge floor and being
lost until the next full discover.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from weld.discover import _discover_single_repo

#: The citing doc and the cited module, in two disjoint source entries.
_DOC = "docs/guide.md"
_CODE = "lib/thing.py"
_EDGE = ("doc:docs/guide", "file:lib/thing")


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


def _documents_edges(graph: dict) -> list[tuple[str, str]]:
    return sorted(
        (e["from"], e["to"])
        for e in graph.get("edges", [])
        if e.get("type") == "documents" and e["from"] == "doc:docs/guide"
    )


def _edge_provenance(graph: dict) -> object:
    """Return the ``provenance`` prop of the fixture's ``documents`` edge.

    Returns the sentinel ``"<no edge>"`` when the edge is absent, so a
    missing edge fails the provenance assertion with a legible value
    rather than an ``AttributeError`` on ``None``.
    """
    for e in graph.get("edges", []):
        if e.get("type") == "documents" and (e["from"], e["to"]) == _EDGE:
            return (e.get("props") or {}).get("provenance")
    return "<no edge>"


def _write_fixture(
    root: Path,
    doc_extra: str,
    code_body: str,
    *,
    delete_code: bool = False,
    drop_citation: bool = False,
) -> None:
    """Lay down the two-file fixture and its two-source discover.yaml.

    ``markdown`` owns the doc glob and ``python_module`` the code glob --
    deliberately disjoint (unlike the intra-glob doc->doc link pass), so a
    dirty cited module leaves the citing doc's own glob entirely clean.
    """
    docs = root / "docs"
    docs.mkdir(exist_ok=True)
    citation = "" if drop_citation else "See `lib/thing.py` for details.\n"
    (docs / "guide.md").write_text(
        f"# Guide\n\n{doc_extra}\n\n{citation}", encoding="utf-8",
    )
    lib = root / "lib"
    lib.mkdir(exist_ok=True)
    code_path = lib / "thing.py"
    if delete_code:
        if code_path.exists():
            code_path.unlink()
    else:
        code_path.write_text(
            f"def run():\n    return {code_body}\n", encoding="utf-8",
        )
    weld_dir = root / ".weld"
    weld_dir.mkdir(exist_ok=True)
    (weld_dir / "discover.yaml").write_text(
        "sources:\n"
        "  - glob: docs/*.md\n    type: doc\n    strategy: markdown\n"
        "    id_prefix: doc:docs\n"
        "  - glob: lib/*.py\n    type: file\n    strategy: python_module\n",
        encoding="utf-8",
    )


def _seed_then_edit(doc_extra: str, code_body: str, **kw: bool) -> dict:
    """Full-discover the ``"1"`` state, apply the edit, refresh incrementally."""
    with tempfile.TemporaryDirectory(prefix="md-coderef-inc-") as td:
        root = Path(td)
        _git(root)
        _write_fixture(root, "1", "1")
        _commit(root)
        _discover_single_repo(root, incremental=False, write_graph=True)
        _write_fixture(root, doc_extra, code_body, **kw)
        _commit(root)
        return _discover_single_repo(root, incremental=True, write_graph=True)


def _full_at(doc_extra: str, code_body: str, **kw: bool) -> dict:
    """Full-discover a clean checkout of the post-edit state."""
    with tempfile.TemporaryDirectory(prefix="md-coderef-full-") as td:
        root = Path(td)
        _git(root)
        _write_fixture(root, doc_extra, code_body, **kw)
        _commit(root)
        return _discover_single_repo(root, incremental=False, write_graph=True)


class DirtyCitedModuleKeepsDocumentsEdgeTest(unittest.TestCase):
    """bd heum shape: edit the cited module; the citing doc stays clean.

    Green before the stamp only by accident of a conservative purge that
    would have dropped the edge -- this is the test that pins the fix,
    not merely the shape (unlike the intra-glob relates_to pass, this one
    really does lose the edge without ``props.provenance.file``).
    """

    def test_documents_edge_survives_and_graph_matches_full(self) -> None:
        g_inc = _seed_then_edit("1", "2")
        g_full = _full_at("1", "2")

        self.assertIn(
            _EDGE, _documents_edges(g_inc),
            "markdown 'documents' edge into the dirty cited module was "
            "purged and never re-minted (the doc glob holds no dirty "
            "file, so the strategy never re-runs) -- ADR 0074 provenance "
            "regression",
        )
        self.assertEqual(_documents_edges(g_inc), _documents_edges(g_full))
        self.assertEqual(
            _strip_meta(g_inc), _strip_meta(g_full),
            "incremental graph (nodes+edges, sans volatile meta) must be "
            "byte-identical to a full discover at the same source state",
        )

    def test_edge_names_the_citing_doc_as_its_producing_file(self) -> None:
        """The stamp itself: provenance is the *citing doc*, both paths."""
        for label, graph in (("full", _full_at("1", "2")),
                             ("incremental", _seed_then_edit("1", "2"))):
            with self.subTest(path=label):
                self.assertEqual(
                    _edge_provenance(graph), {"file": _DOC},
                    "documents edge must name the citing doc it was "
                    "derived from as props.provenance.file",
                )


class DirtyCitingDocMatchesFullTest(unittest.TestCase):
    """The other direction: edit the citing doc; the cited module stays clean."""

    def test_graph_matches_full(self) -> None:
        g_inc = _seed_then_edit("2", "1")
        g_full = _full_at("2", "1")

        self.assertEqual(_documents_edges(g_inc), [_EDGE])
        self.assertEqual(
            _strip_meta(g_inc), _strip_meta(g_full),
            "edit-the-citing-doc incremental graph must be byte-identical "
            "to a full discover at the same source state",
        )


class DeletedCitedModuleDropsDocumentsEdgeTest(unittest.TestCase):
    """A deleted cited module must not leave a dangling ``documents`` edge.

    Provenance keeps the edge through the graph-merge purge, but the
    module node is gone and never re-minted, so the post-process
    dangling-edge filter must drop it -- matching a full discover, where
    the citation never resolves at all.
    """

    def test_deleted_module_matches_full(self) -> None:
        g_inc = _seed_then_edit("1", "1", delete_code=True)
        g_full = _full_at("1", "1", delete_code=True)

        self.assertNotIn(
            "file:lib/thing", g_inc.get("nodes", {}),
            "deleted cited module must not survive incrementally",
        )
        self.assertEqual(
            _documents_edges(g_inc), [],
            "provenance survival must not resurrect a documents edge "
            "whose target was genuinely deleted",
        )
        self.assertEqual(
            _strip_meta(g_inc), _strip_meta(g_full),
            "deleted-module incremental graph must match a full discover",
        )


class RemovedCitationDropsDocumentsEdgeTest(unittest.TestCase):
    """Delete the citation, keep both files: the edge must not survive it.

    Distinct from the deleted-module case -- here both endpoints live on,
    so only provenance can tell the purge the edge belongs to the dirty
    citing doc and must go. The doc glob DOES re-run here (the doc itself
    is dirty), so this also doubles as the re-run-produces-nothing-new
    check.
    """

    def test_removed_citation_matches_full(self) -> None:
        g_inc = _seed_then_edit("1", "1", drop_citation=True)
        g_full = _full_at("1", "1", drop_citation=True)

        self.assertEqual(
            _documents_edges(g_inc), [],
            "documents edge outlived the citation that produced it",
        )
        self.assertEqual(
            _strip_meta(g_inc), _strip_meta(g_full),
            "removed-citation incremental graph must match a full discover",
        )


if __name__ == "__main__":
    unittest.main()
