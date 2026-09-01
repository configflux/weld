"""Which read surfaces seed a fresh worktree (ADR 0096 §2, bd 6osw).

:mod:`weld_worktree_seed_test` pins that ``wd query`` in a worktree created a
moment ago answers about *that* worktree. This suite pins the other half of
the same promise: which commands make it happen. ADR 0096 §2 places seeding at
"the single funnel all graph-backed read CLIs pass through", but wired it into
``ensure_graph_exists``, whose membership answers a different question -- who
gets first-run guidance. Everything outside that set silently skipped the
seed, so in a fresh worktree:

* ``wd stale`` reported ``no graph`` -- the surface CLAUDE.md tells an agent to
  run *first*, and the one that reported the bug;
* ``wd find`` reported ``no matches`` for a file plainly on disk, which is a
  false negative rather than a refusal;

while ``wd query`` one keystroke away seeded and answered correctly.

The tests drive the real CLI at a real ``git worktree add`` checkout and
assert on what came back. Two properties, deliberately paired: every
state-answering surface seeds, and none of them gained the *graph* refusal
that would let a probe exit instead of reporting.

One later correction (field eval v0.24.0, N9): ``find`` does refuse when the
seed leaves it with **no file index at all**. That is not the refusal this
suite guards against -- a probe exiting instead of reporting -- but its
mirror: answering ``no matches`` from an artifact that was never written is
the same false negative the second bullet above is about, one step further
along. ``find`` still takes the seed, still needs no graph, and still answers
whenever an index is reachable; the precondition it gained is over the index
(:mod:`weld._find_precondition`, ADR 0134). The probes -- ``stale`` /
``stats`` / ``list`` / ``dump`` -- gained nothing and must still answer.
"""

from __future__ import annotations

import io
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from weld._errors import FILE_INDEX_MISSING
from weld.tests._mode_a_fixture import ModeAFixture, weld_listing

#: The seed-only surfaces: they answer from ``.weld/`` state rather than from
#: the graph, so the seed has to reach all of them. Each is paired with a term
#: the fixture's ``alpha.py`` satisfies, so a served answer is observable.
_SEED_ONLY = (
    ("stale", ()),
    ("find", ("alpha",)),
    ("stats", ()),
    ("list", ()),
    ("dump", ()),
)

#: The subset that must keep answering even where no seed is available: each
#: reports on ``.weld/`` state, so "there is none" is itself the answer.
#: ``find`` is deliberately absent -- it reports on the *tree*, and has no
#: honest answer to give about it with no index in hand (ADR 0134); its own
#: case is asserted separately below.
_ANSWER_WITHOUT_A_SEED = tuple(
    entry for entry in _SEED_ONLY if entry[0] != "find"
)


def _run(root, *argv: str) -> tuple[int, str, str]:
    """Drive the real graph CLI; return ``(exit_code, stdout, stderr)``."""
    from weld._graph_cli import main as graph_main

    out, err = io.StringIO(), io.StringIO()
    code = 0
    with redirect_stdout(out), redirect_stderr(err):
        try:
            graph_main(["--root", str(root), *argv])
        except SystemExit as exc:
            code = exc.code or 0
    return code, out.getvalue(), err.getvalue()


class SeedOnlySurfacesSeedTest(ModeAFixture):
    """Every state-answering surface bootstraps a fresh linked worktree."""

    def test_each_surface_seeds_and_says_so(self) -> None:
        for cmd, args in _SEED_ONLY:
            with self.subTest(cmd=cmd):
                worktree = self.worktree(f"wt-{cmd}")
                graph = worktree / ".weld" / "graph.json"
                self.assertFalse(graph.is_file(), "fixture must start graphless")

                code, _, err = _run(worktree, cmd, *args)

                self.assertEqual(code, 0, f"{cmd} must answer, not refuse: {err}")
                self.assertTrue(graph.is_file(), f"{cmd} did not seed: {err}")
                self.assertIn(
                    "seeded worktree graph",
                    err,
                    f"{cmd} seeded without the ADR 0096 stderr notice",
                )

    def test_stale_reports_the_seeded_graph_not_no_graph(self) -> None:
        """The reported bug, end to end.

        ``reason: no graph`` is what the reporter saw, and it is a statement
        about a state that does not survive contact with any other command.
        After the seed the probe has a real graph to describe, so the answer
        is about *this* worktree at *this* branch.
        """
        worktree = self.worktree("wt-report")

        code, out, _ = _run(worktree, "stale", "--json")

        self.assertEqual(code, 0)
        self.assertNotIn("no graph", out)
        self.assertIn("wt-report", out, "stale must name the branch that answered")

    def test_find_stops_answering_no_matches_about_a_file_on_disk(self) -> None:
        """The false negative: worse than a refusal, because it reads as fact."""
        worktree = self.worktree("wt-find")

        code, out, _ = _run(worktree, "find", "alpha")

        self.assertEqual(code, 0)
        self.assertIn("alpha.py", out)
        self.assertNotIn("no matches", out)


