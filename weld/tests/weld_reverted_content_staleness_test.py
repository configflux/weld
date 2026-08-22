"""An edit that is discovered, then reverted, still diverges (bd lhye).

Sibling to ``weld_dirty_worktree_settles_test`` and
``weld_dirty_worktree_divergence_detail_test`` (shares their fixture via
``weld.tests._dirty_tree_lib``, split into its own file per that same
line-count precedent): those suites pin the three ADR 0017 / 0101 signals
that all need something -- a commit-range diff, a git-dirty entry, or an
uncovered path -- to point at a file before they look at it. A file edited,
then discovered, then reverted to its committed content clears every one of
those pointers at once: git reports it clean again, the commit range is
empty, and the file is already in the inventory. This suite pins the fourth
signal that closes that gap, ``weld._staleness_reverted.reverted_content_stale``
(ADR 0017's fourth amendment), plus the false-fresh regression test the
amendment's stamping objection demands: a non-discovery writer (``wd
add-node --merge``) must not be able to launder a still-diverging file into
looking checked.
"""

from __future__ import annotations

import os
import unittest

from weld._stale_reasons import CONTENT_DIFFERS, INGESTED_FILE_VANISHED  # noqa: E402
from weld._staleness_reverted import reverted_content_stale  # noqa: E402
from weld.graph import Graph  # noqa: E402
from weld.tests._dirty_tree_lib import DirtyTreeFixture  # noqa: E402


class RevertedContentStillDivergesTest(DirtyTreeFixture):
    """The reported bug, end to end through real discovery."""

    def test_edit_discover_revert_is_stale_then_settles(self) -> None:
        original = (self.root / "src" / "a.py").read_text(encoding="utf-8")
        (self.root / "src" / "a.py").write_text(
            "def a():\n    return 99\n", encoding="utf-8"
        )
        self.discover()
        self.assertSettled("a freshly discovered edit must settle")

        # Revert to the committed content with a fresh write (not a git
        # restore), so this exercises the "an editor undid the edit" shape
        # the issue reports, not a git-specific code path.
        (self.root / "src" / "a.py").write_text(original, encoding="utf-8")
        self.assertEqual(
            self.dirty(["src/"]), [],
            "a reverted file must leave git status clean, or this is not "
            "the reported bug",
        )
        info = self.stale()
        self.assertTrue(
            info["source_stale"],
            f"a reverted edit must still read stale: {info}",
        )
        self.assertFalse(info["sha_behind"], info)
        self.assertEqual(info["commits_behind"], 0, info)
        self.assertEqual(
            info["stale_sources"],
            [{"path": "src/a.py", "reason": CONTENT_DIFFERS}],
        )
        self.assertEqual(info["stale_sources_omitted"], 0)

        self.discover()
        self.assertSettled("re-discovering after a revert must settle it")

    def test_untracked_file_deleted_without_staging_is_caught(self) -> None:
        # An untracked file that is ingested and then deleted before ever
        # being staged leaves no trace in `git status` at all -- git never
        # knew about it to report the removal -- so the working-tree signal
        # never reaches it. This signal does not wait for git to point
        # first.
        (self.root / "src" / "c.py").write_text(
            "def c():\n    return 3\n", encoding="utf-8"
        )
        self.discover()
        self.assertSettled("a newly ingested untracked file must settle")

        (self.root / "src" / "c.py").unlink()
        self.assertEqual(
            self.dirty(["src/"]), [],
            "an untracked-then-deleted file must leave no git-status "
            "trace, or this is not exercising the gap",
        )
        info = self.stale()
        self.assertTrue(info["source_stale"], info)
        self.assertEqual(
            info["stale_sources"],
            [{"path": "src/c.py", "reason": INGESTED_FILE_VANISHED}],
        )


