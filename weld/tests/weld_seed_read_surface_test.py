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
state-answering surface seeds, and none of them gained the *refusal* that
would let a probe exit instead of reporting.
"""

from __future__ import annotations

import io
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from weld.tests._mode_a_fixture import ModeAFixture, weld_listing

#: The seed-only surfaces: they answer from ``.weld/`` state but must keep
#: answering when the checkout stays graphless. Each is paired with a term
#: the fixture's ``alpha.py`` satisfies, so a served answer is observable.
_SEED_ONLY = (
    ("stale", ()),
    ("find", ("alpha",)),
    ("stats", ()),
    ("list", ()),
    ("dump", ()),
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

    A freshness probe that exits instead of reporting "no graph" is useless,
    and ``find`` answers off the file index, which a user may legitimately
    build with no graph at all. So where no seed is possible, these surfaces
    still have to answer.
    """

    discover_primary = False  # a primary with no graph: nothing to seed from

    def test_surfaces_answer_when_no_seed_is_available(self) -> None:
        for cmd, args in _SEED_ONLY:
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


class SeedOnlySurfacesHonourTheFreezeTest(ModeAFixture):
    """Gate 1 covers the widened surface too (ADR 0051 / ADR 0096 §2).

    Seeding writes ``.weld/``, so the freeze that stops a read from writing
    has to reach every command that can now seed -- otherwise the widening
    silently re-enables the write these spellings exist to prevent.
    """

    def test_env_freeze_declines_every_seed_only_surface(self) -> None:
        for cmd, args in _SEED_ONLY:
            with self.subTest(cmd=cmd):
                worktree = self.worktree(f"frozen-{cmd}")
                before = weld_listing(worktree)

                with mock.patch.dict(os.environ, {"WELD_AUTO_REFRESH": "0"}):
                    code, _, _ = _run(worktree, cmd, *args)

                self.assertEqual(code, 0)
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
