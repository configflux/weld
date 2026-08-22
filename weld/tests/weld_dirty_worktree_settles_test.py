"""`wd discover` must settle `source_stale` in a dirty worktree (bd 0jay).

The ADR 0017 working-tree dimension asked git a question about **HEAD** --
"is any tracked source uncommitted-dirty?" -- and `wd discover` commits
nothing, so its answer was invariant under discovery. One uncommitted edit to
a tracked source latched `source_stale` True forever: `wd discover` reported
success, `wd stale` still answered `source_stale: yes` with
`graph_sha == current_sha` and `commits_behind: 0`, and every stale-gated
command (`wd impact --working-tree`) stayed refused with an error message
prescribing the fix that did not fix it.

These tests drive **real** discovery end to end, which is what the pre-existing
`weld_dirty_worktree_staleness_test` suite does not: it hand-writes a graph
meta and never lays down a `discovery-state.json`, so it exercises (and still
pins) the no-inventory fallback and could never have caught this.

Pinned here, per the amendment to ADR 0017:

- the settle itself -- discover, and the dirty tree reports fresh;
- every shape that must still report stale before discovery, and must then
  settle rather than re-latch: a modified ingested file, a new in-scope file,
  a deleted ingested file (whose deletion stays dirty after discovery drops
  it), and a rename of an in-scope file to an out-of-scope name;
- an out-of-scope dirty path under a broad ``./`` ``discovered_from``, which
  is not a graph input and must never latch;
- the fallback: with no inventory to compare against, dirt still means stale.

Direct coverage of the boolean divergence predicate
(``weld._staleness_worktree.dirty_sources_diverge``) lives here too; its
full-enumeration companion has its own sibling suite,
``weld_dirty_worktree_divergence_detail_test``, sharing this module's fixture
via ``weld.tests._dirty_tree_lib``.
"""

from __future__ import annotations

import unittest

from weld._stale_reasons import (  # noqa: E402
    CONTENT_DIFFERS,
    INGESTED_FILE_VANISHED,
    NEVER_INGESTED,
)
from weld._staleness_worktree import dirty_sources_diverge  # noqa: E402
from weld.tests._dirty_tree_lib import DirtyTreeFixture  # noqa: E402


