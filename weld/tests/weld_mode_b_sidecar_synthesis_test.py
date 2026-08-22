"""Mode B bootstrap: a tracked graph must arrive usable (ADR 0096 §2).

``wd init --track-graphs`` commits ``.weld/graph.json`` so every clone
shares a pre-built graph -- but ``graph-meta.json``, which carries
``git_sha``, stays gitignored. A fresh clone therefore held a perfectly
good graph and could not say anything about it: ``git_sha`` absent means
``compute_stale_info`` reports ``source_stale=True``, and with no
``discovery-state.json`` the resulting refresh is a *full* rediscover.
Mode B paid the cold cost it exists to avoid.

These tests pin the behaviour from the outside: real ``git clone`` /
``git worktree add`` of a real ``--track-graphs`` repo, driven through
``weld.cli.main``. The one mock is a counter around
``_discover_single_repo``, because "ran zero discovery" is the entire
payoff and no observable artifact proves that negative as directly.

The gate matrix lives in :mod:`weld_worktree_seed_gates_test`; the shared
checkout fixture in :mod:`_mode_b_fixture`.
"""

from __future__ import annotations

from unittest import mock

from weld._discover_state_check import state_vouches_for_graph
from weld._graph_cli import ensure_graph_exists
from weld._worktree_seed import ensure_seeded
from weld.discovery_state import load_state
from weld.tests._mode_b_fixture import (
    SIDECAR,
    ModeBFixture,
    git,
    graph_commit,
    graph_nodes,
    read,
    sidecar,
    stale_info,
    weld_listing,
    wrapped_discover,
)


