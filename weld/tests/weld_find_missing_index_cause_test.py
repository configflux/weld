"""N9 in the checkout that reproduced it: a worktree that can never seed.

The hermetic half of this fix lives in :mod:`weld_find_missing_index_test`,
which builds roots by writing files. This half builds the *repository shape*
instead, with real ``git``, because the claim is about a repository-wide
policy no worktree of it can escape: a repo that gitignores all of ``.weld/``
(``wd init --ignore-all``, and a common hand-rolled choice) hands every
linked worktree a tree with no config, so seeding can never run there and no
index ever arrives. Deleting an index from a worktree would prove something
weaker.

``wd query`` already names that cause -- the graph route computes it and
prints it under its own headline. ``find`` refuses on a different artifact
but for the *same* withheld seed: ``file-index.json`` is copied by the same
gate-5 pass as ``graph.json`` (``weld._worktree_seed_copy.SEED_STATE_FILES``),
so the prerequisite the graph route names is literally the one that kept the
index out too. Reusing that sentence rather than writing a second one is what
keeps the two surfaces from drifting into disagreeing about a repository they
both looked at.

The restraint half matters as much: in the *default* Mode A shape a worktree
receives its config, seeds, and answers. The guard must not fire there, or
"find refuses when it cannot answer" would have quietly become "find refuses
in fresh worktrees".
"""

from __future__ import annotations

import unittest
from pathlib import Path

from weld._errors import ERROR_HINTS, FILE_INDEX_MISSING
from weld._find_precondition import missing_file_index_payload
from weld._gitignore_writer import IGNORE_ALL_GITIGNORE
from weld._worktree_seed import seed_blocked_reason
from weld.tests._mode_a_fixture import (
    ROOT,
    ModeAFixture,
    run_read,
    weld_listing,
)

#: Fragments of the cause a reader has to receive: what this checkout is,
#: which file is missing, and the repository-wide change that fixes it.
_CAUSE = ("linked git worktree", ".weld/discover.yaml",
          "git add -f .weld/discover.yaml")

#: The rendered prefix an agent scrapes stderr for.
_ERROR_PREFIX = f"error[{FILE_INDEX_MISSING}]:"

#: Retry string held equal across payloads so a comparison isolates the
#: field under test.
_RETRY = "wd find \"alpha\""

_INDEX_REL = Path(".weld") / "file-index.json"

#: The invocation under test, with the checkout bound per call.
_FIND = ("--root", ROOT, "find", "alpha")


class IgnoreAllWorktreeCauseTest(ModeAFixture):
    """A worktree that can never hold an index is told why, not just that."""

    gitignore = IGNORE_ALL_GITIGNORE

    def test_precondition_the_policy_withholds_both_artifacts(self) -> None:
        """The ignore policy, not the test, is what leaves this tree bare --
        and the origin is healthy, so nothing else is wrong."""
        worktree = self.worktree()

        self.assertFalse((worktree / ".weld" / "discover.yaml").exists())
        self.assertFalse((worktree / _INDEX_REL).exists())
        self.assertTrue((self.origin / _INDEX_REL).is_file())

    def test_find_refuses_and_names_the_prerequisite(self) -> None:
        worktree = self.worktree()

        code, stderr = run_read(worktree, _FIND)

        self.assertNotEqual(code, 0)
        self.assertIn(_ERROR_PREFIX, stderr)
        self.assertIn(ERROR_HINTS[FILE_INDEX_MISSING], stderr)
        for fragment in _CAUSE:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, stderr)

    def test_the_cause_is_the_one_the_graph_route_computes(self) -> None:
        """Reused, not restated: a second copy of this sentence is how the
        two routes would come to describe the same repository differently."""
        worktree = self.worktree()

        payload = missing_file_index_payload(worktree, _RETRY)

        self.assertEqual(
            payload["error"],
            f"No Weld file index found.\n{seed_blocked_reason(worktree)}",
        )

    def test_the_machine_readable_vocabulary_is_untouched(self) -> None:
        """A cause is prose. Nothing an agent branches on moves with it."""
        worktree = self.worktree()

        payload = missing_file_index_payload(worktree, _RETRY)
        bare = missing_file_index_payload(self.tmp / "not-a-checkout", _RETRY)

        self.assertEqual(payload["error_code"], FILE_INDEX_MISSING)
        self.assertEqual(payload["hint"], bare["hint"])
        self.assertEqual(payload["retry"], bare["retry"])
        self.assertEqual(set(payload), set(bare))
        self.assertTrue(payload["error"].startswith("No Weld file index found."))

    def test_refusing_writes_nothing(self) -> None:
        """This runs on the failure path of a read, and a read that acquired
        a write to explain itself would be worse than the message it fixes."""
        worktree = self.worktree()
        before = weld_listing(worktree)

        run_read(worktree, _FIND)

        self.assertEqual(weld_listing(worktree), before)


class SeededWorktreeStillAnswersTest(ModeAFixture):
    """Default Mode A: the config is tracked, so gate 5 fires and find works.

    The guard sits *after* seeding for exactly this reason. A worktree that
    can be repaired is repaired first and never sees the refusal.
    """

    def test_a_fresh_worktree_answers_off_the_seeded_index(self) -> None:
        worktree = self.worktree()
        self.assertFalse(
            (worktree / _INDEX_REL).exists(), "the seed has not run yet",
        )

        code, stderr = run_read(worktree, _FIND)

        self.assertEqual(code, 0, stderr)
        self.assertNotIn(_ERROR_PREFIX, stderr)
        self.assertTrue((worktree / _INDEX_REL).is_file())

    def test_such_a_worktree_has_no_cause_to_report(self) -> None:
        self.assertIsNone(seed_blocked_reason(self.worktree()))


if __name__ == "__main__":
    unittest.main()
