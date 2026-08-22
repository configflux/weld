"""Provenance-keyed edge purge keeps ``markdown`` ``relates_to`` edges.

Third sibling of ``incremental_callgraph_provenance_purge_test`` and
``incremental_test_peer_provenance_purge_test``, covering the same ADR 0074
contract for the last cross-file edge kind a glob-scoped strategy minted
without provenance (bd 41vw).

Read the intent carefully, because it differs from its siblings. bd 41vw was
filed on a survey finding -- ``markdown``'s ``relates_to`` edge carried no
``props.provenance.file``, the same shape that made ``test_peer`` lose edges
in bd heum -- and predicted the same loss. **It does not reproduce.** The
strategy only emits an edge when the target resolves inside ``path_to_nid``,
i.e. when the target would itself be minted by the same ``extract`` call, so
the producing file and both endpoints always share one glob. Purging either
endpoint implies a dirty file inside this strategy's own source entry, which
re-runs it over the whole glob and re-mints every edge. heum needed *disjoint*
globs -- the dirty production file sat in ``python_module``'s glob while the
producing test file sat in ``test_peer``'s, so ``test_peer`` never re-ran --
and that disjointness cannot arise here.

So none of these assertions failed before the stamp landed, and that is the
point: they pin the *shape*, not a fix. The safety above is incidental --
it rests on the strategy re-reading every matched ``.md`` whenever any one of
them is dirty, exactly the cost ADR 0084 removed for ``python_module`` by
giving it a dirty scope. Give ``markdown`` the same scope and
:class:`DirtyLinkTargetKeepsRelatesToEdgeTest` is what fails. The stamp makes
the ADR 0074 retention contract explicit rather than emergent, so that
optimisation stays safe to make.

:class:`DeletedLinkTargetDropsRelatesToEdgeTest` covers the one case the
stamp could newly *break*: provenance now retains the edge through a purge
that previously dropped it on endpoint membership, so the post-process
dangling-edge filter has to be the thing that removes it.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from weld.discover import _discover_single_repo

#: The mentioning doc and the doc it links to, in one ``docs/*.md`` glob.
_SRC = "docs/guide.md"
_EDGE = ("doc:docs/guide", "doc:docs/target")


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


def _relates_edges(graph: dict) -> list[tuple[str, str]]:
    return sorted(
        (e["from"], e["to"])
        for e in graph.get("edges", [])
        if e.get("type") == "relates_to"
    )


def _edge_provenance(graph: dict) -> object:
    """Return the ``provenance`` prop of the fixture's ``relates_to`` edge.

    Returns the sentinel ``"<no edge>"`` when the edge is absent, so a
    missing edge fails the provenance assertion with a legible value rather
    than an ``AttributeError`` on ``None``.
    """
    for e in graph.get("edges", []):
        if e.get("type") == "relates_to" and (e["from"], e["to"]) == _EDGE:
            return (e.get("props") or {}).get("provenance")
    return "<no edge>"


def _write_fixture(
    root: Path,
    src_body: str,
    target_body: str,
    *,
    delete_target: bool = False,
    drop_link: bool = False,
) -> None:
    """Lay down the two-doc fixture and its single-source discover.yaml.

    One ``markdown`` source entry owns both files -- not a simplification but
    the strategy's actual constraint: an edge is only emitted when the target
    is in the *same* glob, so a fixture that split them could not produce an
    edge at all.
    """
    docs = root / "docs"
    docs.mkdir(exist_ok=True)
    link = "" if drop_link else "See [the target](target.md) for more.\n"
    (docs / "guide.md").write_text(
        f"# Guide\n\n{src_body}\n\n{link}", encoding="utf-8",
    )
    target = docs / "target.md"
    if delete_target:
        if target.exists():
            target.unlink()
    else:
        target.write_text(f"# Target\n\n{target_body}\n", encoding="utf-8")
    weld_dir = root / ".weld"
    weld_dir.mkdir(exist_ok=True)
    (weld_dir / "discover.yaml").write_text(
        "sources:\n"
        "  - glob: docs/*.md\n"
        "    type: doc\n"
        "    strategy: markdown\n"
        "    id_prefix: doc:docs\n",
        encoding="utf-8",
    )


def _seed_then_edit(src_body: str, target_body: str, **kw: bool) -> dict:
    """Full-discover the ``"1"`` state, apply the edit, refresh incrementally."""
    with tempfile.TemporaryDirectory(prefix="md-prov-inc-") as td:
        root = Path(td)
        _git(root)
        _write_fixture(root, "1", "1")
        _commit(root)
        _discover_single_repo(root, incremental=False, write_graph=True)
        _write_fixture(root, src_body, target_body, **kw)
        _commit(root)
        return _discover_single_repo(root, incremental=True, write_graph=True)


def _full_at(src_body: str, target_body: str, **kw: bool) -> dict:
    """Full-discover a clean checkout of the post-edit state."""
    with tempfile.TemporaryDirectory(prefix="md-prov-full-") as td:
        root = Path(td)
        _git(root)
        _write_fixture(root, src_body, target_body, **kw)
        _commit(root)
        return _discover_single_repo(root, incremental=False, write_graph=True)


class DirtyLinkTargetKeepsRelatesToEdgeTest(unittest.TestCase):
    """Edit the linked-to doc; the mentioning doc stays clean.

    The heum shape transplanted to ``markdown``: the purged endpoint is the
    one the producing file merely points at. Green before the stamp because
    the dirty target is inside the strategy's own glob and forces a whole-glob
    re-run; this is the test that goes red if that re-run is ever narrowed to
    the dirty file.
    """

    def test_relates_to_edge_survives_and_graph_matches_full(self) -> None:
        g_inc = _seed_then_edit("1", "2")
        g_full = _full_at("1", "2")

        self.assertIn(
            _EDGE, _relates_edges(g_inc),
            "markdown 'relates_to' edge into the dirty link target was "
            "purged and never re-minted -- ADR 0074 provenance regression",
        )
        self.assertEqual(_relates_edges(g_inc), _relates_edges(g_full))
        self.assertEqual(
            _strip_meta(g_inc), _strip_meta(g_full),
            "incremental graph (nodes+edges, sans volatile meta) must be "
            "byte-identical to a full discover at the same source state",
        )

    def test_edge_names_the_mentioning_file_as_its_producing_file(self) -> None:
        """The stamp itself: provenance is the *mentioning* doc, both paths.

        ADR 0074 keys purge on "the file that produced the edge". For
        ``markdown`` that is the file whose body held the link, never the
        target it resolved -- pinning the direction here is what stops a
        later change from stamping the endpoint and quietly inverting the
        contract, since a target-stamped edge is attributed to exactly the
        file that is dirty in the case retention has to survive.
        """
        for label, graph in (("full", _full_at("1", "2")),
                             ("incremental", _seed_then_edit("1", "2"))):
            with self.subTest(path=label):
                self.assertEqual(
                    _edge_provenance(graph), {"file": _SRC},
                    "relates_to edge must name the markdown file it was "
                    "derived from as props.provenance.file",
                )


class DirtyMentioningDocMatchesFullTest(unittest.TestCase):
    """The other direction: edit the doc that holds the link.

    Under the stamp the prior edge is now purged *by* provenance rather than
    by endpoint membership, and the re-run must still put back exactly one.
    """

    def test_graph_matches_full(self) -> None:
        g_inc = _seed_then_edit("2", "1")
        g_full = _full_at("2", "1")

        self.assertEqual(_relates_edges(g_inc), [_EDGE])
        self.assertEqual(
            _strip_meta(g_inc), _strip_meta(g_full),
            "edit-the-mentioning-doc incremental graph must be byte-identical "
            "to a full discover at the same source state",
        )


class DeletedLinkTargetDropsRelatesToEdgeTest(unittest.TestCase):
    """A deleted link target must not leave a dangling ``relates_to`` edge.

    The safety check on the new retention, and the one case the stamp could
    newly break. A deletion is not a dirty file, so the source entry does not
    re-run; provenance now keeps the edge through the purge even though its
    target node is gone, which makes the post-process dangling-edge filter --
    not endpoint membership -- the thing that has to drop it. A full discover
    never emits the edge at all, since the target no longer resolves.
    """

    def test_deleted_target_matches_full(self) -> None:
        g_inc = _seed_then_edit("1", "1", delete_target=True)
        g_full = _full_at("1", "1", delete_target=True)

        self.assertNotIn(
            "doc:docs/target", g_inc.get("nodes", {}),
            "deleted link target must not survive incrementally",
        )
        self.assertEqual(
            _relates_edges(g_inc), [],
            "provenance survival must not resurrect a relates_to edge whose "
            "target was genuinely deleted",
        )
        self.assertEqual(
            _strip_meta(g_inc), _strip_meta(g_full),
            "deleted-target incremental graph must match a full discover",
        )


class RemovedLinkDropsRelatesToEdgeTest(unittest.TestCase):
    """Delete the link, keep both docs: the edge must not survive its cause.

    Distinct from the deleted-target case -- here both endpoints live on, so
    only provenance can tell the purge that the edge belongs to the dirty
    mentioning doc and must go. Retaining it would be the silent staleness
    ADR 0074's second amendment rejected the widened floor to avoid.
    """

    def test_removed_link_matches_full(self) -> None:
        g_inc = _seed_then_edit("1", "1", drop_link=True)
        g_full = _full_at("1", "1", drop_link=True)

        self.assertEqual(
            _relates_edges(g_inc), [],
            "relates_to edge outlived the link that produced it",
        )
        self.assertEqual(
            _strip_meta(g_inc), _strip_meta(g_full),
            "removed-link incremental graph must match a full discover",
        )


if __name__ == "__main__":
    unittest.main()