class FreshCloneSynthesisTests(ModeBFixture):
    """The tracked graph must bootstrap its own staleness basis."""

    def test_precondition_clone_has_graph_but_no_sidecar(self) -> None:
        clone = self.clone()

        self.assertTrue((clone / ".weld" / "graph.json").is_file())
        self.assertFalse(
            (clone / ".weld" / SIDECAR).exists(),
            "precondition: the sidecar is gitignored, so a clone lacks it",
        )
        self.assertTrue(
            stale_info(clone)["source_stale"],
            "precondition: without the sidecar the clone reports source-stale",
        )

    def test_read_synthesizes_sidecar_at_the_tracked_graph_commit(self) -> None:
        clone = self.clone()

        read(clone)

        self.assertEqual(
            sidecar(clone).get("git_sha"),
            graph_commit(clone),
            "the synthesized basis must be the last commit touching the graph",
        )

    def test_no_drift_means_fresh_and_zero_discovery(self) -> None:
        """The Mode B payoff: the first read serves the committed graph as-is.

        Neither the presence of the inventory nor its absence proves a
        discovery ran, so the mock counter is what asserts the negative.
        Since ADR 0110 the inventory arrives *in git* -- it is the record
        the origin's own discovery wrote, shipped with the graph it
        describes -- which is why it names a config here: a synthesized
        one could not, and would have cost this clone the full pass it
        exists to avoid.
        """
        clone = self.clone()

        with mock.patch(
            "weld.discover._discover_single_repo",
            side_effect=AssertionError("discovery ran on an undrifted clone"),
        ) as discover:
            read(clone)

        self.assertEqual(discover.call_count, 0)
        self.assertFalse(
            stale_info(clone)["stale"],
            "an undrifted Mode B clone must report fresh after seeding",
        )
        self.assertIn("discovery-state.json", weld_listing(clone))
        self.assertIsNotNone(
            load_state(clone).config_fingerprint,
            "the shipped inventory must be the origin's discovery product",
        )

    def test_source_drift_still_refreshes(self) -> None:
        """Synthesis must not suppress a refresh the sources actually need,
        and that refresh must settle.

        The synthesized basis is a conservative *lower* bound, so drift
        past it has to stay visible -- and once ``wd query`` has actually
        re-read the drifted file, ``source_stale`` must clear back to
        False. This settled-staleness half went unasserted for a while (bd
        aewf) on the chance some second blocker held it open even after the
        original one -- the ``.weld/auto-refresh.jsonl`` gap bd keik closed,
        which bd eqc4 then swept the rest of the ``.weld/`` output family
        into -- was fixed. Verified live: it settles, no second blocker.
        The refresh's own discover stamps ``meta.git_sha`` to this
        checkout's HEAD (asserted below), which collapses ``sha_behind``
        and every signal chained after it; a red-first check that pins the
        sidecar back to its pre-refresh SHA confirms ``source_stale``
        reasserts itself, so the assertion below is live, not vacuous.

        The drift is an *edit to a file the graph read*, which is what ADR
        0017's primary signal is defined over. It used to be a newly added
        ``beta.py``, which this fixture's root-anchored ``*.py`` glob caught
        only because provenance was then the whole-tree marker ``"./"`` --
        the defect bd od2a removed. Detecting a *new* in-scope file is ADR
        0101's coverage probe, and that probe is inert in a Mode B clone,
        which ships no ``discovery-state.json`` for it to compare against;
        that gap is tracked separately (bd r7d7) and is not what this test
        is about.
        """
        clone = self.clone()
        (clone / "alpha.py").write_text(
            "def alpha():\n    return 99\n", encoding="utf-8",
        )
        git(clone, "add", "alpha.py")
        git(clone, "commit", "-q", "-m", "edit alpha after the graph commit")

        self.assertTrue(stale_info(clone)["source_stale"], "precondition: drifted")

        with mock.patch(
            "weld.discover._discover_single_repo", wraps=wrapped_discover(),
        ) as discover:
            read(clone, "alpha")

        self.assertTrue(discover.called, "drift past the synthesized basis must refresh")
        self.assertEqual(
            sidecar(clone).get("git_sha"),
            git(clone, "rev-parse", "HEAD"),
            "the refresh must advance the basis to this checkout's own HEAD",
        )
        self.assertFalse(
            stale_info(clone)["source_stale"],
            "the refresh must settle: source_stale must clear, not just have fired",
        )


    def test_new_in_scope_file_is_seen_and_ingested(self) -> None:
        """bd r7d7: the coverage probe must not be inert in a Mode B clone.

        The case the other two signals cannot reach. Both are scoped to
        ``meta.discovered_from`` -- what the graph *did* read -- so a file
        it never read is absent from them by construction, and ADR 0101's
        coverage probe is the only signal left. It compares against
        ``discovery-state.json``, which Mode B gitignores, so before the
        fourth amendment it compared against nothing: the clone reported
        ``stale: false`` while a module committed at HEAD sat outside the
        graph, and every later read re-stamped that verdict.

        Asserted end to end rather than on the flag alone, because the
        payoff is the file entering the graph, not the report.
        """
        clone = self.clone()
        (clone / "beta.py").write_text(
            "def beta():\n    return 2\n", encoding="utf-8",
        )
        git(clone, "add", "beta.py")
        git(clone, "commit", "-q", "-m", "add a module the tracked graph never read")

        with mock.patch(
            "weld.discover._discover_single_repo", wraps=wrapped_discover(),
        ) as discover:
            read(clone, "beta")

        self.assertTrue(discover.called, "an uncovered in-scope file must refresh")
        self.assertIn(
            "file:beta",
            graph_nodes(clone),
            "the refresh must ingest the file the tracked graph never read",
        )
        self.assertFalse(
            stale_info(clone)["coverage_stale"],
            "and the repair must settle: one pass, not a loop",
        )

    def test_coverage_hole_is_reported_before_the_refresh_hides_it(self) -> None:
        """The seeded clone reports the hole; ``--no-refresh`` cannot hide it.

        The companion to the case above, split out because a passing
        end-to-end refresh cannot distinguish "the probe fired" from "some
        other signal did". Here the basis is already synthesized and HEAD
        matches it for every file the graph read, so ``coverage_stale`` is
        the only signal that can be true -- and it must be.
        """
        clone = self.clone()
        (clone / "beta.py").write_text(
            "def beta():\n    return 2\n", encoding="utf-8",
        )
        git(clone, "add", "beta.py")
        git(clone, "commit", "-q", "-m", "add a module the tracked graph never read")

        read(clone, "alpha", "--no-refresh")
        ensure_seeded(clone)

        info = stale_info(clone)
        self.assertTrue(info["coverage_stale"], "the uncovered file must be reported")
        self.assertTrue(info["stale"], "and coverage staleness must fold into stale")