class DiscoverSettlesDirtyWorktreeTest(DirtyTreeFixture):
    """The reported bug and its neighbours, each through a real discover."""

    def test_committed_clean_tree_is_fresh(self) -> None:
        self.assertSettled("a freshly discovered clean tree must be fresh")

    def test_modified_source_is_stale_then_settles(self) -> None:
        # The reported bug. HEAD never moves, so the only signal in play is
        # the working-tree dimension; before this fix the second assertion
        # failed and no number of discovers could clear it.
        (self.root / "src" / "a.py").write_text(
            "def a():\n    return 99\n", encoding="utf-8"
        )
        self.assertStale("an uncommitted edit must mark the graph stale")
        info = self.stale()
        self.assertFalse(
            info["sha_behind"],
            f"the repro shape requires an unmoved HEAD: {info}",
        )
        self.assertEqual(info["commits_behind"], 0, info)
        # The verdict now names the file and why.
        self.assertEqual(
            info["stale_sources"],
            [{"path": "src/a.py", "reason": CONTENT_DIFFERS}],
        )
        self.assertEqual(info["stale_sources_omitted"], 0)
        self.discover()
        self.assertSettled("discover must settle an uncommitted edit")

    def test_new_in_scope_file_is_stale_then_settles(self) -> None:
        # Untracked and never ingested: the graph is genuinely blind to it,
        # so it must report stale -- and settle once discovery ingests it.
        # Strategies record ``discovered_from`` per file, so a path that
        # exists in neither the graph nor the index is not "tracked" and
        # never reaches the working-tree dimension at all: this arrives via
        # ADR 0101's coverage probe. Pinned here anyway because it is an
        # acceptance criterion in its own right, and because the settle half
        # is what a regression in either signal would break.
        (self.root / "src" / "c.py").write_text(
            "def c():\n    return 3\n", encoding="utf-8"
        )
        self.assertStale("a new in-scope file must mark the graph stale")
        # Arrives via the coverage probe (see comment above), so it is
        # coverage_stale_detail's reason, not the working-tree one.
        self.assertEqual(
            self.stale()["stale_sources"],
            [{"path": "src/c.py", "reason": NEVER_INGESTED}],
        )
        self.discover()
        self.assertSettled("discover must settle a newly ingested file")

    def test_new_in_scope_file_under_root_prefix_is_stale_then_settles(
        self,
    ) -> None:
        # The same file through the working-tree dimension: under a broad
        # ``./`` it *is* tracked, so it is dirty, unrecorded, on disk and in
        # scope -- the one branch that must report stale on an unrecorded
        # path, and must stop doing so once the inventory holds it.
        (self.root / "src" / "c.py").write_text(
            "def c():\n    return 3\n", encoding="utf-8"
        )
        self.assertIn(
            "src/c.py",
            self.dirty(["./"]),
            "fixture must put the new file in the dirty set",
        )
        self.assertTrue(self.stale(discovered_from=["./"])["source_stale"])
        self.discover()
        info = self.stale(discovered_from=["./"])
        self.assertFalse(
            info["source_stale"],
            f"discover must settle a newly ingested untracked file: {info}",
        )

    def test_deleted_source_is_stale_then_settles(self) -> None:
        # The re-latch trap: after discovery drops the file from the
        # inventory the deletion is *still* uncommitted, so the path is
        # still dirty and still resolves in scope. It must not read as a
        # never-ingested source -- nothing is on disk to ingest.
        (self.root / "src" / "b.py").unlink()
        self.assertStale("a deleted ingested source must mark the graph stale")
        self.assertEqual(
            self.stale()["stale_sources"],
            [{"path": "src/b.py", "reason": INGESTED_FILE_VANISHED}],
        )
        self.discover()
        self.assertSettled("discover must settle a deletion")

    def test_rename_out_of_scope_is_stale_then_settles(self) -> None:
        # Rename detection would report only the new path, hiding the
        # vacated original; the dirty set is listed with renames off so the
        # original surfaces as a deletion of an ingested file.
        (self.root / "src" / "b.py").rename(self.root / "src" / "b.py.bak")
        self.assertStale("a renamed-away ingested source must mark stale")
        self.discover()
        self.assertSettled("discover must settle a rename out of scope")

    def test_out_of_scope_dirt_never_latches(self) -> None:
        # ``./`` is the default ``wd init`` shape, under which every path in
        # the repo is "tracked". ``notes.txt`` is matched by no source entry,
        # so discovery can never ingest it -- flagging it would be staleness
        # no discover could clear.
        (self.root / "notes.txt").write_text("edited\n", encoding="utf-8")
        self.assertIn(
            "notes.txt",
            self.dirty(["./"]),
            "fixture must make the out-of-scope file dirty, or this passes "
            "for the wrong reason",
        )
        info = self.stale(discovered_from=["./"])
        self.assertFalse(
            info["source_stale"],
            f"dirt outside every source glob is not a graph input: {info}",
        )

    def test_in_scope_dirt_under_root_prefix_still_stale(self) -> None:
        # The other half of the case above: a broad ``./`` must not become a
        # blanket exemption.
        (self.root / "src" / "a.py").write_text(
            "def a():\n    return 7\n", encoding="utf-8"
        )
        info = self.stale(discovered_from=["./"])
        self.assertTrue(info["source_stale"], info)


