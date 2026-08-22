"""Graph inputs no source glob resolves, and the delta that must see them.

ADR 0008's inventory was built from one set: the files
:func:`weld._source_resolve.resolve_source_file_map` resolves. That set is not
the same as the set discovery *reads*. ADR 0109 resolves ``load()``, so the
bazel strategy reads every ``.bzl`` a ``BUILD`` file loads, evaluates the
constants it exports, and lets them decide a target's real ``srcs``. Those
files reach ``meta.discovered_from`` and get a ``file:`` node of their own --
they are graph inputs by every other measure -- but ``.weld/discover.yaml``
deliberately globs no ``*.bzl`` (one nobody loads declares nothing, so a glob
would mint nodes with no relationships), so the inventory recorded none of
them.

Two subsystems read that inventory, and both went blind in the same place:

* ADR 0017's working-tree dimension (bd 0jay) compares a dirty path's content
  against it. A path it holds no hash for, that is on disk and that no source
  entry resolves, reads as "not a graph input, never stale" -- correct for
  genuine non-inputs, and exactly wrong here. Editing a loaded ``.bzl``
  produced no staleness signal at all, while ``discover.yaml``'s own
  bazel-entry comment still claimed it did (bd a4q8).
* The ADR 0008 delta is the same comparison one layer down, so an incremental
  run over an edited ``.bzl`` reported "no files changed, graph is up to date"
  and left the ``contains`` edge pointing at the previous ``srcs``.

The fix is one invariant, applied at both ends of a run: **the inventory
records every file the graph names in ``meta.discovered_from``**, not just the
glob-resolved subset. :func:`plan_delta` puts the prior run's inputs into the
delta before it is computed; :func:`graph_input_hashes` puts this run's into
the state being written (:func:`weld._discover_sidecar.finalize_single_repo`).
Directory prefixes in the manifest (``weld/``, the shape a root-scanning
strategy records) are not files and are skipped -- recording one would put a
permanently unhashable key in the delta basis.

A changed input no glob resolves forces a **full** run. Nothing maps such a
path back to a source entry, so an incremental pass would re-stamp its hash
without re-running the strategies that read it -- freshness would settle onto a
graph still built from the old content, which is a worse state than the bug:
silent instead of merely absent. Re-running every source is the only scope that
is sound without new per-path bookkeeping, and it converges in one pass. The
same reasoning does not reach a *deleted* input: "recorded, absent from disk,
resolved by no glob" is indistinguishable from an ordinary deleted source, so
deletions keep the incremental handling they already had -- the path leaves the
inventory, freshness settles, and the next full run re-reads its loaders.

``discovered_from`` is graph-authored data, so it is treated as untrusted here:
every candidate must resolve to a regular file under *root*. Containment for
``load()`` labels is already enforced where they are resolved
(``weld_bazel_loads_containment_test``); this is the read-side half, so a graph
carrying an absolute path, a ``..`` escape, or a symlink out of the tree cannot
make discovery hash a file outside the repository.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from weld.discovery_state import (
    DiscoveryState,
    StateDiff,
    build_file_hashes,
    diff_state,
)


def _within_root(root: Path, rel: str) -> bool:
    """True when ``root / rel`` is a regular file that stays under *root*.

    ``resolve()`` on both sides, so a symlink pointing out of the tree is
    refused as well as a lexical ``..`` escape -- the check is about where the
    bytes actually live, not how the path is spelled.
    """
    try:
        resolved = (root / rel).resolve()
        resolved.relative_to(root.resolve())
        return resolved.is_file()
    except (OSError, ValueError):
        return False


def stale_directory_marker(root: Path, entry: str) -> bool:
    """True when *entry* is a directory-provenance marker for a directory
    gone from disk (bd 0t5p).

    :func:`weld.strategies._provenance.directory_provenance` is the sole
    producer of a directory-shaped ``discovered_from`` entry, and it always
    trails the path with ``"/"`` -- the repo-root case degenerates to member
    files instead of a bare ``"./"``, so every directory marker in practice
    has this shape. A file path never does, so the suffix check alone tells
    markers apart from files with no directory lookup on the (overwhelmingly
    common) file-entry path.

    Kept, not dropped, when the directory still exists: a strategy's glob
    can legitimately match nothing under a live directory for one commit
    (files renamed away and back, a multi-file edit landing out of order),
    and treating that the same as the directory's own removal would need
    re-resolving every strategy's glob per marker on every incremental run
    -- exactly the per-file bookkeeping incremental mode exists to avoid. A
    live directory's marker is an over-report at worst, the same harmless
    imprecision ADR 0017's prefix-match staleness check already tolerates
    for it (bd 0t5p's own diagnosis); only the
    directory's own disappearance is unambiguous and cheap enough to act on.

    ``discovered_from`` is graph-authored data, so it is treated as
    untrusted here like :func:`_within_root`: an entry that does not safely
    resolve under *root* (a ``..`` escape, a symlink out of the tree) counts
    as stale and is dropped rather than stat'd.
    """
    if not entry.endswith("/"):
        return False
    try:
        resolved = (root / entry).resolve()
        resolved.relative_to(root.resolve())
    except (OSError, ValueError):
        return True
    return not resolved.is_dir()


def graph_inputs(root: Path, graph: dict | None, recorded: Iterable[str]) -> list[str]:
    """Files *graph* names as inputs that *recorded* does not already hold.

    Order follows ``meta.discovered_from`` and duplicates are dropped, so two
    runs over an unchanged tree produce the same list (ADR 0012 §3).
    """
    if not isinstance(graph, dict):
        return []
    meta = graph.get("meta")
    manifest = meta.get("discovered_from") if isinstance(meta, dict) else None
    if not isinstance(manifest, list):
        return []
    held = set(recorded)
    out: list[str] = []
    for entry in manifest:
        if not isinstance(entry, str) or entry in held:
            continue
        held.add(entry)
        if _within_root(root, entry):
            out.append(entry)
    return out


def graph_input_hashes(
    root: Path, graph: dict | None, recorded: dict[str, str],
) -> dict[str, str]:
    """*recorded*, extended to every file *graph* names as an input.

    Returns *recorded* unchanged when there is nothing to add -- a repo whose
    every input is glob-resolved allocates nothing -- and a merge is re-keyed
    in path order. ``discovery-state.json`` is written in insertion order and
    every inventory before this was sorted by construction, since
    ``current_file_set`` is a sorted list (which is also why the untouched
    return is already in that order). Appending the inputs instead would key
    the same content two ways depending on which path merged them -- a full
    run merges here, an incremental one before the delta -- and ADR 0110
    tracks this file, so that reads as a diff nobody made.
    """
    extra = graph_inputs(root, graph, recorded)
    if not extra:
        return recorded
    merged = dict(recorded)
    merged.update(build_file_hashes(root, extra))
    return {rel: merged[rel] for rel in sorted(merged)}


def plan_delta(
    root: Path,
    existing_graph: dict | None,
    current_file_set: list[str],
    old_state: DiscoveryState | None,
    incremental: bool,
) -> tuple[dict[str, str], StateDiff, bool]:
    """Hash this run's inputs, diff them, and re-decide *incremental*.

    Returns ``(current_hashes, state_diff, incremental)``.

    A full run needs neither half of the extension: its diff is unused, and
    the inputs it should record are the ones it is about to read, which
    ``finalize_single_repo`` takes from the graph it produces. Carrying the
    *prior* run's inputs into a full run's state would record a file this run
    may no longer read.

    An incremental run needs both. The prior graph's inputs are hashed into
    the current set before the diff, or every one of them would read as a
    deletion on every run -- which would also cost the no-change fast path,
    since a deletion is a change. With them present, an unchanged tree matches
    exactly and a changed input shows up as ``modified``. Whether the delta
    can still be applied incrementally is then decided by whether any dirty
    path is claimed by a source entry; see the module docstring for why an
    unclaimed one cannot be.
    """
    current_hashes = build_file_hashes(root, current_file_set)
    if not incremental:
        return current_hashes, StateDiff(added=set(current_hashes)), False
    current_hashes = graph_input_hashes(root, existing_graph, current_hashes)
    state_diff = diff_state(old_state, current_hashes)
    claimed = set(current_file_set)
    return current_hashes, state_diff, not (state_diff.dirty - claimed)


__all__ = [
    "graph_input_hashes", "graph_inputs", "plan_delta", "stale_directory_marker",
]
