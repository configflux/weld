"""Incremental discovered_from == full discovered_from (bd 8084).

The sibling ``incremental_refresh_equivalence_test`` and
``incremental_cross_source_equivalence_test`` both strip
``meta.discovered_from`` out of their structural comparison, on the theory
that it "legitimately differs" between the two paths. It did not legitimately
differ -- the incremental path re-derived a substitute for
``discovered_from`` from ``source_file_map`` (the glob-resolved file list)
filtered to the dirty set, instead of collecting what each re-run source
actually reported. That re-derivation cannot represent two shapes at all:

* a footprint-less source with no ``glob``/``path``/``files`` key (bd um00's
  command-only ``external_json`` adapter), whose whole ``discovered_from``
  lives outside ``source_file_map`` (empty for such an entry, structurally,
  forever);
* a directory-anchored provenance entry
  (``weld.strategies._helpers.directory_provenance``, used by
  ``python_package``), whose marker (e.g. ``"extra/"``) is never a member of
  any file list in ``source_file_map`` either.

Both classes could previously only ever enter ``discovered_from`` via a full
run -- and a directory marker already present in the PRIOR graph survives an
incremental run "by accident" (nothing ever removes it, since it is not a
literal file path that content-hash diffing can mark deleted). That inertia
hides the bug for an *existing* package, so the fixture below adds a
brand-new package directory only on the incremental round: its marker was
never in the previous graph, so it can only appear if the incremental path
actually collects what ``python_package`` just reported.

The "by accident" survival above was itself hiding a second gap (bd 0t5p):
that inertia never *ends* either. When a whole package directory is deleted,
``old_df``'s ``p not in state_diff.deleted`` filter cannot remove the
directory marker -- ``state_diff.deleted`` holds only literal file paths --
so the marker lingered forever on the incremental path. See
``test_incremental_drops_marker_for_a_fully_deleted_package`` below.
"""

from __future__ import annotations

import shutil
import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

from weld.discover import _discover_single_repo  # noqa: E402

MARKER_SCRIPT = textwrap.dedent("""\
    #!/usr/bin/env python3
    import json, sys
    json.dump({
        "nodes": {"tool:marker": {"type": "tool", "label": "Marker",
                                    "props": {"source_strategy": "external_json"}}},
        "edges": [], "discovered_from": ["manifest.txt"]
    }, sys.stdout)
""")


def _git(root: Path) -> None:
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@t"],
        ["git", "config", "user.name", "T"],
        ["git", "config", "commit.gpgsign", "false"],
    ):
        subprocess.run(cmd, cwd=str(root), check=True)


def _commit(root: Path) -> None:
    subprocess.run(["git", "add", "-A"], cwd=str(root), check=True)
    subprocess.run(["git", "commit", "-qm", "x"], cwd=str(root), check=True)


def _write_fixture(root: Path) -> None:
    """A tree combining all three ``discovered_from`` shapes bd 8084 covers.

    ``src/`` is a real Python package that ``python_module`` AND
    ``python_package`` both glob (via a repo-wide ``**/*.py``, so a package
    added later under a different directory is picked up without a config
    change). ``adapter.py`` is a footprint-less ``external_json`` command --
    its ``discovered_from`` (``"manifest.txt"``) has no glob/path/files key
    to be represented by at all, so it can only ever change on a full run;
    an incremental run must carry it forward unchanged.
    """
    src = root / "src"
    src.mkdir(exist_ok=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "alpha.py").write_text("def alpha_fn():\n    return 1\n", encoding="utf-8")

    (root / "manifest.txt").write_text("marker\n", encoding="utf-8")
    adapter = root / "adapter.py"
    adapter.write_text(MARKER_SCRIPT, encoding="utf-8")
    adapter.chmod(adapter.stat().st_mode | stat.S_IEXEC)

    weld_dir = root / ".weld"
    weld_dir.mkdir(exist_ok=True)
    (weld_dir / "discover.yaml").write_text(
        "sources:\n"
        '  - strategy: python_module\n    glob: "**/*.py"\n    type: file\n'
        '  - strategy: python_package\n    glob: "**/*.py"\n'
        "  - strategy: external_json\n"
        f'    command: "{adapter}"\n',
        encoding="utf-8",
    )


def _add_extra_package(root: Path) -> None:
    """Add a brand-new package directory the baseline full run never saw.

    ``extra/``'s directory-provenance marker cannot be sitting in the prior
    graph's ``discovered_from`` (the prior graph predates this directory),
    so it can only reach the incremental result by the incremental path
    collecting what ``python_package`` reports on the pass that discovers
    it -- never by ``old_df`` carry-forward.
    """
    extra = root / "extra"
    extra.mkdir(exist_ok=True)
    (extra / "__init__.py").write_text("", encoding="utf-8")
    (extra / "gamma.py").write_text("def gamma_fn():\n    return 3\n", encoding="utf-8")