class NoInventoryFallbackTest(DirtyTreeFixture):
    """With nothing recorded to compare against, dirt still means stale.

    Each case starts from a *settled* dirty tree -- edited, then discovered,
    so the inventory holds exactly what is on disk and the uncommitted edit no
    longer marks the graph stale. Removing the basis is then the only change
    in play, and the assertion cannot pass for the ordinary reason.
    """

    def setUp(self) -> None:
        super().setUp()
        (self.root / "src" / "a.py").write_text(
            "def a():\n    return 4\n", encoding="utf-8"
        )
        self.discover()
        self.assertSettled("fixture must start from a settled dirty tree")

    def test_missing_state_falls_back_to_dirty_means_stale(self) -> None:
        (self.root / ".weld" / "discovery-state.json").unlink()
        self.assertStale("no inventory must fall back to dirty => stale")
        # The bool gate stays conservative (stale=True), but with no
        # inventory to compare against there is no per-path evidence to
        # name -- under-report rather than invent one.
        self.assertEqual(self.stale()["stale_sources"], [])

    def test_missing_config_falls_back_to_dirty_means_stale(self) -> None:
        # Without ``sources`` there is no way to tell an un-ingested source
        # from a file discovery would never read, so the conservative answer
        # is the only sound one.
        (self.root / ".weld" / "discover.yaml").unlink()
        self.assertStale("no sources config must fall back to dirty => stale")


class SettledTreeStillDefersToVouchingTest(DirtyTreeFixture):
    """A settled dirty tree must not vouch for a graph the inventory cannot.

    The working-tree branch trusts inventory hashes, and an inventory that
    describes a *different* graph body than the one on disk (ADR 0101 / bd
    wq9i) would otherwise let it report fresh for content no reader can see.
    What stops it is ordering alone: ``coverage_stale`` runs immediately after
    this branch clears and refuses a non-vouching inventory. Reorder those two
    and this test is the one that fails.
    """

    def test_foreign_graph_body_under_a_settled_tree_is_stale(self) -> None:
        import json

        (self.root / "src" / "a.py").write_text(
            "def a():\n    return 42\n", encoding="utf-8"
        )
        self.discover()
        self.assertSettled("fixture must start from a settled dirty tree")
        body = json.loads(self.graph_path.read_text(encoding="utf-8"))
        self.assertTrue(body["nodes"], "fixture graph must have nodes to drop")
        body["nodes"], body["edges"] = {}, []
        self.graph_path.write_text(
            json.dumps(body, indent=2), encoding="utf-8"
        )
        info = self.stale()
        self.assertTrue(
            info["source_stale"],
            f"an inventory that cannot vouch must not read fresh: {info}",
        )
        self.assertTrue(info["coverage_stale"], info)


class DirtySourcesDivergeUnitTest(DirtyTreeFixture):
    """Direct coverage of the predicate the staleness check delegates to."""

    def test_empty_dirty_set_does_not_diverge(self) -> None:
        self.assertFalse(dirty_sources_diverge(self.root, []))

    def test_unchanged_ingested_file_does_not_diverge(self) -> None:
        self.assertFalse(dirty_sources_diverge(self.root, ["src/a.py"]))

    def test_changed_ingested_file_diverges(self) -> None:
        (self.root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
        self.assertTrue(dirty_sources_diverge(self.root, ["src/a.py"]))

    def test_vanished_ingested_file_diverges(self) -> None:
        (self.root / "src" / "a.py").unlink()
        self.assertTrue(dirty_sources_diverge(self.root, ["src/a.py"]))

    def test_unknown_in_scope_file_on_disk_diverges(self) -> None:
        (self.root / "src" / "c.py").write_text("c = 1\n", encoding="utf-8")
        self.assertTrue(dirty_sources_diverge(self.root, ["src/c.py"]))

    def test_unknown_path_not_on_disk_does_not_diverge(self) -> None:
        self.assertFalse(dirty_sources_diverge(self.root, ["src/gone.py"]))

    def test_unknown_out_of_scope_file_does_not_diverge(self) -> None:
        (self.root / "scratch.log").write_text("x\n", encoding="utf-8")
        self.assertFalse(dirty_sources_diverge(self.root, ["scratch.log"]))

    def test_one_divergence_among_many_wins(self) -> None:
        (self.root / "src" / "b.py").write_text("b = 9\n", encoding="utf-8")
        self.assertTrue(
            dirty_sources_diverge(
                self.root, ["src/a.py", "notes.txt", "src/b.py"]
            )
        )

    def test_missing_inventory_is_undecidable_and_diverges(self) -> None:
        (self.root / ".weld" / "discovery-state.json").unlink()
        self.assertTrue(dirty_sources_diverge(self.root, ["src/a.py"]))


if __name__ == "__main__":
    unittest.main()
