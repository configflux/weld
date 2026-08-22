"""Whether the ``wd find`` index still accounts for its own surface (bd yw4b).

ADR 0101 gave the *graph* a coverage signal: a file discovery would resolve
at this commit, absent from the graph's inventory, reports ``coverage_stale``
and schedules the refresh that ingests it. The file index got nothing, and it
cannot borrow the graph's answer, because the two describe different file
sets:

* the graph's scope is ``.weld/discover.yaml``'s source globs;
* the index's surface is every repo-visible file
  :func:`weld.file_index._is_indexed_file` accepts -- an extension/name
  allow-list applied to the whole tree.

The surface is the strictly broader set, and every file in the gap is one the
graph is *right* to ignore. So a checkout can add a ``.md`` note, a
``.yaml`` config, a ``.sh`` script -- anything outside the source globs -- and
every graph signal correctly reports fresh while ``wd find`` answers "no
matches" about a file plainly on disk. Measured before the fix, in a warm
checkout with nothing else changed: ``stale: no, source_stale: no`` and
``wd find`` blind to the new file permanently, in Mode A as well as Mode B
(the issue was filed as Mode-B-only; it is not).

A false negative is the worst answer a search can give -- it reads as "no such
file" and sends the user to grep -- which is why this is worth a probe on the
read path at all.

**What states the claim.** ``file-index-state.json``, not the index body. The
companion records a hash per surface file, so its key set is exactly "what the
last index build accounted for", including files it read and legitimately drew
no tokens from (:func:`weld.file_index.build_file_index` omits those from the
index entirely). Diffing against the index body instead would read every
token-less file as a hole and rebuild on every single ``find``, forever.

The companion also already carries the binding half this needs:
``meta.index_sha256`` ties it to the exact ``file-index.json`` it describes, so
a companion restored or copied from elsewhere is rejected rather than believed
(this is the property bd yw4b noted was "already solved here").

**When it declines.** No companion, or one whose binding does not match the
index on disk, yields no claim and therefore no probe. Nothing else on disk can
say what the index accounted for, and inventing that claim is the rebuild-loop
above. The state converges the first time a real discovery pass writes a
companion (:func:`weld._file_index_incremental.reindex_full`), and until then
the graph's own staleness signals still drive ``find``'s refresh -- which is
what already heals the fresh-clone case bd yw4b was filed about.
"""

from __future__ import annotations

import os
from pathlib import Path

from weld._auto_refresh import _env_disabled
from weld.repo_boundary import iter_repo_files

__all__ = ["ensure_index_covers_surface", "index_uncovered_files", "surface_paths"]


def surface_paths(root: Path) -> set[str]:
    """Repo-relative paths of every file in the ``wd find`` surface.

    Names only: the probe asks which files are *accounted for*, never what
    they contain, so it costs the boundary's file list plus an allow-list
    check per name and reads no file.
    :func:`weld._file_index_incremental._surface_hashes` walks the same two
    rules and hashes each hit, which is the refresh path's price, not this
    one's.
    """
    from weld.file_index import _is_indexed_file

    root = Path(root).resolve()
    return {
        str(path.relative_to(root))
        for path in iter_repo_files(root)
        if _is_indexed_file(path)
    }


def index_uncovered_files(root: Path) -> set[str]:
    """Surface files the index's companion does not account for.

    Empty when the index is complete *and* when nothing can vouch for it --
    the two are deliberately not distinguished here, because both mean "this
    probe has no rebuild to schedule". See the module docstring for why an
    unvouched companion is not treated as total non-coverage.

    The companion's format, version checks and integrity binding are read
    through :mod:`weld._file_index_incremental` rather than re-parsed here:
    a second reader of that file is exactly how the two would drift on what
    a valid companion is.
    """
    from weld._file_index_incremental import _index_sha256, _load_state_hashes

    loaded = _load_state_hashes(Path(root))
    if loaded is None:
        return set()
    claimed, recorded_index_sha = loaded
    live_index_sha = _index_sha256(Path(root))
    if live_index_sha is None or live_index_sha != recorded_index_sha:
        return set()
    return surface_paths(root) - set(claimed)


def ensure_index_covers_surface(
    root: Path, *, no_refresh: bool = False,
) -> int | None:
    """Rebuild the file index when its surface has outgrown it.

    Returns the number of previously unaccounted-for files when a refresh
    ran, else ``None``. Call **before** loading the index to answer a query,
    so the answer comes from the repaired one.

    Declines under the ADR 0051 freeze in either spelling, and does so
    without probing: the freeze means this read performs no work, and the
    probe is work even though it writes nothing. That matches
    :func:`weld._worktree_seed.ensure_seeded` gate 1, which declines on the
    same two spellings and equally silently.

    Degrades to a no-op rather than raising -- like every other repair on the
    read path, a search must not fail because a self-heal could not run.
    """
    if no_refresh or _env_disabled(os.environ):
        return None
    try:
        uncovered = index_uncovered_files(root)
        if not uncovered:
            return None
        _rebuild(Path(root))
    except Exception:  # noqa: BLE001 -- best-effort repair; never fail a read.
        # Broad on purpose, and not merely defensive: the probe shells out to
        # git for the boundary's file list, so the failures available here are
        # not all OSError (a subprocess timeout is not). Letting one escape
        # would crash ``wd find`` outright -- strictly worse than the missing
        # file this exists to fix. Mirrors the same swallow in
        # :func:`weld._file_index_incremental.refresh_file_index`.
        return None
    return len(uncovered)


def _rebuild(root: Path) -> None:
    """Bring ``file-index.json`` and its companion back in step.

    :func:`weld._file_index_incremental.refresh_file_index` is the cheap path
    and rewrites the companion itself; it returns ``None`` when it declines
    (no usable companion, broken binding, empty surface), and the full
    rebuild is then the only way to close the hole.
    :func:`weld._file_index_incremental.reindex_full` is used for it rather
    than ``build_file_index`` + ``save_file_index`` because only it reseeds
    the companion -- and a rebuild that left the companion behind would
    re-report the same hole on the next read.
    """
    from weld._file_index_incremental import refresh_file_index, reindex_full

    if refresh_file_index(root) is None:
        reindex_full(root)
