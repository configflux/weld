"""Shared Mode B checkout fixture for the ADR 0096 §2 seeding tests.

One ``--track-graphs`` repository, plus the two ways a developer meets it
for the first time: ``git clone`` and ``git worktree add``. The git and
discover plumbing they run on is shared with the Mode A suites and lives
in :mod:`_seed_fixture`; what is Mode B is the committed graph.

Imported by :mod:`weld_mode_b_sidecar_synthesis_test` (the end-to-end
behaviour) and :mod:`weld_worktree_seed_gates_test` (the gate matrix), so
the fixture is written once and both suites see the same checkout. The
names re-exported below are part of that contract -- importers name this
module, not the plumbing underneath it.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld._gitignore_writer import TRACK_GRAPHS_GITIGNORE
from weld.tests._seed_fixture import (  # noqa: F401 -- re-exported fixture API
    SIDECAR,
    commit_all,
    discover,
    git,
    graph_nodes,
    init_repo,
    read,
    sidecar,
    stale_info,
    weld_listing,
    wrapped_discover,
)


#: The Mode B policy weld wrote before ADR 0110: the graph is tracked and
#: every record that explains it is gitignored, so a clone arrives holding a
#: graph and no account of what it read.
#:
#: Kept here rather than in the product because the product no longer writes
#: it -- but repositories initialised by an earlier weld still *have* it, and
#: they are the entire population the seeding and synthesis machinery serves.
#: A suite that pins that machinery has to stand in such a checkout; run
#: against today's policy it would be testing a path that can no longer be
#: reached, and would pass or fail for reasons unrelated to what it claims.
LEGACY_TRACK_GRAPHS_GITIGNORE = """\
discovery-state.json
graph-previous.json
workspace-state.json
workspace.lock
graph.write.lock
query_state.bin
graph.db
graph-meta.json
file-index-state.json
graph-communities.json
graph-community-report.md
graph-community-index.md
telemetry.jsonl
auto-refresh.jsonl
review-state.json
"""


def seed_mode_b_repo(root: Path, gitignore: str = TRACK_GRAPHS_GITIGNORE) -> None:
    """Init a ``--track-graphs`` repo whose committed tree carries the graph.

    Under today's policy (ADR 0110) that means the graph *and* the records
    that explain it -- ``discovery-state.json``, ``file-index.json``,
    ``file-index-state.json`` -- travel together; only ``graph-meta.json``
    and the rest of the per-machine state stay gitignored.

    Pass :data:`LEGACY_TRACK_GRAPHS_GITIGNORE` for the older posture, where
    a clone gets the graph and none of the state.
    """
    init_repo(root, gitignore)
    discover(root)
    commit_all(root, "mode B: track the graph")


def graph_commit(root: Path) -> str:
    """The commit ``tracked_graph_commit`` should resolve to for *root*."""
    return git(root, "log", "-n", "1", "--format=%H", "--", ".weld/graph.json")


class ModeBFixture(unittest.TestCase):
    """A committed Mode B repo, plus clones and worktrees on demand.

    Subclasses override :attr:`GITIGNORE` with
    :data:`LEGACY_TRACK_GRAPHS_GITIGNORE` to get the pre-ADR-0110 posture.
    """

    #: Which Mode B policy the origin repo was initialised with.
    GITIGNORE: str = TRACK_GRAPHS_GITIGNORE

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.origin = self.tmp / "origin"
        seed_mode_b_repo(self.origin, self.GITIGNORE)

    def clone(self) -> Path:
        """A plain clone: tracked graph, no sibling worktree to seed from."""
        target = self.tmp / "clone"
        git(self.tmp, "clone", "-q", str(self.origin), str(target))
        git(target, "config", "user.email", "test@example.com")
        git(target, "config", "user.name", "Weld Test")
        return target

    def worktree(self, name: str = "feature") -> Path:
        """A linked worktree: the primary checkout is available as a seed."""
        target = self.tmp / name
        git(self.origin, "worktree", "add", "-q", "-b", name, str(target))
        return target
