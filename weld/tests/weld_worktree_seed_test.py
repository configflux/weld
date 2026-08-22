"""Mode A copy-seed, end to end (ADR 0096 §2 gate 5).

The user-visible claim is one sentence: the *first* ``wd query`` in a
worktree created a moment ago answers about **that worktree's** code.
The tests below drive the real read CLI at a real ``git worktree add``
checkout and assert on what came back, not on how it got there.

The second half is the other half of the claim -- everything gate 5 must
decline. A seed writes ``.weld/`` during a read, so each precondition
that does not hold has to leave the checkout exactly as it was, first-run
guidance included.

Source resolution across layouts lives in
:mod:`weld_seed_source_resolution_test`, the mid-copy and concurrency
rules in :mod:`weld_worktree_seed_race_test`.
"""

from __future__ import annotations

import io
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from weld._graph_cli import ensure_graph_exists
from weld._worktree_seed import ensure_seeded
from weld.tests._mode_a_fixture import (
    ALPHA_NODE,
    BETA_NODE,
    SIDECAR,
    ModeAFixture,
    add_branch_file,
    git,
    graph_nodes,
    read,
    sidecar,
    stale_info,
    weld_listing,
)

#: Placeholder for the checkout under test inside the argv tables below.
_ROOT = "<root>"

#: The invocation the single-surface tests drive, and the table's first
#: row. Spelled out rather than indexed out of the table so reordering
#: the table cannot quietly change what those tests exercise.
_QUERY = ("--root", _ROOT, "query", "alpha")

#: Read entry points that expose ``--no-refresh`` *and* funnel through
#: ``ensure_graph_exists``. Each builds its own parser and makes its own
#: choke-point call, so a hand-off missing from any one of them silently
#: re-enables the write that command just promised not to make. ``diff``
#: and ``enrich`` share the choke point but expose no such flag, so they
#: have nothing to hand off.
#:
#: Where ``--root`` sits is part of each surface, not noise: the
#: ``_graph_cli`` family takes it as a global option *before* the
#: subcommand, while ``brief`` / ``trace`` / ``impact`` each parse it
#: themselves, after. Entries are ``(label, argv-after-wd)``.
_NO_REFRESH_READS = (
    ("query", _QUERY),
    ("brief", ("brief", "alpha", "--root", _ROOT)),
    ("trace", ("trace", "--node", ALPHA_NODE, "--root", _ROOT)),
    ("impact", ("impact", ALPHA_NODE, "--root", _ROOT)),
)


def run_read(root: Path, argv: tuple[str, ...], *extra: str) -> tuple[int, str]:
    """Drive the real read CLI at *root*; return ``(exit_code, stderr)``.

    The shared ``read`` helper asserts success, but a *declined* seed ends
    in first-run guidance and a nonzero exit -- which is the observable
    under test here, so it needs the code rather than an assertion.
    """
    from weld.cli import main as cli_main

    resolved = [str(root) if tok == _ROOT else tok for tok in (*argv, *extra)]
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = cli_main(resolved) or 0
        except SystemExit as exc:
            code = exc.code or 0
    return code, err.getvalue()


