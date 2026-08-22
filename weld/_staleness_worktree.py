"""Does the dirty working tree hold source the graph never read? (bd 0jay)

The ADR 0017 working-tree dimension exists so an agent mid-edit sees its own
uncommitted changes reflected in freshness. It was implemented as
``bool(working_tree_dirty_sources(...))`` -- and that is a question about
**HEAD**, not about the graph. ``wd discover`` commits nothing, so no
discovery run can change the answer: one uncommitted edit to a tracked source
latched ``source_stale`` True for as long as the edit was held. ``wd discover``
reported success, ``wd stale`` still answered ``source_stale: yes`` with
``graph_sha == current_sha`` and ``commits_behind: 0``, and the stale-gated
commands stayed refused behind an error message prescribing the very fix that
could not work. That is not a tuning problem: the signal had no recorded basis
to settle *against*.

It does have one. ADR 0008's ``.weld/discovery-state.json`` records a SHA-256
per file discovery ingested, and the incremental path already reads it to
conclude "no files changed, graph is up to date" -- the same sentence weld
printed on the run whose freshness check then said stale. This module asks the
inventory instead of asking git, so the two subsystems answer from one basis.

Cost is unchanged where it mattered: ``working_tree_dirty_sources`` stays the
caller's pre-filter, so a clean tree still costs one ``git status`` and hashes
nothing. Only the handful of paths git reports dirty are ever hashed, and the
never-ingested check runs first so an obviously-stale tree short-circuits
before hashing at all.

Reading a path that is *absent* from the inventory is the whole difficulty,
because the inventory is not a scope declaration -- ``meta.discovered_from``
carries directory prefixes as well as files, so "dirty and unrecorded" covers
both a new module the graph is blind to and a file discovery would never read.
Three states, distinguished in this order:

* **not on disk** -- a deletion. Discovery already dropped it from the
  inventory, so it is unrecorded *because* it is gone. Flagging it would
  re-latch the bug one discover later: the deletion stays uncommitted, hence
  dirty, hence re-examined forever. The same reading covers the vacated
  original of a rename, which is why the caller lists the dirty set with
  rename detection off -- see ``detect_renames`` in
  :func:`weld._git.working_tree_dirty_sources`.
* **on disk and in scope** -- a source the graph has not ingested. Stale, and
  it settles on the next discover, which puts it in the inventory.
* **on disk and out of scope** -- not a graph input. Never stale, or dirt
  under a broad ``discovered_from`` of ``./`` (the default ``wd init`` shape)
  would be staleness no discover could clear -- the defect this module exists
  to remove, reintroduced by the back door. This branch is only as true as the
  inventory is complete, which is why discovery records every file it names in
  ``discovered_from`` and not merely the glob-resolved ones (bd a4q8): a
  ``.bzl`` a BUILD file loads is a genuine input that no glob resolves, and
  while it went unrecorded it landed here and never marked anything stale. See
  :mod:`weld._discover_inputs`.

Scope is decided by ADR 0101's :func:`weld._staleness_coverage.in_scope_files`,
the same matcher the coverage probe applies a few lines later in
``compute_stale_info``, so the two cannot drift on what "in scope" means. Its
documented one-directional bias (never over-report) carries over: a pattern it
fails to reproduce reads as out of scope, which under-reports staleness for a
brand-new file rather than making one permanently uncoverable. That is the
same exposure ADR 0101 already accepts for tracked files.

Undecidable inputs stay conservative -- no inventory, an empty one, or no
configured ``sources`` all report divergence, which is exactly the pre-fix
``dirty => stale`` behaviour. A graph whose inventory cannot speak for it at
all is a separate question, and the caller already asks it: ``coverage_stale``
runs immediately after this clears and refuses a non-vouching inventory
(``inventory_vouches_for_graph``), so nothing here needs to re-ask -- but the
two must stay in that order.

Both sides of every comparison here are the *index* path vocabulary (git's
porcelain output and ``DiscoveryState.files``), which bd v552 made POSIX end
to end. The :mod:`weld._rel_path` fold guards the index<->*graph* line and
does not apply.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable


def dirty_sources_diverge(root: Path, dirty: Iterable[str]) -> bool:
    """True when *dirty* holds content the graph's inventory cannot account for.

    *dirty* is the repo-relative path list from
    :func:`weld._git.working_tree_dirty_sources` -- already intersected with
    ``meta.discovered_from`` and already stripped of weld's own bookkeeping.
    Pass it listed with ``detect_renames=False`` so a rename's vacated original
    arrives as an explicit deletion; with rename detection on, only the new
    path is reported and a rename of an ingested file to an out-of-scope name
    reads as "no source input changed".

    Returns True for anything this cannot establish soundly, so a caller that
    treats True as stale degrades to the pre-fix behaviour rather than
    vouching for a graph on no evidence.
    """
    paths = list(dirty)
    if not paths:
        return False

    from weld._staleness_coverage import _load_sources, in_scope_files
    from weld.discovery_state import compute_hash, load_state

    state = load_state(root)
    if state is None or not state.files:
        return True
    sources = _load_sources(root)
    if not sources:
        return True

    recorded_hashes: list[tuple[str, str]] = []
    unrecorded_on_disk: list[str] = []
    for rel in paths:
        recorded = state.files.get(rel)
        if recorded is None:
            if (root / rel).is_file():
                unrecorded_on_disk.append(rel)
            continue
        recorded_hashes.append((rel, recorded))

    # Cheapest discriminator first: a never-ingested source in scope settles
    # the answer without hashing anything.
    if unrecorded_on_disk and in_scope_files(sources, unrecorded_on_disk):
        return True

    for rel, recorded in recorded_hashes:
        try:
            if compute_hash(root / rel) != recorded:
                return True
        except OSError:
            # An ingested source weld can no longer read -- deleted, or
            # replaced by something unreadable. Either way the graph holds
            # content the tree does not.
            return True
    return False


def dirty_sources_diverge_detail(root: Path, dirty: Iterable[str]) -> list[dict]:
    """Enumerate every *dirty* path that diverges, and why.

    The full-enumeration companion to :func:`dirty_sources_diverge`: that
    function stops at the first divergence so the boolean gate stays cheap on
    every read; this one is meant to be called only after it has already
    returned ``True``, so the cost of hashing the rest of the dirty set is
    paid exactly once, on the path that is already about to run a
    multi-second ``wd discover`` -- never on a clean or a settled-dirty tree.

    Mirrors that function's structure and the three-state reading in the
    module docstring above, tagging each entry instead of returning at the
    first match:

    * not on disk -- a deletion; omitted (not a divergence, see module
      docstring).
    * on disk, in scope, never ingested --
      :data:`weld._stale_reasons.NEVER_INGESTED`.
    * on disk, ingested, content differs --
      :data:`weld._stale_reasons.CONTENT_DIFFERS`.
    * ingested, now unreadable -- :data:`weld._stale_reasons.INGESTED_FILE_VANISHED`.

    Undecidable inputs (no inventory, no configured sources) return ``[]``:
    the boolean gate reads them conservatively as divergent (pre-fix
    ``dirty => stale`` behaviour, unchanged here), but there is no
    per-path evidence to name, so this under-reports detail rather than
    inventing a claim it cannot back -- the same choice ADR 0017's
    amendments make for the other bases-less states.
    """
    paths = list(dirty)
    if not paths:
        return []

    from weld._staleness_coverage import _load_sources, in_scope_files
    from weld._stale_reasons import (
        CONTENT_DIFFERS,
        INGESTED_FILE_VANISHED,
        NEVER_INGESTED,
    )
    from weld.discovery_state import compute_hash, load_state

    state = load_state(root)
    if state is None or not state.files:
        return []
    sources = _load_sources(root)
    if not sources:
        return []

    recorded_hashes: list[tuple[str, str]] = []
    unrecorded_on_disk: list[str] = []
    for rel in paths:
        recorded = state.files.get(rel)
        if recorded is None:
            if (root / rel).is_file():
                unrecorded_on_disk.append(rel)
            continue
        recorded_hashes.append((rel, recorded))

    out: list[dict] = []
    if unrecorded_on_disk:
        never_ingested = in_scope_files(sources, unrecorded_on_disk)
        out.extend(
            {"path": rel, "reason": NEVER_INGESTED}
            for rel in unrecorded_on_disk if rel in never_ingested
        )

    for rel, recorded in recorded_hashes:
        try:
            if compute_hash(root / rel) != recorded:
                out.append({"path": rel, "reason": CONTENT_DIFFERS})
        except OSError:
            out.append({"path": rel, "reason": INGESTED_FILE_VANISHED})
    return out
