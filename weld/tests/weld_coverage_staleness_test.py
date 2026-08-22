"""Coverage staleness (ADR 0101).

The reported failure: a read answered "no match" for a module that had
shipped hours earlier, and stamped the answer ``{stale: false,
commits_behind: 0}``. The graph's recorded SHA was HEAD and the tree was
clean, so ADR 0017's two signals -- a ``graph_sha..HEAD`` diff over
``meta.discovered_from`` and a git-status probe over the same prefixes -- both
had nothing to look at. A file the graph never ingested is invisible to both,
and every later refresh re-stamped the same SHA, so the blind spot was
permanent rather than transient.

These tests pin the third question -- does the graph's own inventory still
cover what discovery would resolve today? -- end to end: the probe itself,
its fold into ``compute_stale_info``, and the refresh it has to trigger.
Scope-matching equivalence lives in ``weld_coverage_scope_match_test``.

The *other* half of "covered" -- whether the inventory has any standing to
describe ``graph.json`` in the first place -- is
``weld_inventory_vouching_test``.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from weld._git import get_git_sha
from weld._staleness import compute_stale_info
from weld._staleness_coverage import (
    coverage_stale,
    coverage_stale_detail,
    files_missing_from_inventory,
)
from weld._stale_reasons import NEVER_INGESTED
from weld.discovery_state import DiscoveryState, save_state
from weld.tests._coverage_stale_lib import (
    CoverageFixture,
    commit_all,
    indexed_files,
    write_tree,
)


class CoverageStaleTest(CoverageFixture):
    """``coverage_stale`` on converged, holed, and degraded inputs."""

    def test_converged_inventory_is_not_stale(self) -> None:
        self.save_state()
        self.assertFalse(coverage_stale(self.root))

    def test_missing_in_scope_file_is_stale(self) -> None:
        # The reported bug in miniature: a shipped, committed, clean file
        # that the inventory never recorded.
        self.save_state(omit={"src/b.py"})
        self.assertTrue(coverage_stale(self.root))
        self.assertEqual(files_missing_from_inventory(self.root), {"src/b.py"})

    def test_missing_in_scope_file_names_itself(self) -> None:
        # The boolean gate has a full-enumeration companion that names the
        # uncovered file and why.
        self.save_state(omit={"src/b.py"})
        self.assertEqual(
            coverage_stale_detail(self.root),
            [{"path": "src/b.py", "reason": NEVER_INGESTED}],
        )

    def test_converged_inventory_names_nothing(self) -> None:
        self.save_state()
        self.assertEqual(coverage_stale_detail(self.root), [])

    def test_newly_added_committed_file_is_stale(self) -> None:
        self.save_state()
        (self.root / "src" / "new.py").write_text("n = 1\n", encoding="utf-8")
        commit_all(self.root, "add module")
        self.assertEqual(files_missing_from_inventory(self.root), {"src/new.py"})

    def test_out_of_scope_file_does_not_flip_coverage(self) -> None:
        self.save_state()
        (self.root / "extra.txt").write_text("x\n", encoding="utf-8")
        commit_all(self.root, "add non-source")
        self.assertFalse(coverage_stale(self.root))

    def test_files_with_no_nodes_are_not_treated_as_missing(self) -> None:
        # A file discovery legitimately produces nothing for (an empty
        # __init__.py and friends) is tracked separately; counting it as
        # uncovered would refresh on every read forever.
        self.save_state(omit={"src/b.py"}, no_nodes={"src/b.py"})
        self.assertFalse(coverage_stale(self.root))

    def test_deleted_but_still_indexed_file_is_not_stale(self) -> None:
        # The boundary snapshot reads ``git ls-files --cached``, which keeps
        # listing a file removed from the working tree until the removal is
        # staged. No glob walk can resolve it, so no discovery run could ever
        # cover it -- counting it as missing would refresh on every read.
        self.save_state(omit={"src/b.py"})
        (self.root / "src" / "b.py").unlink()
        self.assertIn(
            "src/b.py", indexed_files(self.root),
            "fixture precondition: git must still list the deleted file",
        )
        self.assertFalse(coverage_stale(self.root))

    def test_no_discovery_state_is_not_stale(self) -> None:
        # Nothing to be stale against; the missing-graph guard owns first run.
        self.assertFalse(coverage_stale(self.root))

    def test_no_sources_is_not_stale(self) -> None:
        self.save_state()
        (self.root / ".weld" / "discover.yaml").write_text(
            "sources: []\n", encoding="utf-8",
        )
        self.assertFalse(coverage_stale(self.root))

    def test_missing_config_is_not_stale(self) -> None:
        self.save_state()
        (self.root / ".weld" / "discover.yaml").unlink()
        self.assertFalse(coverage_stale(self.root))

    def test_unparseable_config_is_not_stale(self) -> None:
        self.save_state(omit={"src/b.py"})
        (self.root / ".weld" / "discover.yaml").write_text(
            "sources: [unterminated\n", encoding="utf-8",
        )
        self.assertFalse(coverage_stale(self.root))

    def test_non_git_root_is_not_stale(self) -> None:
        import shutil
        non_git = Path(tempfile.mkdtemp())
        try:
            write_tree(non_git)
            save_state(non_git, DiscoveryState(files={"src/a.py": "sha256:x"}))
            self.assertFalse(coverage_stale(non_git))
        finally:
            shutil.rmtree(non_git, ignore_errors=True)


class ComputeStaleInfoCoverageTest(CoverageFixture):
    """The reported envelope: SHA at HEAD, clean tree, module missing."""

    def setUp(self) -> None:
        super().setUp()
        self._sha0 = get_git_sha(self.root)
        assert self._sha0 is not None

    def _info(self) -> dict:
        meta = {"git_sha": self._sha0, "discovered_from": ["src/", "pkg/"]}
        return compute_stale_info(self.root / ".weld" / "graph.json", meta)

    def test_converged_graph_stays_fresh(self) -> None:
        self.save_state()
        info = self._info()
        self.assertFalse(info["stale"], info)
        self.assertFalse(info["coverage_stale"], info)
        self.assertEqual(info["stale_sources"], [])
        self.assertEqual(info["stale_sources_omitted"], 0)

    def test_uningested_module_is_stale_at_head(self) -> None:
        # Before ADR 0101 this returned {stale: False, commits_behind: 0}
        # while the graph was missing a shipped module.
        self.save_state(omit={"src/b.py"})
        info = self._info()
        self.assertTrue(info["coverage_stale"], info)
        self.assertTrue(info["source_stale"], info)
        self.assertTrue(info["stale"], info)
        # The misleading part of the original report: HEAD had not moved.
        self.assertFalse(info["sha_behind"], info)
        self.assertEqual(info["commits_behind"], 0, info)
        # The verdict now names the file and why.
        self.assertEqual(
            info["stale_sources"],
            [{"path": "src/b.py", "reason": NEVER_INGESTED}],
        )
        self.assertEqual(info["stale_sources_omitted"], 0)

    def test_non_git_root_reports_coverage_key(self) -> None:
        import shutil
        non_git = Path(tempfile.mkdtemp())
        try:
            (non_git / ".weld").mkdir(parents=True)
            info = compute_stale_info(non_git / ".weld" / "graph.json", {})
            self.assertFalse(info["coverage_stale"], info)
        finally:
            shutil.rmtree(non_git, ignore_errors=True)

    def test_probe_never_walks_the_tree(self) -> None:
        # The structural reason the probe is affordable on the read path: it
        # matches a `git ls-files` snapshot in memory instead of resolving
        # globs, which costs ~730ms on this repo. Gate on that structure
        # rather than on a wall-clock number -- if anyone reaches for the
        # real resolver here, this fails deterministically on any machine.
        self.save_state()
        walked = []

        def _forbidden(*args, **kwargs):
            walked.append(args)
            raise AssertionError("coverage probe walked the tree")

        with mock.patch("os.walk", _forbidden), \
                mock.patch("pathlib.Path.glob", _forbidden):
            info = self._info()
        self.assertEqual(walked, [])
        self.assertFalse(info["coverage_stale"], info)

        # Timing is advisory only (ADR 0051 budgets 1s for the whole no-op
        # refresh path; this slice measures ~45ms on this repo).
        start = time.monotonic()
        self._info()
        print(
            "[advisory] coverage stale check: "
            f"{(time.monotonic() - start) * 1000:.0f}ms"
        )


class AutoRefreshFiresOnCoverageGapTest(CoverageFixture):
    """The gap must drive a real refresh, not merely report itself."""

    def setUp(self) -> None:
        super().setUp()
        self._sha0 = get_git_sha(self.root)

    def _write_graph(self) -> None:
        from weld.contract import SCHEMA_VERSION
        payload = {
            "meta": {
                "version": SCHEMA_VERSION,
                "updated_at": "2026-08-13T12:00:00+00:00",
                "git_sha": self._sha0,
                "discovered_from": ["src/", "pkg/"],
            },
            "nodes": {},
            "edges": [],
        }
        (self.root / ".weld" / "graph.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8",
        )

    def _refresh(self) -> dict | None:
        from weld._auto_refresh import auto_refresh_if_stale
        env = {k: v for k, v in os.environ.items() if k != "WELD_AUTO_REFRESH"}
        return auto_refresh_if_stale(
            self.root, env=env, stderr=io.StringIO(),
        )

    def test_coverage_gap_triggers_auto_refresh(self) -> None:
        self._write_graph()
        self.save_state(omit={"src/b.py"})
        self.assertIsNotNone(
            self._refresh(),
            "an uningested in-scope module should trigger refresh",
        )

    def test_refresh_closes_the_gap(self) -> None:
        # The point of the signal: one read repairs the graph, so the same
        # miss cannot persist across reads the way the reported one did.
        self._write_graph()
        self.save_state(omit={"src/b.py"})
        self._refresh()
        self.assertFalse(
            coverage_stale(self.root), "refresh left the coverage hole open",
        )

    def test_converged_graph_does_not_refresh(self) -> None:
        self._write_graph()
        self.save_state()
        self.assertIsNone(
            self._refresh(), "a fully covered graph must not refresh",
        )


if __name__ == "__main__":
    unittest.main()
