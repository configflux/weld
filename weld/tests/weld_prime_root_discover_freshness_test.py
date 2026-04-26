"""Customer 5-step repro for bd-5038-yb89.

Default ``wd init`` writes a discover.yaml whose strategies record
``discovered_from=['./']`` -- the repo-root marker meaning "any path is
tracked." After committing the freshly-written graph + state, ``wd
prime`` reported the graph as stale because the diff
``graph_sha..HEAD`` contains weld's own bookkeeping files
(``.weld/graph.json``, ``.weld/discovery-state.json``) and the broad
``'./'`` prefix matched them. Those files are weld outputs, never
user source -- they must not contribute to ``source_stale``.

This file pins the bookkeeping-exclusion contract introduced in
``weld/_git.py`` and runs the customer's 5-step CLI repro end-to-end.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld._git import get_git_sha  # noqa: E402
from weld.prime import prime  # noqa: E402
from weld.tests.weld_prime_freshness_test import (  # noqa: E402
    _commit_all,
    _enriched_nodes,
    _git_init,
    _run,
    _setup_weld_repo,
    _write_graph,
)


class PrimeRootDiscoveredFromTest(unittest.TestCase):
    """``discovered_from=['./']`` must not match weld bookkeeping files."""

    def test_root_discovered_from_excludes_bookkeeping(self) -> None:
        """Step 3 of customer repro: fresh after committing graph+state."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sha0 = _setup_weld_repo(root)
            _write_graph(
                root, git_sha=sha0, discovered_from=["./"],
                nodes=_enriched_nodes(),
            )
            # Commit the bookkeeping files. HEAD advances; the only
            # changed paths are weld's own outputs.
            (root / ".weld" / "discovery-state.json").write_text(
                "{}", encoding="utf-8",
            )
            _commit_all(root, "add graph")

            output = prime(root)

            self.assertNotIn("wd discover", output, output)
            self.assertNotIn("behind HEAD", output, output)
            self.assertIn("up to date", output)

    def test_root_discovered_from_excludes_touch_drift(self) -> None:
        """Step 5 of customer repro: fresh after `wd touch` + commit."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sha0 = _setup_weld_repo(root)
            # Pretend the user did step 2 (graph committed at sha0).
            _write_graph(
                root, git_sha=sha0, discovered_from=["./"],
                nodes=_enriched_nodes(),
            )
            (root / ".weld" / "discovery-state.json").write_text(
                "{}", encoding="utf-8",
            )
            _commit_all(root, "add graph")
            sha1 = get_git_sha(root)
            assert sha1 is not None
            # `wd touch` re-stamps meta.git_sha to current HEAD.
            _write_graph(
                root, git_sha=sha1, discovered_from=["./"],
                nodes=_enriched_nodes(11),
            )
            _commit_all(root, "touch graph")

            output = prime(root)

            self.assertNotIn("wd discover", output, output)
            self.assertNotIn("wd touch", output, output)
            self.assertNotIn("behind HEAD", output, output)
            self.assertIn("up to date", output)

    def test_root_discovered_from_real_source_change_still_stale(self) -> None:
        """Regression: with ``discovered_from=['./']``, real source
        changes must still mark the graph as source-stale."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sha0 = _setup_weld_repo(root)
            _write_graph(
                root, git_sha=sha0, discovered_from=["./"],
                nodes=_enriched_nodes(),
            )
            (root / "src" / "a.py").write_text("x = 99\n", encoding="utf-8")
            _commit_all(root, "real source change")

            output = prime(root)
            self.assertIn("wd discover --output .weld/graph.json", output)


class PrimeCustomerFiveStepReproTest(unittest.TestCase):
    """End-to-end subprocess repro for bd-5038-yb89.

    Replays the customer's exact 5-step scenario through the real
    ``python -m weld`` CLI: init, discover --output, commit graph,
    expect fresh; then touch, commit graph, expect fresh again.
    """

    def _wd(self, args: list[str], cwd: Path) -> str:
        env = {**os.environ, "LC_ALL": "C", "PYTHONPATH": _repo_root}
        result = subprocess.run(
            [sys.executable, "-m", "weld", *args],
            cwd=str(cwd), capture_output=True, text=True, timeout=60,
            env=env,
        )
        if result.returncode != 0:
            raise AssertionError(
                f"wd {' '.join(args)} failed (rc={result.returncode}):\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        return result.stdout

    def test_customer_five_step_repro_fresh_at_steps_3_and_5(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # Step 1: scratch + git init + seed sources + wd init + commit.
            _git_init(root)
            (root / "app.py").write_text(
                'def hello() -> str:\n    return "world"\n', encoding="utf-8",
            )
            self._wd(["init"], root)
            _run(
                ["git", "add", "app.py", ".weld/discover.yaml", ".weld/.gitignore"],
                root,
            )
            _run(["git", "commit", "-qm", "seed sources"], root)

            # Step 2: discover then commit graph + state.
            # graph.json AND discovery-state.json are both ignored by the
            # config-only .weld/.gitignore that wd init now writes by
            # default; force-add to faithfully reproduce the customer
            # scenario (which committed both files to the repo).
            self._wd(["discover", "--output", ".weld/graph.json"], root)
            _run(["git", "add", "-f", ".weld/graph.json"], root)
            _run(
                ["git", "add", "-f", ".weld/discovery-state.json"],
                root,
            )
            _run(["git", "commit", "-qm", "add graph"], root)

            # Step 3: prime must report fresh.
            out3 = self._wd(["prime"], root)
            self.assertNotIn("behind HEAD", out3, out3)
            self.assertNotIn(
                "wd discover --output .weld/graph.json", out3, out3,
            )

            # Step 4: touch then commit graph (state may also rewrite).
            self._wd(["touch"], root)
            _run(["git", "add", "-A", ".weld/"], root)
            _run(["git", "commit", "-qm", "touch graph"], root)

            # Step 5: prime must still report fresh.
            out5 = self._wd(["prime"], root)
            self.assertNotIn("behind HEAD", out5, out5)
            self.assertNotIn(
                "wd discover --output .weld/graph.json", out5, out5,
            )


if __name__ == "__main__":
    unittest.main()