class SeedOnlySurfacesStillAnswerTest(ModeAFixture):
    """Seeding must not bring the first-run refusal along with it.

    A freshness or inventory probe that exits instead of reporting "no graph"
    is useless: the absence *is* the answer it was asked for. So where no seed
    is possible, these surfaces still have to answer, and none of them may
    acquire ``ensure_graph_exists``'s refusal by joining the seeded set.
    """

    discover_primary = False  # a primary with no graph: nothing to seed from

    def test_surfaces_answer_when_no_seed_is_available(self) -> None:
        for cmd, args in _ANSWER_WITHOUT_A_SEED:
            with self.subTest(cmd=cmd):
                worktree = self.worktree(f"dry-{cmd}")

                code, _, err = _run(worktree, cmd, *args)

                self.assertEqual(
                    code, 0, f"{cmd} must still answer without a seed: {err}",
                )
                self.assertFalse((worktree / ".weld" / "graph.json").is_file())

    def test_query_still_refuses_where_the_probes_answer(self) -> None:
        """The set that keeps the refusal is unchanged -- this is a split, not a move."""
        worktree = self.worktree("dry-query")

        code, _, _ = _run(worktree, "query", "alpha")

        self.assertNotEqual(code, 0, "query must keep its first-run guidance")

    def test_find_refuses_rather_than_reporting_no_matches(self) -> None:
        """The false negative this suite was filed about, at its end: with no
        seed there is no index either, and ``no matches`` from an index that
        does not exist is a claim about a tree nothing ever searched.

        It is not ``query``'s refusal borrowed: the message names the index,
        never the graph, and a checkout that has an index answers normally
        whether or not it has ever seen a graph.
        """
        worktree = self.worktree("dry-find")

        code, out, err = _run(worktree, "find", "alpha")

        self.assertNotEqual(code, 0, f"find answered without an index: {out}")
        self.assertIn(f"error[{FILE_INDEX_MISSING}]:", err)
        self.assertNotIn("No Weld graph found.", err)


class SeedOnlySurfacesHonourTheFreezeTest(ModeAFixture):
    """Gate 1 covers the widened surface too (ADR 0051 / ADR 0096 §2).

    Seeding writes ``.weld/``, so the freeze that stops a read from writing
    has to reach every command that can now seed -- otherwise the widening
    silently re-enables the write these spellings exist to prevent.
    """

    def test_env_freeze_declines_every_seed_only_surface(self) -> None:
        """The subject is the write, not the exit code: under the freeze every
        one of these surfaces must leave ``.weld/`` exactly as it found it.

        ``find`` exits non-zero here while the probes exit 0, and that is the
        freeze working rather than an inconsistency: the seed it would have
        answered from is the write the freeze withheld, so it has no index --
        and a frozen read must still not answer a question it cannot answer
        (ADR 0134). The probes report on ``.weld/`` state, so "there is none"
        remains a true answer for them.
        """
        for cmd, args in _SEED_ONLY:
            with self.subTest(cmd=cmd):
                worktree = self.worktree(f"frozen-{cmd}")
                before = weld_listing(worktree)

                with mock.patch.dict(os.environ, {"WELD_AUTO_REFRESH": "0"}):
                    code, _, _ = _run(worktree, cmd, *args)

                self.assertEqual(code, 0 if cmd != "find" else 1)
                self.assertEqual(
                    before,
                    weld_listing(worktree),
                    f"{cmd} wrote .weld/ under the gate freeze",
                )

    def test_stale_offers_the_flag_now_that_it_can_write(self) -> None:
        """``--no-refresh`` reaches the seed, so ``wd stale`` stays a pure read."""
        worktree = self.worktree("flagged")
        before = weld_listing(worktree)

        code, _, _ = _run(worktree, "stale", "--no-refresh")

        self.assertEqual(code, 0)
        self.assertEqual(before, weld_listing(worktree))


if __name__ == "__main__":
    unittest.main()
