"""Does an inventoried file's content still match, even where git is clean?

The three ADR 0017 signals -- committed diff, working-tree dirty-content (bd
0jay), and ADR 0101 coverage -- each need *something* to point at a file
before they will look at it: a commit-range diff entry, a ``git status``
dirty entry, or absence from the inventory. A file that was edited, then
discovered (so ``.weld/discovery-state.json`` now holds the edited content's
hash), then reverted to its committed content clears every one of those
pointers at once: ``git status`` reports it clean again (content matches
HEAD), the commit range is empty (HEAD never moved), and the inventory
already names the file, so coverage sees nothing uncovered. The graph
answers from content that is on no commit and in no working tree, and
nothing re-hashes it to find out (bd lhye).

This module is the fourth signal: it reads the inventory *directly*, for
every file it names, rather than waiting for git's dirty list to name one
first.

The working-tree amendment's design considered persisting a "which paths
were dirty when discovery ran" set and rejected it -- that needs a stamping
site in every graph writer, and a writer that stamps it wrong (``wd add-node
--merge``, which reads no source file) would manufacture a FALSE-fresh worse
than the blind spot being closed. This module adds no persisted field and no
new stamping site: the bound below is the *existing* discovery-state file's
own mtime, and the only code path that ever advances it is the discovery
write tail (:func:`weld._discover_state_check.save_state_for_graph` and the
re-stamp in :func:`weld._discover_state_check.mark_state_published`). Every
graph-mutating CLI path (``add-node``, ``add-edge``, ``rm-node``, ``rm-edge``,
``touch``, ``import``) writes through ``Graph.save()`` alone, which never
imports :mod:`weld.discovery_state` and never touches
``discovery-state.json``. A writer that cannot move the reference point has
nothing to get wrong -- see
``NonDiscoveryWriterPreservesBasisTest.test_add_node_neither_clears_nor_forges_the_basis``
in ``weld_reverted_content_staleness_test.py``, which pins exactly that.

Cost is bounded the same way the working-tree signal bounds itself, just
without a cheap git-native prefilter to lean on: a file's content cannot have
changed since its mtime last moved, so a file whose mtime predates
``discovery-state.json``'s own mtime is trusted on its recorded hash without
being re-read. Measured on this repo (1,523 inventoried files): a ``stat()``
pass over all of them costs ~12.7 ms; a naive unconditional rehash of the
same set costs ~57.7 ms. Both are inside the existing 500 ms
``test_clean_tree_stale_check_is_fast`` budget at this repo's size, but the
stat-first bound is what keeps the cost proportional as an inventory grows,
matching ADR 0008's own "proportional cost" principle. See ADR 0017's fourth
amendment ("the inventory is consulted directly, not only where git still
points") for the full argument and the measured numbers.

The mtime compare is ``>=``, not ``>``: a file touched in the same clock tick
as the state write re-verifies rather than being skipped, which is the safer
direction on a race. It cannot close the window outright -- an explicitly
backdated mtime, or a race inside one tick on a coarse-resolution filesystem,
still reads fresh. Accepted and documented: the same one-directional bias
(under-report rather than risk a new over-report class) ADR 0101 already
takes for scope matching, and the ordinary revert this module exists for --
a real write, followed later by a real ``git checkout``/``git restore`` --
clears it correctly.

Every existing reader of ``state.files`` uses its keys only for a dict lookup
or a set-membership test (:mod:`weld._staleness_worktree`,
:mod:`weld._staleness_coverage`); this is the first that joins a key onto
*root* to build a filesystem path and read it. ``discovery-state.json`` is
gitignored and written only by the validated pipeline
(:func:`weld._discover_inputs.graph_input_hashes`, which resolves every
candidate under *root* before it is ever recorded -- bd a4q8), so under
ordinary operation every key is already safe. This module still re-checks
containment on the read side before touching disk, the same defensive
posture bd a4q8 applied to ``meta.discovered_from`` for the identical
reason: a hand-edited or corrupted state file must not turn a stat/hash loop
into an absolute-path or ``..``-escape read of a file outside the
repository. Deliberately *not* a reuse of
:func:`weld._discover_inputs._within_root`: that helper also requires
``is_file()``, which is right for deciding whether a candidate should ever
enter the inventory, but wrong here -- it would silently exclude a
legitimately *vanished* inventoried file from ever being examined, which is
exactly the case :data:`weld._stale_reasons.INGESTED_FILE_VANISHED` exists
to report. Containment and existence are asked separately below.
"""

