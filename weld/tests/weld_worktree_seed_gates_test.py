"""The gates and copy rules of ``ensure_seeded`` (ADR 0096 §2).

Seeding sits in front of every graph-backed read, so what it *declines*
to do matters as much as what it does. Each gate is a distinct
precondition -- environment freeze, warm root, federation, Mode A, a
basis already on record, no graph at all -- and each is asserted on its
own here; a CLI round-trip would only obscure which one fired.

The second half covers the state copy: which files may be borrowed from
a sibling checkout, and the proof required before borrowing them.

End-to-end Mode B behaviour lives in
:mod:`weld_mode_b_sidecar_synthesis_test`; the shared checkout fixture in
:mod:`_mode_b_fixture`.
"""

from __future__ import annotations

import io
import json
import os
import unittest
from contextlib import redirect_stderr
from unittest import mock

from weld._graph_cli import ensure_graph_exists
from weld._worktree_seed import SEED_STATE_FILES, ensure_seeded
from weld._worktree_seed_copy import borrow_state_from_identical_sibling
from weld.tests._mode_b_fixture import (
    LEGACY_TRACK_GRAPHS_GITIGNORE,
    SIDECAR,
    ModeBFixture,
    git,
    weld_listing,
)


class _LegacyModeB(ModeBFixture):
    """Mode B as an earlier weld wrote it: graph tracked, records not.

    Every gate below answers the question *what may a checkout that
    arrived without state borrow, and on what proof* -- so it has to
    stand in a checkout that arrives without state. Today's policy
    (ADR 0110) ships those records in git, which is the better answer
    and not the one under test here.
    """

    GITIGNORE = LEGACY_TRACK_GRAPHS_GITIGNORE


class SeedGateTests(_LegacyModeB):
    """Each gate is a distinct precondition; assert them one at a time."""

    def test_env_freeze_writes_nothing(self) -> None:
        """Seeding writes ``.weld/`` -- the gate freeze must cover it."""
        clone = self.clone()
        before = weld_listing(clone)

        for value in ("0", "off", "false", "no", "disabled"):
            with mock.patch.dict(os.environ, {"WELD_AUTO_REFRESH": value}):
                self.assertIsNone(ensure_seeded(clone), f"WELD_AUTO_REFRESH={value}")

        self.assertEqual(weld_listing(clone), before)

    def test_no_refresh_writes_nothing(self) -> None:
        """The freeze answers to both of its documented spellings.

        ADR 0051 names ``--no-refresh`` and ``WELD_AUTO_REFRESH=0`` as
        equal answers to "this caller must not write"; gate 1 honouring
        only the env var would make them unequal. Synthesis is a small
        write next to a Mode A seed, but it is still a write, and a clone
        that declines it simply reports source-stale -- an answer, which
        is all ``--no-refresh`` ever promised.
        """
        clone = self.clone()
        before = weld_listing(clone)

        self.assertIsNone(ensure_seeded(clone, no_refresh=True))

        self.assertEqual(weld_listing(clone), before)

    def test_steady_state_is_a_noop(self) -> None:
        clone = self.clone()
        self.assertIsNotNone(ensure_seeded(clone))
        stamped = (clone / ".weld" / SIDECAR).read_bytes()

        self.assertIsNone(ensure_seeded(clone), "a warm root must short-circuit")
        self.assertEqual((clone / ".weld" / SIDECAR).read_bytes(), stamped)

    def test_federated_root_is_out_of_scope(self) -> None:
        clone = self.clone()
        (clone / ".weld" / "workspaces.yaml").write_text(
            "children: []\n", encoding="utf-8",
        )

        self.assertIsNone(ensure_seeded(clone))
        self.assertFalse((clone / ".weld" / SIDECAR).exists())

    def test_recorded_basis_is_never_overwritten(self) -> None:
        """An exact basis outranks the approximation, wherever it is recorded.

        A pre-ADR-0065 graph keeps ``git_sha`` inside ``graph.json`` and
        has no sidecar, so "sidecar missing" alone would fire here and
        replace the true build commit with the commit that happened to
        add the graph to git. When the graph was committed *after* the
        content it describes -- the ordinary case -- that reads a stale
        graph as fresh, the one outcome this must never produce.

        The coverage inventory beside it is a *different* claim (bd r7d7)
        and still lands: knowing which commit built a graph says nothing
        about which files it read, and a legacy graph is as blind to a new
        in-scope file as any other.
        """
        clone = self.clone()
        graph_path = clone / ".weld" / "graph.json"
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
        graph.setdefault("meta", {})["git_sha"] = "0" * 40
        graph_path.write_text(json.dumps(graph), encoding="utf-8")

        result = ensure_seeded(clone)

        self.assertIsNone(
            result["git_sha"],
            "a graph that already knows its own basis must be left alone",
        )
        self.assertFalse((clone / ".weld" / SIDECAR).exists())
        self.assertEqual(result["coverage_inventory"], 1)

    def test_untracked_graph_is_left_alone(self) -> None:
        """Mode A: an untracked graph has no commit to derive a basis from."""
        clone = self.clone()
        git(clone, "rm", "-q", "--cached", ".weld/graph.json")
        git(clone, "commit", "-q", "-m", "switch to Mode A")

        self.assertIsNone(ensure_seeded(clone))
        self.assertFalse((clone / ".weld" / SIDECAR).exists())

    def test_missing_graph_preserves_first_run_guidance(self) -> None:
        """No repository, no graph: gate 5 has nothing to seed from either.

        Gate 5 seeds a missing graph only inside a *linked worktree* of a
        repository that has one somewhere. A bare directory is neither,
        so the first-run guidance is still the whole answer here.
        """
        bare = self.tmp / "empty"
        (bare / ".weld").mkdir(parents=True)

        self.assertIsNone(ensure_seeded(bare))

        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit) as caught:
            ensure_graph_exists(bare, 'wd query "x"')
        self.assertEqual(caught.exception.code, 1)
        self.assertIn("No Weld graph found.", err.getvalue())

    def test_non_git_root_is_left_alone(self) -> None:
        """No repository means no tracked-graph question to answer."""
        plain = self.tmp / "plain"
        (plain / ".weld").mkdir(parents=True)
        (plain / ".weld" / "graph.json").write_text('{"nodes": []}\n', encoding="utf-8")

        self.assertIsNone(ensure_seeded(plain))
        self.assertFalse((plain / ".weld" / SIDECAR).exists())