def _delete_package(root: Path) -> None:
    """Remove the pre-existing ``src/`` package directory entirely.

    Every file under it disappears from disk, not just from the glob match
    -- the shape bd 0t5p's marker-lingers-forever bug needs. Deleting only
    the files while leaving an empty ``src/`` directory behind would
    exercise a different, deliberately out-of-scope case: the directory
    itself still exists, so its marker is meant to survive (equivalence to
    a full run is the spec, and a full run never emits a "delete this
    marker" signal for a directory that is still there).
    """
    shutil.rmtree(root / "src")


def _empty_package_directory_in_place(root: Path) -> None:
    """Remove ``src/``'s ``.py`` members but leave the directory itself.

    A non-``.py`` file stays behind so the directory survives on disk with
    zero files any source globs. This is the counterpart scenario
    ``_delete_package`` calls out as deliberately different: the directory
    itself was never deleted, only emptied of matching members, so its
    marker must be kept even though a hypothetical full run over this same
    end state would not re-mint it (``python_package`` skips a directory
    whose glob match is empty -- see its ``extract()``: ``if not matched:
    return ...`` fires before any group, hence any marker, is ever formed).
    Treating "glob temporarily empty" the same as "directory gone" would
    need re-resolving every strategy's glob per marker on every incremental
    run, which is exactly the per-file bookkeeping incremental mode exists
    to avoid -- see ``stale_directory_marker``'s docstring
    (``weld/_discover_inputs.py``) for the full rule.
    """
    (root / "src" / "__init__.py").unlink()
    (root / "src" / "alpha.py").unlink()
    (root / "src" / "keep.txt").write_text("not a source file\n", encoding="utf-8")


def _df(graph: dict) -> set[str]:
    return set(graph.get("meta", {}).get("discovered_from", []))


