"""A Mode B file index must not restamp on every no-change discover (bd nwbn).

bd emyk closed the ``graph.json`` half of this shape: a no-change ``wd
discover`` now leaves ``graph.json`` and ``discovery-state.json``
byte-identical, because an identical body is no longer rewritten. The file
index was the residual, and a worse shape than emyk's, because it could not
converge by skipping a rewrite: ``meta.git_sha`` records the commit the file
is written *under*, and the file is then committed *into* the next commit.
So the sha this run reads (current HEAD) is never the sha the file will
carry once committed (the *parent* of the commit still being built) --
whatever you commit, the tracked file names its own parent. The next
discover restamps it to the new HEAD, you commit that, discover restamps
again -- forever, in every Mode B repository.

Measured before the fix, in a one-commit Mode B repo at a stable HEAD, three
consecutive no-change ``wd discover`` runs each rewrote both
``file-index.json`` (``meta.git_sha``) and ``file-index-state.json``
(``meta.git_sha``, and therefore ``meta.index_sha256`` too, since that field
is the sha256 of ``file-index.json``'s own bytes).

The fix (see ``weld.file_index.save_file_index`` /
``weld._file_index_incremental._save_state_hashes``) drops ``git_sha``
entirely rather than sidecar it: a repo-wide grep found no reader of the
field on either file -- only the two writers touched it -- which is the same
shape bd lrfu already resolved for ``discovery-state.json``'s ``created_at``.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from weld.tests._mode_b_fixture import ModeBFixture
from weld.tests._seed_fixture import discover, git


class ModeBFileIndexNoRestampTest(ModeBFixture):
    """``setUp`` already leaves one commit behind carrying a discovered index."""

    def _file_index_paths(self, root: Path) -> tuple[Path, Path]:
        weld = root / ".weld"
        return weld / "file-index.json", weld / "file-index-state.json"

    def test_three_no_change_discovers_leave_file_index_byte_stable(self) -> None:
        """The acceptance repro: N no-change discovers, zero drift between them."""
        idx_path, state_path = self._file_index_paths(self.origin)
        committed_index = idx_path.read_bytes()
        committed_state = state_path.read_bytes()

        for i in range(3):
            discover(self.origin)
            self.assertEqual(
                idx_path.read_bytes(), committed_index,
                f"discover #{i + 1}: file-index.json drifted from the "
                "committed copy on a run with no source changes",
            )
            self.assertEqual(
                state_path.read_bytes(), committed_state,
                f"discover #{i + 1}: file-index-state.json drifted from the "
                "committed copy on a run with no source changes",
            )

    def test_commit_discover_cycle_leaves_git_status_clean(self) -> None:
        """The restamp loop, at the layer a developer actually observes."""
        discover(self.origin)
        status = git(self.origin, "status", "--porcelain")
        self.assertEqual(
            status, "",
            f"a no-change discover left the working tree dirty:\n{status}",
        )

    def test_committed_meta_carries_no_git_sha(self) -> None:
        """The root cause, pinned directly: the tracked bytes name no commit."""
        idx_path, state_path = self._file_index_paths(self.origin)
        index_meta = json.loads(idx_path.read_text(encoding="utf-8"))["meta"]
        state_meta = json.loads(state_path.read_text(encoding="utf-8"))["meta"]
        self.assertNotIn(
            "git_sha", index_meta,
            "file-index.json meta still names a commit -- it will always "
            "disagree with the commit it is about to be committed into",
        )
        self.assertNotIn(
            "git_sha", state_meta,
            "file-index-state.json meta still names a commit -- same "
            "self-reference bug, independent write site",
        )


if __name__ == "__main__":
    unittest.main()