class FrozenCloneTests(ModeBFixture):
    """``--no-refresh`` declines synthesis without costing the answer.

    Freezing seeding is only defensible if it leaves the caller where
    they were before seeding existed. For Mode B that is a real answer
    from the committed graph plus the honest stale warning -- which is
    all ``--no-refresh`` ever promised.
    """

    def test_frozen_read_answers_without_writing_a_sidecar(self) -> None:
        clone = self.clone()

        err = read(clone, "alpha", "--no-refresh")

        self.assertFalse(
            (clone / ".weld" / SIDECAR).exists(),
            "synthesis is a write, so the freeze must decline it",
        )
        self.assertIn("--no-refresh in effect", err, "the answer must say it may lag")


class SeededWorktreeReconcileTests(ModeBFixture):
    """A linked worktree reconciles its branch delta off the full path."""

    def test_seeded_worktree_reconciles_incrementally(self) -> None:
        worktree = self.worktree()
        (worktree / "gamma.py").write_text(
            "def gamma():\n    return 3\n", encoding="utf-8",
        )
        git(worktree, "add", "gamma.py")
        git(worktree, "commit", "-q", "-m", "branch adds gamma")

        with mock.patch(
            "weld.discover._discover_single_repo", wraps=wrapped_discover(),
        ) as discover:
            read(worktree, "gamma")

        self.assertTrue(discover.called, "a drifted worktree must refresh")
        self.assertTrue(
            discover.call_args.kwargs.get("incremental"),
            "the seeded discovery state must keep the reconcile incremental",
        )


class ShippedInventoryTests(ModeBFixture):
    """ADR 0110: the artifacts and their records travel together.

    The point of shipping the inventory is that the clone never has to
    guess. Before, a clone derived a conservative record from the graph's
    own anchors, which cannot see the files a strategy read and declined,
    so it reported stale on its first read and paid one full discovery to
    learn them. These tests pin the two halves that removed that pass:
    the record is in git, and it carries claims a derived one may not
    make.
    """

    def test_a_clone_receives_the_records_not_just_the_artifacts(self) -> None:
        listing = weld_listing(self.clone())
        for name in (
            "graph.json",
            "discovery-state.json",
            "file-index.json",
            "file-index-state.json",
        ):
            self.assertIn(name, listing, f"{name} did not travel with the clone")

    def test_the_shipped_record_carries_real_content_hashes(self) -> None:
        """The claim a derived record explicitly withholds.

        A derived record maps every file to the ``unproven`` sentinel,
        which can never equal a ``sha256:`` value, so every file diffs
        dirty and the refresh it schedules is a full one. Real hashes are
        what make the shipped record usable as an incremental basis --
        and they are valid in a checkout that did not build it, because a
        content hash of a committed file is the same in every clone at
        that commit.
        """
        state = load_state(self.clone())
        self.assertTrue(state.files, "the shipped record claims no files")
        for path, digest in state.files.items():
            self.assertTrue(
                digest.startswith("sha256:"),
                f"{path} carries {digest!r}, not a content hash",
            )

    def test_the_record_still_vouches_for_the_graph_beside_it(self) -> None:
        """A copy lands at a new inode; only the digest can settle it.

        ``published_graph`` records ``(sha256, size, mtime_ns)``. A clone
        misses on the stat pair by construction, so this is the case the
        digest fallback exists for -- and getting it wrong would mean the
        clone silently disbelieving a record that is perfectly true.
        """
        clone = self.clone()
        self.assertTrue(
            state_vouches_for_graph(
                load_state(clone), clone / ".weld" / "graph.json",
            ),
        )


class ChokePointWiringTests(ModeBFixture):
    """``ensure_graph_exists`` is the funnel every graph-backed read uses."""

    def test_ensure_graph_exists_seeds_before_it_decides(self) -> None:
        clone = self.clone()

        ensure_graph_exists(clone, 'wd query "x"')

        self.assertTrue((clone / ".weld" / SIDECAR).is_file())


if __name__ == "__main__":
    import unittest

    unittest.main()