class StateCopyTests(_LegacyModeB):
    """What a fresh checkout may borrow from a sibling, and on what proof."""

    def test_worktree_seeds_state_from_the_primary_checkout(self) -> None:
        worktree = self.worktree()
        primary_state = self.origin / ".weld" / "discovery-state.json"
        self.assertTrue(primary_state.is_file(), "precondition: primary has state")

        result = ensure_seeded(worktree)

        self.assertIsNotNone(result)
        self.assertIn("discovery-state.json", result["seeded_state"])
        self.assertEqual(
            (worktree / ".weld" / "discovery-state.json").read_bytes(),
            primary_state.read_bytes(),
        )

    def test_plain_clone_gets_no_state_files(self) -> None:
        """No sibling checkout exists, so a plain clone accepts a full first pass."""
        clone = self.clone()

        result = ensure_seeded(clone)

        self.assertIsNotNone(result)
        self.assertEqual(result["seeded_state"], [])

    def test_state_copy_requires_a_byte_identical_source_graph(self) -> None:
        """A sibling's state describes a sibling's graph -- prove it is ours.

        ``discovery-state.json`` carries no binding to the graph it was
        written beside (unlike ``file-index-state.json``, which
        self-rejects a foreign index via ``meta.index_sha256``). Pairing
        it with a *different* graph would let a content-hash match mark a
        file unchanged whose nodes in our graph came from another
        revision -- a silently wrong incremental merge. Byte-identity of
        the content-addressable graph (ADR 0065) is the proof, and
        without it we take the full pass instead.
        """
        worktree = self.worktree()
        graph = self.origin / ".weld" / "graph.json"
        graph.write_text(graph.read_text(encoding="utf-8") + "\n", encoding="utf-8")

        result = ensure_seeded(worktree)

        self.assertIsNotNone(result)
        self.assertEqual(result["seeded_state"], [])
        self.assertTrue(
            (worktree / ".weld" / SIDECAR).is_file(),
            "the sidecar is derived from our own git history and must still land",
        )

    def test_existing_state_is_never_overwritten(self) -> None:
        worktree = self.worktree()
        local = worktree / ".weld" / "discovery-state.json"
        local.write_text('{"version": 1, "files": {}}\n', encoding="utf-8")

        result = ensure_seeded(worktree)

        self.assertNotIn("discovery-state.json", result["seeded_state"])
        self.assertEqual(
            local.read_text(encoding="utf-8"), '{"version": 1, "files": {}}\n',
        )

    def test_a_fully_stated_root_is_not_digested_to_learn_that(self) -> None:
        """Nothing left to borrow means no proof is worth buying.

        The proof is a full ``sha256`` of ``graph.json``. Gate 4 re-enters on
        every read of a root whose sidecar decision stays open -- a
        pre-ADR-0065 graph records its basis inside ``graph.json`` and never
        gets one -- so establishing "there was nothing to copy" the expensive
        way would put a multi-megabyte digest on the read path, which is
        exactly what ADR 0101 section 4 and bd aqqa keep out of it.
        """
        worktree = self.worktree()
        for name in SEED_STATE_FILES:
            (worktree / ".weld" / name).write_text("{}\n", encoding="utf-8")

        with mock.patch(
            "weld._worktree_seed_copy._identical_sibling",
            side_effect=AssertionError("digested a root with nothing to borrow"),
        ) as probe:
            self.assertEqual(
                borrow_state_from_identical_sibling(
                    worktree, worktree / ".weld" / "graph.json",
                ),
                [],
            )

        self.assertEqual(probe.call_count, 0)

    def test_seed_set_is_a_fixed_state_allowlist(self) -> None:
        """Only derived state is ever copied -- never a graph, never source."""
        self.assertEqual(
            SEED_STATE_FILES,
            ("discovery-state.json", "file-index.json", "file-index-state.json"),
        )


if __name__ == "__main__":
    unittest.main()