class FreshWorktreeReadTests(ModeAFixture):
    """The first read in a fresh worktree answers about that worktree."""

    def test_precondition_worktree_starts_without_a_graph(self) -> None:
        worktree = self.worktree()

        self.assertFalse((worktree / ".weld" / "graph.json").exists())
        self.assertTrue((worktree / ".weld" / "discover.yaml").is_file())
        self.assertTrue((self.origin / ".weld" / "graph.json").is_file())

    def test_first_read_answers_with_branch_content(self) -> None:
        """The payoff: branch-only content is in the graph on read one."""
        worktree = self.worktree()
        add_branch_file(worktree)

        read(worktree, "beta")

        nodes = graph_nodes(worktree)
        self.assertIn(BETA_NODE, nodes, "reconcile must pick up branch content")
        self.assertIn(ALPHA_NODE, nodes, "the seeded content must survive it")
        self.assertNotIn(
            BETA_NODE,
            graph_nodes(self.origin),
            "the source checkout must not be written to",
        )

    def test_seeded_graph_is_stamped_with_our_own_head_and_branch(self) -> None:
        """Identity comes from the reconcile, not from the source checkout."""
        worktree = self.worktree()
        add_branch_file(worktree)

        read(worktree)

        meta = sidecar(worktree)
        self.assertEqual(meta["git_sha"], git(worktree, "rev-parse", "HEAD"))
        self.assertEqual(meta["git_branch"], "feature")
        self.assertNotEqual(meta["git_sha"], git(self.origin, "rev-parse", "HEAD"))

    def test_seed_reports_itself_on_stderr(self) -> None:
        worktree = self.worktree()

        err = read(worktree)

        self.assertIn("seeded worktree graph from", err)
        self.assertIn(str(self.origin), err)
        self.assertIn("reconciled to feature@", err)

    def test_steady_state_after_seeding_is_clean_and_quiet(self) -> None:
        """One seed, then nothing: no re-seed, no staleness, no notice."""
        worktree = self.worktree()
        add_branch_file(worktree)
        read(worktree)

        self.assertFalse(stale_info(worktree)["stale"])
        self.assertIsNone(ensure_seeded(worktree), "a warm root must short-circuit")
        self.assertNotIn("seeded worktree graph", read(worktree))

    def test_seed_borrows_the_source_content_hash_state(self) -> None:
        """Borrowed state is what keeps the reconcile incremental."""
        worktree = self.worktree()

        result = ensure_seeded(worktree)

        self.assertEqual(result["action"], "worktree_copy_seed")
        self.assertEqual(result["source"], str(self.origin))
        self.assertTrue(result["reconciled"])
        self.assertIn("discovery-state.json", result["seeded_state"])

    def test_sqlite_sidecar_is_not_seeded(self) -> None:
        """graph.db is tens of megabytes and rebuilds lazily (ADR 0058)."""
        self.assertTrue((self.origin / ".weld" / "graph.db").is_file())
        worktree = self.worktree()

        ensure_seeded(worktree)

        self.assertFalse((worktree / ".weld" / "graph.db").exists())