class DiscoveredFromEquivalenceTest(unittest.TestCase):
    def test_incremental_matches_full_after_one_edit(self) -> None:
        """Baseline sanity: an ordinary content edit to an already-known
        file must not regress the (already-correct pre-fix) glob-based
        ``python_module`` accounting."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _git(root)
            _write_fixture(root)
            _commit(root)
            _discover_single_repo(root, incremental=False, write_graph=True)
            (root / "src" / "alpha.py").write_text(
                "def alpha_fn():\n    return 99\n", encoding="utf-8",
            )
            _commit(root)
            g_inc = _discover_single_repo(root, incremental=True, write_graph=True)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _git(root)
            _write_fixture(root)
            (root / "src" / "alpha.py").write_text(
                "def alpha_fn():\n    return 99\n", encoding="utf-8",
            )
            _commit(root)
            g_full = _discover_single_repo(root, incremental=False, write_graph=True)

        inc_df, full_df = _df(g_inc), _df(g_full)
        self.assertEqual(
            inc_df, full_df,
            f"incremental discovered_from diverged from a full discover at "
            f"the same source state (full-only={sorted(full_df - inc_df)}, "
            f"inc-only={sorted(inc_df - full_df)})",
        )

    def test_incremental_matches_full_after_adding_a_package(self) -> None:
        """The bug-revealing case: a package directory that first exists on
        the incremental round, plus the pre-existing package and the
        footprint-less source's marker, must all be present."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _git(root)
            _write_fixture(root)
            _commit(root)
            _discover_single_repo(root, incremental=False, write_graph=True)
            _add_extra_package(root)
            _commit(root)
            g_inc = _discover_single_repo(root, incremental=True, write_graph=True)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _git(root)
            _write_fixture(root)
            _add_extra_package(root)
            _commit(root)
            g_full = _discover_single_repo(root, incremental=False, write_graph=True)

        inc_df, full_df = _df(g_inc), _df(g_full)
        self.assertEqual(
            inc_df, full_df,
            f"incremental discovered_from diverged from a full discover "
            f"after adding a package (full-only={sorted(full_df - inc_df)}, "
            f"inc-only={sorted(inc_df - full_df)})",
        )
        # Named directly, not just via the set diff: these are exactly the
        # shapes bd 8084 found broken.
        self.assertIn(
            "extra/", inc_df,
            "a brand-new package's directory-anchored provenance did not "
            "survive the incremental pass that first creates it",
        )
        self.assertIn(
            "src/", inc_df,
            "the pre-existing package's directory-anchored provenance did "
            "not survive the incremental pass",
        )
        self.assertIn(
            "manifest.txt", inc_df,
            "the footprint-less external_json source's discovered_from did "
            "not survive the incremental pass (it never re-runs on file "
            "dirt alone -- it must persist via old_df)",
        )

    def test_incremental_drops_marker_for_a_fully_deleted_package(self) -> None:
        """bd 0t5p: a whole package directory removed on the incremental
        round must drop its directory-anchored provenance marker, not
        carry it forward forever.

        ``state_diff.deleted`` only ever holds literal file paths -- the
        keys of ``old_state.files`` -- so it never names the directory
        marker itself (``"src/"``), only the two files that dropped out
        from under it. Before the fix, ``old_df``'s
        ``p not in state_diff.deleted`` filter left ``"src/"`` sitting in
        ``meta.discovered_from`` forever, since that exact string was
        never a key ``state_diff.deleted`` could hold.
        """
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _git(root)
            _write_fixture(root)
            _commit(root)
            g_baseline = _discover_single_repo(root, incremental=False, write_graph=True)
            self.assertIn(
                "src/", _df(g_baseline),
                "fixture setup assumption broken: the baseline full run "
                "must mint a directory-anchored 'src/' marker for the "
                "pre-delete round to actually exercise anything",
            )
            _delete_package(root)
            _commit(root)
            g_inc = _discover_single_repo(root, incremental=True, write_graph=True)
            # Determinism: a second incremental pass over the same
            # (now-unchanged) tree must report the identical set, not
            # merely a first-run coincidence.
            g_inc_again = _discover_single_repo(root, incremental=True, write_graph=True)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _git(root)
            _write_fixture(root)
            _delete_package(root)
            _commit(root)
            g_full = _discover_single_repo(root, incremental=False, write_graph=True)

        inc_df, full_df = _df(g_inc), _df(g_full)
        self.assertEqual(
            inc_df, full_df,
            f"incremental discovered_from diverged from a full discover "
            f"after deleting a whole package (full-only="
            f"{sorted(full_df - inc_df)}, inc-only={sorted(inc_df - full_df)})",
        )
        self.assertNotIn(
            "src/", inc_df,
            "a fully-deleted package's directory-anchored provenance "
            "marker survived an incremental pass instead of leaving the "
            "inventory",
        )
        self.assertEqual(
            inc_df, _df(g_inc_again),
            "discovered_from must be deterministic across repeated "
            "incremental passes over an unchanged tree, not merely "
            "correct on the first",
        )

    def test_incremental_keeps_marker_when_directory_survives_an_empty_glob(
        self,
    ) -> None:
        """The KEEP side of bd 0t5p's rule: a directory that still exists
        keeps its marker even when the owning strategy's glob currently
        matches nothing under it -- this is NOT full-run equivalence (a
        full run over this exact end state mints no marker for ``src/``
        either, per ``_empty_package_directory_in_place``'s docstring), and
        that is intentional. Distinguishing this from an outright deleted
        directory would require re-resolving every strategy's glob per
        marker on every incremental run; ``stale_directory_marker`` checks
        only ``is_dir()``, cheaply, and this test pins that it does not
        overreach into treating an emptied-but-present directory the same
        as a deleted one.
        """
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _git(root)
            _write_fixture(root)
            _commit(root)
            _discover_single_repo(root, incremental=False, write_graph=True)
            _empty_package_directory_in_place(root)
            _commit(root)
            g_inc = _discover_single_repo(root, incremental=True, write_graph=True)

        self.assertIn(
            "src/", _df(g_inc),
            "a directory-anchored marker was dropped for a directory that "
            "still exists on disk, merely because its glob currently "
            "matches nothing under it -- stale_directory_marker must key "
            "on is_dir(), not on the strategy's current match count",
        )

    def test_convergence_across_several_incrementals(self) -> None:
        """Full, then an ordinary edit, then a new-package add, over two
        separate incremental rounds -- the final discovered_from must equal
        a fresh full discover's set at the same end state, not merely a
        superset or a lucky subset."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _git(root)
            _write_fixture(root)
            _commit(root)
            _discover_single_repo(root, incremental=False, write_graph=True)

            (root / "src" / "alpha.py").write_text(
                "def alpha_fn():\n    return 41\n", encoding="utf-8",
            )
            _commit(root)
            _discover_single_repo(root, incremental=True, write_graph=True)

            _add_extra_package(root)
            _commit(root)
            g_inc = _discover_single_repo(root, incremental=True, write_graph=True)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _git(root)
            _write_fixture(root)
            (root / "src" / "alpha.py").write_text(
                "def alpha_fn():\n    return 41\n", encoding="utf-8",
            )
            _add_extra_package(root)
            _commit(root)
            g_full = _discover_single_repo(root, incremental=False, write_graph=True)

        inc_df, full_df = _df(g_inc), _df(g_full)
        self.assertEqual(
            inc_df, full_df,
            f"discovered_from failed to converge across two incremental "
            f"passes (full-only={sorted(full_df - inc_df)}, "
            f"inc-only={sorted(inc_df - full_df)})",
        )


if __name__ == "__main__":
    unittest.main()
