"""Shared fixture for the ADR 0101 coverage-staleness tests.

One tree and one config serve every part of the suite -- the scope-matching
equivalence tests, the staleness/refresh behaviour tests, and the
inventory-vouching tests -- so they never drift apart on what "in scope"
means, nor on what a healthy inventory looks like.

The config deliberately exercises every resolution key the matcher has to
reproduce: a single-directory glob, a globstar glob, a glob with a wildcard
in a *directory* segment, a glob with a ``{a,b}`` alternative group, a
wildcard-free glob, a ``files`` list, a pattern exclude, and a bare
directory-name exclude.

The wildcard-directory-segment entry is bd uhxjc's: the matcher translates
``apps/*/package.json`` correctly and always did, while ``walk_glob``'s flat
branch guarded on ``(root / pattern).parent.is_dir()`` -- the literal path
``<root>/apps/*``, never a directory -- and resolved nothing. That is the
**over**-report this suite singles out as the expensive direction, and the
shape was missing from here, which is why the disagreement went unseen.

The brace-group entry is bd 2z5no's, and it is the same disagreement read
the other way round: ``walk_glob`` expands ``{json,toml}`` before walking,
while the matcher translated the pattern as written -- and ``{`` is escaped
into a literal by that translation, so the entry matched **nothing** there.
An under-report rather than an over-report, so it cost detections instead of
refresh loops, and it was silent for exactly that reason. This shape was
missing from here too.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from weld._source_resolve import resolve_source_file_map


CONFIG = """sources:
  - glob: "src/*.py"
    type: file
    strategy: python_module
  - glob: "pkg/**/*.py"
    type: file
    strategy: python_module
    exclude:
      - "pkg/vendor/**"
      - "generated"
  - glob: "*.md"
    type: doc
    strategy: markdown
  - glob: "apps/*/package.json"
    type: config
    strategy: config_file
  - glob: "cfg/*.{json,toml}"
    type: config
    strategy: config_file
  - files: ["MODULE.bazel"]
    type: config
    strategy: config_file
"""

#: ``rel path -> contents``, covering every scope decision under test.
TREE: dict[str, str] = {
    "src/a.py": "a = 1\n",
    "src/b.py": "b = 2\n",
    # Nested under a single-directory glob: must NOT be in scope.
    "src/deep/c.py": "c = 3\n",
    "pkg/one.py": "one = 1\n",
    "pkg/sub/two.py": "two = 2\n",
    # Excluded by an explicit "**" pattern.
    "pkg/vendor/dep.py": "dep = 1\n",
    # Excluded by a bare directory-name pattern (walk_glob prunes the
    # directory; the path-list matcher must reproduce that).
    "pkg/generated/gen.py": "gen = 1\n",
    "README.md": "hi\n",
    # Wildcard in a directory segment (bd uhxjc): the matcher resolves these
    # by regex, so the walk must resolve them too or they are in scope and
    # uncoverable forever.
    "apps/a/package.json": '{"name": "app-a"}\n',
    "apps/b/package.json": '{"name": "app-b"}\n',
    # One segment deeper than that glob's single ``*`` reaches: in neither
    # answer, so it also pins that the fix did not widen ``*`` into ``**``.
    "apps/b/nested/package.json": '{"name": "nested"}\n',
    # Brace alternatives (bd 2z5no): the walk expands the group before
    # walking, so the matcher must too or both are in scope for the walk and
    # out of scope for the accounting -- the silent half of the same drift.
    "cfg/app.json": '{"debug": false}\n',
    "cfg/app.toml": 'debug = false\n',
    # Neither alternative names it, so the group must not read as "any
    # suffix": in neither answer, which is what pins the expansion narrow.
    "cfg/app.yaml": "debug: false\n",
    "MODULE.bazel": 'module(name = "t")\n',
    # Matched by no source entry at all.
    "notes.txt": "text\n",
}


def run_git(cmd: list[str], cwd: Path) -> None:
    result = subprocess.run(
        cmd, cwd=str(cwd), capture_output=True, text=True, timeout=20,
        env={**os.environ, "LC_ALL": "C"},
    )
    if result.returncode != 0:
        raise AssertionError(
            f"{' '.join(cmd)} failed (rc={result.returncode}):\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


def git_init(root: Path) -> None:
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    run_git(["git", "init", "--quiet"], root)
    run_git(["git", "config", "user.email", "test@test.com"], root)
    run_git(["git", "config", "user.name", "Test"], root)
    run_git(["git", "config", "commit.gpgsign", "false"], root)


def commit_all(root: Path, msg: str) -> None:
    run_git(["git", "add", "-A"], root)
    run_git(["git", "commit", "-m", msg, "--quiet"], root)


def write_tree(root: Path) -> None:
    """Materialize :data:`TREE` plus the discovery config under *root*."""
    (root / ".weld").mkdir(parents=True, exist_ok=True)
    (root / ".weld" / "discover.yaml").write_text(CONFIG, encoding="utf-8")
    for rel, body in TREE.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")


def make_repo(root: Path) -> None:
    """Git-init *root*, write the tree, and commit it."""
    git_init(root)
    write_tree(root)
    commit_all(root, "initial")


def sources(root: Path) -> list[dict]:
    from weld._staleness_coverage import _load_sources
    return _load_sources(root)


def walked_files(root: Path) -> set[str]:
    """The file set a real glob walk resolves -- the equivalence reference."""
    return {
        f
        for files in resolve_source_file_map(root, sources(root))
        for f in files
    }


def indexed_files(root: Path) -> set[str]:
    """The boundary snapshot's view -- what ``git ls-files`` reports."""
    from weld.repo_boundary import get_repo_boundary
    return set(get_repo_boundary(root).visible_files or ())


class CoverageFixture(unittest.TestCase):
    """A committed repo whose inventory can be holed at will.

    Shared by both halves of ADR 0101: the coverage probe itself
    (``weld_coverage_staleness_test``) and the inventory-to-graph binding that
    decides whether an inventory may speak for the graph at all
    (``weld_inventory_vouching_test``).
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        make_repo(self.root)

    def save_state(
        self,
        *,
        omit: set[str] | None = None,
        no_nodes: set[str] | None = None,
        graph_published: bool = True,
    ) -> None:
        """Record an inventory covering the walked set minus *omit*.

        *graph_published* defaults to the healthy case (the writing run also
        wrote the graph) so scope can be exercised in isolation. It records
        the *identity* of whatever graph is on disk (bd wq9i), which is what a
        real publishing run stamps -- with no graph there, there is nothing to
        name and the state vouches for nothing.
        """
        from weld._discover_state_check import published_graph_token
        from weld.discovery_state import DiscoveryState, save_state

        covered = walked_files(self.root) - (omit or set())
        save_state(
            self.root,
            DiscoveryState(
                files={f: f"sha256:{f}" for f in covered},
                files_with_no_nodes=set(no_nodes or ()),
                published_graph=(
                    published_graph_token(self.root / ".weld" / "graph.json")
                    if graph_published else None
                ),
            ),
        )