class SeedDeclinedTests(ModeAFixture):
    """Every precondition that does not hold must leave the checkout alone."""

    def _assert_guidance(self, root) -> None:
        """The read still ends in first-run guidance, and nothing was written."""
        before = weld_listing(root)
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit) as caught:
            ensure_graph_exists(root, 'wd query "x"')
        self.assertEqual(caught.exception.code, 1)
        self.assertIn("No Weld graph found.", err.getvalue())
        self.assertEqual(weld_listing(root), before)

    def test_env_freeze_writes_nothing(self) -> None:
        """Seeding writes ``.weld/`` -- the gate freeze must cover it."""
        worktree = self.worktree()
        before = weld_listing(worktree)

        for value in ("0", "off", "false", "no", "disabled"):
            with mock.patch.dict(os.environ, {"WELD_AUTO_REFRESH": value}):
                self.assertIsNone(ensure_seeded(worktree), f"value={value}")

        self.assertEqual(weld_listing(worktree), before)

    def test_frozen_read_keeps_guidance_and_touches_nothing(self) -> None:
        """The whole read path under the freeze, not just ``ensure_seeded``.

        Telemetry is disabled alongside it: it is a separate contract with
        its own opt-out (ADR 0035), and leaving it on would put
        ``telemetry.jsonl`` in the listing and hide what this asserts.
        """
        worktree = self.worktree()
        frozen = {"WELD_AUTO_REFRESH": "0", "WELD_TELEMETRY": "off"}

        with mock.patch.dict(os.environ, frozen):
            self._assert_guidance(worktree)

    def test_no_refresh_writes_nothing(self) -> None:
        """``--no-refresh`` is a no-graph-write promise, not only a stale one.

        ADR 0051 offers ``--no-refresh`` and ``WELD_AUTO_REFRESH=0`` as
        equal answers to the same requirement -- "this caller must not
        write" -- so a freeze gate honouring only the env var makes two
        documented spellings behave differently. Gate 5 is the sharpest
        case: it lands a graph *and* ends in an unconditional reconcile,
        a discovery pass under the one flag whose name refuses one.
        """
        worktree = self.worktree()
        before = weld_listing(worktree)

        self.assertIsNone(ensure_seeded(worktree, no_refresh=True))

        self.assertEqual(weld_listing(worktree), before)

    def test_no_refresh_read_keeps_guidance_and_touches_nothing(self) -> None:
        """The whole read path under the flag, not just ``ensure_seeded``."""
        worktree = self.worktree()
        before = weld_listing(worktree)

        with mock.patch.dict(os.environ, {"WELD_TELEMETRY": "off"}):
            code, err = run_read(worktree, _QUERY, "--no-refresh")

        self.assertEqual(code, 1)
        self.assertIn("No Weld graph found.", err)
        self.assertEqual(weld_listing(worktree), before)

    def test_the_same_read_without_the_flag_still_seeds(self) -> None:
        """The control: the decline is the flag's doing, not a dead gate 5."""
        worktree = self.worktree()

        with mock.patch.dict(os.environ, {"WELD_TELEMETRY": "off"}):
            code, err = run_read(worktree, _QUERY)

        self.assertEqual(code, 0)
        self.assertIn("seeded worktree graph from", err)

    def test_every_no_refresh_read_declines(self) -> None:
        """Four parsers, four choke-point calls -- each one hands the flag on."""
        for label, argv in _NO_REFRESH_READS:
            with self.subTest(command=label):
                worktree = self.worktree(label)
                before = weld_listing(worktree)

                with mock.patch.dict(os.environ, {"WELD_TELEMETRY": "off"}):
                    code, err = run_read(worktree, argv, "--no-refresh")

                self.assertEqual(code, 1)
                self.assertIn("No Weld graph found.", err)
                self.assertEqual(weld_listing(worktree), before)

    def test_non_worktree_root_is_left_alone(self) -> None:
        """A plain clone has no sibling checkout; ``wd warm`` answers there."""
        clone = self.clone()

        self.assertIsNone(ensure_seeded(clone))
        self._assert_guidance(clone)

    def test_main_checkout_is_left_alone(self) -> None:
        """A graphless primary means "run wd discover", not "borrow one"."""
        graph = self.origin / ".weld" / "graph.json"
        graph.unlink()
        (self.origin / ".weld" / SIDECAR).unlink()
        self.worktree()  # a sibling exists and still must not be used

        self.assertIsNone(ensure_seeded(self.origin))
        self.assertFalse(graph.exists())

    def test_federated_root_is_out_of_scope(self) -> None:
        worktree = self.worktree()
        (worktree / ".weld" / "workspaces.yaml").write_text(
            "children: []\n", encoding="utf-8",
        )

        self.assertIsNone(ensure_seeded(worktree))
        self.assertFalse((worktree / ".weld" / "graph.json").exists())

    def test_missing_discover_config_is_left_alone(self) -> None:
        """Without config the seed could never be re-derived, so it is refused."""
        worktree = self.worktree()
        (worktree / ".weld" / "discover.yaml").unlink()

        self.assertIsNone(ensure_seeded(worktree))
        self._assert_guidance(worktree)

    def test_config_is_never_copied_from_the_source(self) -> None:
        """Strategy selection stays inside the tree that owns it."""
        worktree = self.worktree()
        (worktree / ".weld" / "discover.yaml").unlink()

        ensure_seeded(worktree)

        self.assertFalse((worktree / ".weld" / "discover.yaml").exists())


class NoSeedSourceTests(ModeAFixture):
    """A worktree whose repository has no graph anywhere keeps guidance."""

    discover_primary = False

    def test_worktree_without_a_source_falls_back_to_guidance(self) -> None:
        worktree = self.worktree()

        self.assertIsNone(ensure_seeded(worktree))

        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            with self.assertRaises(SystemExit) as caught:
                ensure_graph_exists(worktree, 'wd query "x"')
        self.assertEqual(caught.exception.code, 1)
        self.assertIn("No Weld graph found.", err.getvalue())


if __name__ == "__main__":
    unittest.main()
