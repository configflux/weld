"""Which checkout a fresh worktree seeds from (ADR 0096 §2 gate 5).

Seed-source resolution is the part of gate 5 that must not encode a
layout. It reads ``git worktree list --porcelain`` and nothing else -- no
path pattern, no assumption about which tool created a worktree or where
it put it -- so the tests here are layouts, each built with real git:

* the ordinary one, where the primary checkout holds the graph;
* a primary that has no graph, where a sibling worktree must be used;
* a **bare-repo hub**, where the primary has no working tree at all and
  therefore can never be the source.

A layout weld had to special-case would show up here as a branch in the
code; there is none, which is the point.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from weld._worktree_seed import ensure_seeded
from weld.tests._mode_a_fixture import (
    ALPHA_NODE,
    SIDECAR,
    ModeAFixture,
    discover,
    git,
    graph_nodes,
    seed_mode_a_repo,
)
from weld.tests._seed_fixture import GIT_ENV


class SourcePreferenceTests(ModeAFixture):
    """Primary first, then the remaining worktrees in registration order."""

    def test_primary_checkout_wins_when_it_has_a_graph(self) -> None:
        other = self.worktree("other")
        discover(other)
        target = self.worktree("target")

        result = ensure_seeded(target)

        self.assertEqual(result["source"], str(self.origin))

    def test_graphless_primary_is_skipped_for_a_sibling(self) -> None:
        """A primary mid-``wd init`` must not stop a worktree from seeding."""
        donor = self.worktree("donor")
        discover(donor)
        (self.origin / ".weld" / "graph.json").unlink()
        (self.origin / ".weld" / SIDECAR).unlink()
        target = self.worktree("target")

        result = ensure_seeded(target)

        self.assertEqual(result["source"], str(donor))
        self.assertIn(ALPHA_NODE, graph_nodes(target))

    def test_a_source_without_a_sidecar_is_not_chosen(self) -> None:
        """A checkout that cannot date its own graph is a worse source.

        The sidecar carries the ``git_sha`` the seed is stamped with, so a
        candidate missing it is skipped in favour of the next one rather
        than landing a graph with no basis at all.
        """
        donor = self.worktree("donor")
        discover(donor)
        (self.origin / ".weld" / SIDECAR).unlink()
        target = self.worktree("target")

        result = ensure_seeded(target)

        self.assertEqual(result["source"], str(donor))

    def test_a_worktree_never_seeds_from_itself(self) -> None:
        """Self-exclusion is by realpath, so a symlinked spelling cannot slip."""
        target = self.worktree("target")
        link = self.tmp / "target-link"
        link.symlink_to(target)

        result = ensure_seeded(link)

        self.assertEqual(result["source"], str(self.origin))


class BareRepoHubTests(unittest.TestCase):
    """A bare primary has no working tree, so a sibling worktree must seed.

    This is the layout that defeats every path-pattern heuristic: there is
    no "main checkout" directory to look in, only a ``.git`` hub with
    worktrees hanging off it. Gate 5 needs no special case -- the bare
    entry ``git worktree list`` reports simply has no readable graph.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        origin = self.tmp / "origin"
        seed_mode_a_repo(origin, run_discover=False)
        self.hub = self.tmp / "hub.git"
        subprocess.run(
            ["git", "clone", "-q", "--bare", str(origin), str(self.hub)],
            check=True, capture_output=True, text=True, env=GIT_ENV,
        )

    def _worktree(self, name: str) -> Path:
        target = self.tmp / name
        git(self.hub, "worktree", "add", "-q", "-b", name, str(target))
        return target

    def test_sibling_worktree_seeds_when_the_primary_is_bare(self) -> None:
        donor = self._worktree("donor")
        discover(donor)
        target = self._worktree("target")

        result = ensure_seeded(target)

        self.assertIsNotNone(result, "a bare hub must not block seeding")
        self.assertEqual(result["source"], str(donor))
        self.assertIn(ALPHA_NODE, graph_nodes(target))

    def test_bare_hub_alone_offers_no_source(self) -> None:
        target = self._worktree("target")

        self.assertIsNone(ensure_seeded(target))
        self.assertFalse((target / ".weld" / "graph.json").exists())


if __name__ == "__main__":
    unittest.main()
