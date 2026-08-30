"""Finding 09: the no-graph message names the prerequisite it is missing.

Transcript-derived, from
``docs/field-reports/weld-0.23.1-findings/transcripts/09-worktree-seeding-\
requires-tracked-config.txt``. The evaluator ran the same read in two
worktrees of one repository at one commit, and the only difference between
them was whether git carried ``.weld/discover.yaml`` into the checkout:

* **case A** -- the repository ignores all of ``.weld/``, so the worktree
  has no config, gate 5 declines, and the reader is shown the ordinary
  first-run guidance with nothing in it about the one thing that would have
  to change. That is the finding.
* **case B** -- config tracked, so the identical read seeds from the sibling
  checkout and answers in about two seconds. That case was reported as
  working, and the point of the pair is that nothing here breaks it.

Both cases are built for real: a repository whose ignore policy is exactly
what ``wd init --ignore-all`` writes, a plain ``git worktree add``, and the
real read CLI. Case A cannot be faked by deleting the config from a
worktree (:mod:`weld_worktree_seed_test` covers that shape) -- the finding
is about a *repository-wide* policy no worktree of it can escape, and only
building the repository that way proves the message reaches the person who
made that choice.

The declined-gate matrix itself lives in :mod:`weld_worktree_seed_test`;
what is asserted here is only what the user is told when a gate declines.
"""

from __future__ import annotations

import os
import unittest
from dataclasses import dataclass
from unittest import mock

from weld._doctor_gitignore import check_seeding_config
from weld._gitignore_writer import IGNORE_ALL_GITIGNORE
from weld._worktree_seed import seed_blocked_reason
from weld.tests._mode_a_fixture import (
    ROOT,
    ModeAFixture,
    run_read,
    weld_listing,
)

#: The read the transcript ran, in both cases.
_QUERY = ("--root", ROOT, "query", "alpha")

#: Guidance the block has always carried. A cause is additive, so every
#: one of these has to survive it -- onboarding docs match on them.
_STANDING = ("No Weld graph found.", "wd init", "wd discover", "Then retry:")


@dataclass
class FakeResult:
    """Stand-in for :class:`weld.doctor.CheckResult`."""

    level: str
    message: str
    section: str
    note_id: str | None = None


class IgnoreAllRepoMessageTest(ModeAFixture):
    """Case A: a repository whose worktrees can never seed says so."""

    gitignore = IGNORE_ALL_GITIGNORE

    def test_precondition_the_worktree_arrives_without_the_config(self) -> None:
        """The repository, not the test, is what withholds ``discover.yaml``."""
        worktree = self.worktree()

        self.assertFalse((worktree / ".weld" / "discover.yaml").exists())
        # The seed source exists and is healthy: nothing else is wrong here.
        self.assertTrue((self.origin / ".weld" / "graph.json").is_file())
        self.assertTrue((self.origin / ".weld" / "discover.yaml").is_file())

    def test_message_names_the_missing_prerequisite_and_the_fix(self) -> None:
        with mock.patch.dict(os.environ, {"WELD_TELEMETRY": "off"}):
            code, err = run_read(self.worktree(), _QUERY)

        self.assertEqual(code, 1)
        self.assertIn("linked git worktree", err)
        self.assertIn(".weld/discover.yaml", err)
        self.assertIn("git add -f .weld/discover.yaml", err)

    def test_the_standing_guidance_survives_the_added_cause(self) -> None:
        """A cause is additive: the block a reader already knows is intact."""
        with mock.patch.dict(os.environ, {"WELD_TELEMETRY": "off"}):
            _code, err = run_read(self.worktree(), _QUERY)

        for fragment in _STANDING:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, err)
        # The headline still leads, so a reader (or a log grep) finds it first.
        self.assertTrue(err.lstrip().startswith("No Weld graph found."))

    def test_explaining_the_decline_still_writes_nothing(self) -> None:
        """The cause is a probe, not a repair: ``.weld/`` is untouched."""
        worktree = self.worktree()
        before = weld_listing(worktree)

        with mock.patch.dict(os.environ, {"WELD_TELEMETRY": "off"}):
            run_read(worktree, _QUERY)

        self.assertEqual(weld_listing(worktree), before)

    def test_doctor_reports_the_same_cause_before_a_worktree_exists(self) -> None:
        """The other half: the policy is visible from the main checkout.

        The read-time message can only reach someone already standing in a
        crippled worktree. This is what a maintainer sees first.
        """
        results = check_seeding_config(self.origin / ".weld", FakeResult)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].level, "note")
        self.assertEqual(results[0].note_id, "worktree-seeding-config-ignored")
        self.assertIn(".weld/discover.yaml", results[0].message)


class TrackedConfigRepoTest(ModeAFixture):
    """Case B: the reported-working path, unchanged."""

    def test_the_same_read_still_seeds_and_answers(self) -> None:
        with mock.patch.dict(os.environ, {"WELD_TELEMETRY": "off"}):
            code, err = run_read(self.worktree(), _QUERY)

        self.assertEqual(code, 0)
        self.assertIn("seeded worktree graph from", err)

    def test_doctor_is_silent_when_the_config_is_tracked(self) -> None:
        self.assertEqual(check_seeding_config(self.origin / ".weld", FakeResult), [])


class NoCauseForUnrelatedDeclinesTest(ModeAFixture):
    """Only the one decline is explained; the rest keep the old message."""

    def test_a_plain_clone_is_not_told_about_worktrees(self) -> None:
        """No sibling to seed from, so nothing was withheld from it."""
        clone = self.clone()
        (clone / ".weld" / "discover.yaml").unlink()

        self.assertIsNone(seed_blocked_reason(clone))

    def test_the_main_checkout_is_not_told_about_worktrees(self) -> None:
        (self.origin / ".weld" / "discover.yaml").unlink()

        self.assertIsNone(seed_blocked_reason(self.origin))

    def test_a_worktree_that_has_its_config_gets_no_cause(self) -> None:
        """Whatever stopped that seed, it was not the prerequisite."""
        self.assertIsNone(seed_blocked_reason(self.worktree()))

    def test_a_federated_worktree_gets_no_cause(self) -> None:
        """Gate 3 declined first, so gate 5's prerequisite never applied."""
        worktree = self.worktree()
        (worktree / ".weld" / "discover.yaml").unlink()
        (worktree / ".weld" / "workspaces.yaml").write_text(
            "children: []\n", encoding="utf-8",
        )

        self.assertIsNone(seed_blocked_reason(worktree))


if __name__ == "__main__":
    unittest.main()
