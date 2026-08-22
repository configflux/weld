"""Repeat reads over a settled dirty tree re-discover once, not once per read.

This is the outcome bd o18k's process-local refresh cache used to deliver and
that bd 0jay moved into the freshness signal itself. o18k's premise was that
the ADR 0017 working-tree dimension marked the graph ``source_stale`` for as
long as *any* uncommitted edit was held, so auto-refresh (ADR 0051) re-ran
discovery on every following read; the cache short-circuited the second and
later reads by working-tree signature. 0jay removed the premise -- a dirty tree
whose content the ADR 0008 inventory already holds now reports **fresh** -- so
``auto_refresh_if_stale`` returns at its own staleness guard and never reaches
a cache at all. Measured on this repo (bd hmaz): 60 reads, 11 of them reaching
the cache, **0** hits. The cache was removed; this pins what has to keep
holding without it.

The property is stated over ``auto_refresh_if_stale`` -- the caller that used
to consult the cache -- rather than over ``compute_stale_info``, because
"discovery does not re-run" is the user-visible cost, and only the caller can
show it. ``weld_dirty_worktree_settles_test`` pins the *signal* settling from
the same fixture shape; this pins the *work* not repeating.

Three assertions, and each rules out one way of passing for the wrong reason:

* discovery runs **exactly once** across repeated reads of an unchanging dirty
  tree -- the o18k pathology, and the thing a cache removal could regress;
* the graph reports **fresh** between those reads -- so the single run is the
  staleness signal settling, not some cache or a swallowed refresh failure
  quietly suppressing work that was still owed. Without this a broken refresh
  path that always returned ``None`` would satisfy the count;
* a **further edit re-runs** discovery -- so the count is not satisfied by an
  over-eager "never refresh again", which is the opposite failure and the one
  that would serve a stale graph forever.
"""

from __future__ import annotations

import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from weld import discover as _discover_mod
from weld._auto_refresh import auto_refresh_if_stale
from weld._graph_meta_sidecar import load_graph_meta
from weld._staleness import compute_stale_info
from weld.discover import _discover_single_repo

CONFIG = """sources:
  - glob: "src/*.py"
    type: file
    strategy: python_module
"""

TREE: dict[str, str] = {
    "src/a.py": "def a():\n    return 1\n",
    "src/b.py": "def b():\n    return 2\n",
}


def _run(cmd: list[str], cwd: Path) -> None:
    result = subprocess.run(
        cmd, cwd=str(cwd), capture_output=True, text=True, timeout=60,
        env={**os.environ, "LC_ALL": "C"},
    )
    if result.returncode != 0:
        raise AssertionError(
            f"{' '.join(cmd)} failed (rc={result.returncode}):\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


class AutoRefreshSettlesOnceTest(unittest.TestCase):
    """A real repo, a real graph, and a real count of discovery runs."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / ".weld").mkdir(parents=True, exist_ok=True)
        _run(["git", "init", "--quiet"], self.root)
        _run(["git", "config", "user.email", "test@test.com"], self.root)
        _run(["git", "config", "user.name", "Test"], self.root)
        _run(["git", "config", "commit.gpgsign", "false"], self.root)
        (self.root / ".weld" / "discover.yaml").write_text(
            CONFIG, encoding="utf-8"
        )
        for rel, body in TREE.items():
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        _run(["git", "add", "-A"], self.root)
        _run(["git", "commit", "-m", "initial", "--quiet"], self.root)
        _discover_single_repo(self.root, incremental=None, write_graph=True)

        # Count real discovery runs. ``_do_refresh`` late-imports
        # ``_discover_single_repo`` from ``weld.discover`` on every call, so
        # patching the attribute *there* is what the refresh path resolves.
        # The setUp discovery above ran through the module-level import, which
        # is bound before this and so is never counted.
        self.runs = 0
        real = _discover_mod._discover_single_repo

        def _counting(*args, **kwargs):
            self.runs += 1
            return real(*args, **kwargs)

        _discover_mod._discover_single_repo = _counting
        self.addCleanup(setattr, _discover_mod, "_discover_single_repo", real)

    def read(self, times: int = 1) -> None:
        """Drive the read path the way a `wd query` would.

        ``env={}`` is load-bearing, not tidiness: ``WELD_AUTO_REFRESH=0``
        turns auto-refresh off at the top of ``auto_refresh_if_stale``, and
        this repository's quality-gate wrapper exports exactly that for its
        whole run so graph reads cannot rewrite a tracked graph mid-run.
        Inheriting the ambient environment would let that setting silence
        every refresh here and satisfy "discovery ran once" with zero runs --
        the assertions below would then pass by never doing anything.
        """
        for _ in range(times):
            auto_refresh_if_stale(self.root, stderr=io.StringIO(), env={})

    def is_stale(self) -> bool:
        graph_path = self.root / ".weld" / "graph.json"
        meta = dict(load_graph_meta(graph_path))
        return bool(compute_stale_info(graph_path, meta)["stale"])

    def test_repeat_reads_of_a_held_edit_discover_once(self) -> None:
        (self.root / "src" / "a.py").write_text(
            "def a():\n    return 99\n", encoding="utf-8"
        )
        self.assertTrue(self.is_stale(), "edit must be stale before any read")

        self.read(5)

        self.assertEqual(
            self.runs, 1,
            "a held edit must re-discover once, not once per read -- the bd "
            "o18k pathology, now prevented by the bd 0jay settle rather than "
            "by a refresh cache",
        )
        self.assertFalse(
            self.is_stale(),
            "the single run must have SETTLED the graph -- if it still reads "
            "stale, reads 2-5 were skipped for some other reason and the "
            "count above proves nothing",
        )

    def test_a_further_edit_discovers_again(self) -> None:
        (self.root / "src" / "a.py").write_text(
            "def a():\n    return 99\n", encoding="utf-8"
        )
        self.read(3)
        self.assertEqual(self.runs, 1)

        (self.root / "src" / "b.py").write_text(
            "def b():\n    return 77\n", encoding="utf-8"
        )
        self.assertTrue(self.is_stale(), "a new edit must re-open staleness")

        self.read(3)

        self.assertEqual(
            self.runs, 2,
            "real new content must re-discover -- 'once' is a property of an "
            "unchanging tree, not a licence to stop refreshing",
        )
        self.assertFalse(self.is_stale())

    def test_clean_tree_never_discovers(self) -> None:
        self.read(4)
        self.assertEqual(
            self.runs, 0,
            "a clean committed tree at the discovered sha is fresh; the read "
            "path must not run discovery at all",
        )


if __name__ == "__main__":
    unittest.main()