from __future__ import annotations

from pathlib import Path


def _resolves_under_root(root: Path, rel: str) -> bool:
    """True when *rel* resolves to a path that stays under *root*.

    Containment only -- unlike :func:`weld._discover_inputs._within_root`,
    this does not require the path to currently exist, so a legitimately
    vanished inventoried file still passes and reaches the existence check
    below. Refuses an absolute *rel* (``root / rel`` degenerates to the
    absolute path outright), a lexical ``..`` escape, and a symlink resolving
    outside *root*.
    """
    try:
        (root / rel).resolve().relative_to(root.resolve())
        return not Path(rel).is_absolute()
    except (OSError, ValueError):
        return False


def reverted_content_stale(root: Path) -> list[dict]:
    """Inventoried files whose current content disagrees with ``state.files``.

    Returns ``[{"path": <repo-relative str>, "reason": <CONTENT_DIFFERS |
    INGESTED_FILE_VANISHED>}, ...]``, sorted by path. Empty when there is no
    inventory to compare against (a first run has nothing to be stale
    against) -- the same undecidable-input convention
    :func:`weld._staleness_worktree.dirty_sources_diverge_detail` and
    :func:`weld._staleness_coverage.coverage_stale_detail` already use.

    Meant to run only after the other three ADR 0017 / 0101 signals have
    already cleared (see :func:`weld._staleness.compute_stale_info`): it pays
    a stat call per inventoried file unconditionally, so gating it behind the
    cheaper signals keeps a genuinely-stale read from paying it too.
    """
    from weld._stale_reasons import CONTENT_DIFFERS, INGESTED_FILE_VANISHED
    from weld.discovery_state import STATE_FILENAME, compute_hash, load_state

    state = load_state(root)
    if state is None or not state.files:
        return []

    state_path = root / ".weld" / STATE_FILENAME
    try:
        basis_mtime_ns = state_path.stat().st_mtime_ns
    except OSError:
        # The state we just loaded came from this exact path; a stat
        # failure here means it vanished between the two calls (a racing
        # discover, most likely). No stable basis to bound against --
        # report nothing rather than guess a race's outcome.
        return []

    out: list[dict] = []
    for rel, recorded in state.files.items():
        if not _resolves_under_root(root, rel):
            # A malformed or tampered key. Under ordinary operation this
            # never fires -- see the module docstring -- so silently
            # skipping it costs nothing real; the alternative is stat-ing
            # and hashing whatever path a corrupted state file names.
            continue
        path = root / rel
        try:
            touched_since = path.stat().st_mtime_ns >= basis_mtime_ns
        except OSError:
            # Can no longer be read at all: deleted, or replaced by
            # something unreadable. An untracked file that was ingested and
            # then deleted without ever being staged leaves no trace in
            # `git status` output -- git never knew about it to report the
            # removal -- so `dirty_sources_diverge` never reaches it either.
            # This signal does not wait for git to point first, so it still
            # catches it.
            out.append({"path": rel, "reason": INGESTED_FILE_VANISHED})
            continue
        if not touched_since:
            # Content cannot have changed since the mtime last moved, and
            # that predates the last confirmed discovery -- trust the
            # recorded hash without re-reading the file.
            continue
        try:
            if compute_hash(path) != recorded:
                out.append({"path": rel, "reason": CONTENT_DIFFERS})
        except OSError:
            out.append({"path": rel, "reason": INGESTED_FILE_VANISHED})

    return sorted(out, key=lambda entry: entry["path"])
