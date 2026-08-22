"""Regression: an unproven inventory is not a valid incremental basis.

``discovery-state.json`` lists the files a discovery run *resolved*, at the
hashes it saw. Diffing that inventory against the tree is a sound way to
decide what to re-extract only when the graph being updated is the graph that
same run published. Otherwise the delta is computed against one generation's
content and applied to another generation's body.

ADR 0101 (bd esww) named that coupling and recorded it as ``graph_published``,
but wired it only into the freshness *report* (``coverage_stale``). The
incremental *basis* decision kept its original three questions -- is there a
state, is there a graph, does the graph parse -- and never asked whether the
state describes that graph. A run that resolved files without publishing a
graph (the library ``discover()`` / ``--output``-elsewhere shape, or an
interruption between the two writes) therefore left an inventory the next
auto-detected refresh trusted: the hashes already matched the tree, so nothing
was dirty, ``_no_change_refresh`` returned the older body byte-pristine, and
the run then stamped ``graph_published=True`` over it. The doubt was not
repaired, it was converged away.

The ADR 0008 per-file repair cannot close this hole, which is where ADR 0101's
"one incremental pass repairs it" reasoning failed:
``files_missing_from_graph`` catches only files carrying *zero* nodes. A file
whose content changed still carries nodes -- the wrong ones -- so it is
invisible to the hash diff (the state already matches the tree) and to the
per-file audit alike. In-place edits are therefore the only fixture that
reproduces this; adding or deleting a file would be caught by machinery that
already works.

Observed as bd nwyq: a fresh worktree seeded from a checkout in exactly this
state answered from a body two commits behind while printing ``reconciled to
<branch>@<HEAD>`` and ``no files changed, graph is up to date``.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from weld import discover as discover_mod
from weld.discover import _discover_single_repo
from weld.discovery_state import build_file_hashes, load_state
from weld.tests._unproven_state_lib import (
    MOD as _MOD,
    build_fixture as _build_fixture,
    desync as _desync,
    exports as _exports,
    on_disk as _on_disk,
    vouches as _vouches,
)


class UnprovenInventoryTests(unittest.TestCase):
    """A state that cannot vouch for ``graph.json`` forces full discovery."""

    def _desynced_root(self) -> Path:
        """A root whose state matches the tree and whose graph does not."""
        td = tempfile.TemporaryDirectory(prefix="unproven-state-")
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        _build_fixture(root)
        _desync(root)

        self.assertEqual(
            _exports(_on_disk(root)), ["helper"],
            "fixture invariant: the graph on disk must still describe the "
            "pre-rewrite content",
        )
        state = load_state(root)
        self.assertIsNotNone(state, "fixture invariant: a state was written")
        self.assertFalse(
            _vouches(state, root),
            "fixture invariant: the run that wrote the inventory did not "
            "publish the graph, so the inventory is unproven",
        )
        self.assertEqual(
            state.files[_MOD], build_file_hashes(root, [_MOD])[_MOD],
            "fixture invariant: the inventory already matches the tree, so "
            "the hash diff has nothing to report -- this is what makes the "
            "stale body invisible",
        )
        return root

    def test_unproven_inventory_is_re_derived_in_full(self) -> None:
        """The served graph must describe the tree, not the older body.

        Without the fix the auto-detected refresh takes the incremental path,
        finds no content delta, and returns the pre-rewrite body while
        reporting the graph up to date.
        """
        root = self._desynced_root()

        graph = _discover_single_repo(root, incremental=None, write_graph=True)

        self.assertEqual(
            _exports(graph), ["replacement"],
            "discovery served a graph derived from content the tree no "
            "longer holds: an inventory that never published a graph cannot "
            "be used as the delta basis for one",
        )
        self.assertEqual(
            _exports(_on_disk(root)), ["replacement"],
            "the re-derived graph must also land on disk, or the next read "
            "re-serves the stale body",
        )

    def test_re_derivation_converges_after_one_pass(self) -> None:
        """The doubt is repaired once, then the fast path returns.

        ADR 0101 section 4's asymmetry: over-reporting is safe only if it
        settles. The full pass publishes graph and inventory together, which
        re-stamps the flag, so the next run has a proven basis and does no
        strategy work at all.
        """
        root = self._desynced_root()

        _discover_single_repo(root, incremental=None, write_graph=True)

        self.assertTrue(
            _vouches(load_state(root), root),
            "the repairing run publishes graph and inventory together, so it "
            "must leave the state proven",
        )

        with mock.patch.object(
            discover_mod, "_run_source", wraps=discover_mod._run_source,
        ) as spy:
            _discover_single_repo(root, incremental=None, write_graph=True)

        self.assertEqual(
            spy.call_count, 0,
            "the downgrade must settle after one pass: a root that keeps "
            "re-deriving in full on every read has traded a silent wrong "
            "answer for a permanent cold start",
        )

    def test_the_downgrade_is_announced(self) -> None:
        """Silence is what made this cost two investigations.

        The three downgrades beside this one each say why they gave up the
        incremental path; this one has to as well, on stderr like the rest.
        """
        root = self._desynced_root()

        err = io.StringIO()
        with redirect_stderr(err):
            _discover_single_repo(root, incremental=None, write_graph=True)
        printed = err.getvalue()

        self.assertIn(
            "running full discovery", printed,
            "the downgrade to full discovery must be reported",
        )
        self.assertIn(
            "graph.json", printed,
            "the notice must name what the state failed to describe",
        )
        self.assertNotIn(
            "no files changed", printed,
            "reporting the graph up to date while re-deriving it in full "
            "would be the same false claim in a new place",
        )

    def test_explicit_incremental_is_downgraded_too(self) -> None:
        """``--incremental`` asks for a mode, not for a wrong answer.

        The missing-graph and corrupt-graph downgrades already override an
        explicit request, because a basis that does not exist cannot be used.
        A basis that exists but describes a different graph is the same
        problem, so it resolves the same way.
        """
        root = self._desynced_root()

        graph = _discover_single_repo(root, incremental=True, write_graph=True)

        self.assertEqual(
            _exports(graph), ["replacement"],
            "an explicit --incremental must still refuse a basis the state "
            "cannot vouch for",
        )

    def test_a_state_predating_the_flag_reads_unproven(self) -> None:
        """The ADR 0101 migration case, corrected.

        ADR 0101 kept ``STATE_VERSION`` unbumped so a state written before the
        field existed would buy one *incremental* refresh, on the reasoning
        that the incremental pass runs the per-file repair. It does -- but the
        per-file repair only finds files with no nodes, so a legacy state
        hiding an in-place content drift was repaired by nothing. Such a state
        now buys one full pass instead.
        """
        root = self._desynced_root()
        path = root / ".weld" / "discovery-state.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw.pop("published_graph", None)
        raw["graph_published"] = True
        path.write_text(json.dumps(raw), encoding="utf-8")

        graph = _discover_single_repo(root, incremental=None, write_graph=True)

        self.assertEqual(
            _exports(graph), ["replacement"],
            "a state that cannot name the graph it published proves nothing "
            "about the graph on disk and must not be trusted as a basis -- "
            "including when an older weld's boolean claims otherwise",
        )

    def test_a_proven_inventory_keeps_the_no_change_fast_path(self) -> None:
        """Inverse: the steady state must not pay for this.

        Every canonical writer -- auto-refresh, the ``wd discover`` tail, ``wd
        warm`` -- leaves the state proven, so the ordinary no-change read must
        still invoke no strategy at all.
        """
        with tempfile.TemporaryDirectory(prefix="proven-state-") as td:
            root = Path(td)
            _build_fixture(root)
            _discover_single_repo(root, incremental=False, write_graph=True)

            self.assertTrue(
                _vouches(load_state(root), root),
                "fixture invariant: a run that wrote the graph vouches for it",
            )

            with mock.patch.object(
                discover_mod, "_run_source", wraps=discover_mod._run_source,
            ) as spy:
                _discover_single_repo(root, incremental=None, write_graph=True)

            self.assertEqual(
                spy.call_count, 0,
                "the no-change fast path regressed: a proven inventory over "
                "an unchanged tree must still skip every strategy",
            )


if __name__ == "__main__":
    unittest.main()