class NonDiscoveryWriterPreservesBasisTest(DirtyTreeFixture):
    """A writer that cannot see source content must not launder staleness.

    This is the false-fresh objection the ADR's fourth amendment has to
    answer: a writer that stamped a new "dirty at discovery" basis wrongly
    would manufacture a worse blind spot than the one being fixed. This
    design has no new stamping site for such a writer to get wrong -- proven
    here by showing ``wd add-node --merge``'s underlying call
    (``Graph.add_node`` + ``Graph.save(touch_git_sha=True)``) leaves
    ``discovery-state.json`` byte-identical and does not clear a divergence
    the fourth signal already found.
    """

    def test_add_node_neither_clears_nor_forges_the_basis(self) -> None:
        original = (self.root / "src" / "a.py").read_text(encoding="utf-8")
        (self.root / "src" / "a.py").write_text(
            "def a():\n    return 99\n", encoding="utf-8"
        )
        self.discover()
        (self.root / "src" / "a.py").write_text(original, encoding="utf-8")
        self.assertStale("fixture must start from the reported bug shape")

        state_path = self.root / ".weld" / "discovery-state.json"
        before_bytes = state_path.read_bytes()
        before_mtime_ns = state_path.stat().st_mtime_ns

        g = Graph(self.root)
        g.load()
        g.add_node("entity:Manual", "entity", "Manual", {"description": "x"})
        g.save(touch_git_sha=True)

        self.assertEqual(
            state_path.read_bytes(), before_bytes,
            "a non-discovery writer must not touch discovery-state.json",
        )
        self.assertEqual(
            state_path.stat().st_mtime_ns, before_mtime_ns,
            "a non-discovery writer must not move the reference point "
            "reverted_content_stale bounds its stat pass against",
        )
        self.assertStale(
            "add-node must not clear a divergence it never checked"
        )


class RevertedContentStaleUnitTest(DirtyTreeFixture):
    """Direct coverage of the predicate the fourth signal delegates to."""

    def test_unchanged_inventory_reports_nothing(self) -> None:
        self.assertEqual(reverted_content_stale(self.root), [])

    def test_missing_inventory_reports_nothing(self) -> None:
        (self.root / ".weld" / "discovery-state.json").unlink()
        self.assertEqual(reverted_content_stale(self.root), [])

    def test_reverted_file_is_named(self) -> None:
        original = (self.root / "src" / "a.py").read_text(encoding="utf-8")
        (self.root / "src" / "a.py").write_text(
            "def a():\n    return 7\n", encoding="utf-8"
        )
        self.discover()
        (self.root / "src" / "a.py").write_text(original, encoding="utf-8")
        self.assertEqual(
            reverted_content_stale(self.root),
            [{"path": "src/a.py", "reason": CONTENT_DIFFERS}],
        )

    def test_stale_content_with_old_mtime_is_not_detected(self) -> None:
        # Pins the ADR's documented "Accepted limitation": content that
        # diverges but whose mtime predates the inventory's own write is
        # trusted without a re-read. Proves the stat bound actually skips
        # the hash, not merely that the outcome happens to agree.
        path = self.root / "src" / "a.py"
        state_path = self.root / ".weld" / "discovery-state.json"
        path.write_text("def a():\n    return 123\n", encoding="utf-8")
        backdated_ns = state_path.stat().st_mtime_ns - 10_000_000_000
        os.utime(path, ns=(backdated_ns, backdated_ns))
        self.assertEqual(
            reverted_content_stale(self.root), [],
            "a backdated mtime is the documented, accepted miss",
        )


class MalformedInventoryKeyTest(DirtyTreeFixture):
    """A hand-tampered or corrupted state file must not escape *root*.

    ``discovery-state.json`` is gitignored and, under ordinary operation,
    written only by the validated discovery pipeline (bd a4q8's containment
    check). These tests simulate the file having been corrupted or
    hand-edited anyway -- the only way an untrusted key could ever reach
    this signal -- and pin that it is silently skipped rather than turned
    into a filesystem read outside the repository.
    """

    def _inject_key(self, rel: str) -> None:
        from weld.discovery_state import load_state, save_state

        state = load_state(self.root)
        assert state is not None
        state.files[rel] = "sha256:" + "0" * 64
        save_state(self.root, state)

    def test_path_traversal_key_is_ignored(self) -> None:
        self._inject_key("../../../../../../../../etc/passwd")
        self.assertEqual(reverted_content_stale(self.root), [])

    def test_absolute_path_key_is_ignored(self) -> None:
        self._inject_key("/etc/passwd")
        self.assertEqual(reverted_content_stale(self.root), [])

    def test_tampered_key_does_not_blind_the_signal_to_real_divergence(
        self,
    ) -> None:
        # A malformed entry must not short-circuit or corrupt the scan of
        # every legitimate one alongside it.
        original = (self.root / "src" / "a.py").read_text(encoding="utf-8")
        (self.root / "src" / "a.py").write_text(
            "def a():\n    return 55\n", encoding="utf-8"
        )
        self.discover()
        self._inject_key("../../../../../../../../etc/shadow")
        (self.root / "src" / "a.py").write_text(original, encoding="utf-8")
        self.assertEqual(
            reverted_content_stale(self.root),
            [{"path": "src/a.py", "reason": CONTENT_DIFFERS}],
        )


if __name__ == "__main__":
    unittest.main()
