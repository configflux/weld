"""What a tracked graph can prove about its own coverage (ADR 0101, 4th).

A Mode B checkout (``wd init --track-graphs``) commits ``.weld/graph.json``
and gitignores ``.weld/discovery-state.json``, so a fresh clone holds a
complete graph and no inventory of what that graph read. ADR 0101 section 5
reads a missing inventory as "first run, nothing to be stale against" and
answers ``coverage_stale: false`` -- true for a root with no graph, and
exactly wrong here. The other two ADR 0017 signals cannot cover for it: both
are scoped to ``meta.discovered_from``, which lists what the graph *did*
read, so a file it never read is absent from them by construction. A new
in-scope file was therefore invisible to every freshness signal in Mode B,
permanently (bd r7d7).

This module closes that by writing the inventory the checkout did not
receive, from the only evidence it holds: **the graph's own anchors**. The
record states coverage and nothing else --

* no content-hash claim. ``files`` maps every anchor to
  :data:`UNPROVEN`, because what the graph read is not what is on disk now
  and nothing here can recover the difference.
* no config or strategy-code claim, for the same reason.

so :func:`weld._discover_basis.incremental_basis_valid` refuses it as a delta
basis (``config_fingerprint`` is ``None``) and the refresh it triggers is a
full one. The sentinel hash makes that fail-safe rather than merely unreached:
every file diffs dirty even if some future caller skipped the config check.

Anchors are a **lower** bound on coverage. A file a strategy read and
legitimately declined -- a blank ``__init__.py``, a ``BUILD.bazel`` no rule
anchors -- has no node, so it reads as uncovered and costs the clone one full
discovery, which writes the real inventory and converges permanently. That
direction is chosen deliberately: over-reporting costs one pass, while
under-reporting is a confident wrong answer that never heals. The cost
disappears once the tracked artifact ships its own inventory (bd az06.2), and
nothing here forecloses that -- synthesis only ever writes where no inventory
exists.

The two shapes that look cheaper are both unsound, not merely coarser:

* reconstructing the declined set from the tree at the graph's basis commit
  claims every in-scope file that existed there was read. The likeliest Mode
  B workflow breaks it -- add a file, forget to re-discover, commit both --
  and the new file is then recorded as "legitimately empty", which is this
  bug with a different spelling.
* using ``meta.discovered_from`` as the covered set fails the same way:
  :func:`weld.strategies._provenance.directory_provenance` records package
  *directories*, so ``weld/`` claims a brand-new ``weld/foo.py`` as read.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from weld._discover_state_check import published_graph_token
from weld._graph_anchors import graph_files_with_nodes
from weld.discovery_state import STATE_FILENAME, DiscoveryState

__all__ = ["UNPROVEN", "synthesize_coverage_inventory"]

#: Stand-in for a claim this record cannot make. Written where a real state
#: carries a ``sha256:``-prefixed content hash or fingerprint, so it can never
#: compare equal to one: every file diffs dirty, and every fingerprint check
#: fails closed.
UNPROVEN = "unproven"


def synthesize_coverage_inventory(root: Path, graph_path: Path) -> int | None:
    """Write *root*'s coverage-only inventory for its tracked graph.

    Returns the number of files claimed, or ``None`` when nothing was
    written: an inventory already exists (borrowed, tracked, or local -- all
    of them outrank a derived one), the graph cannot be read, it anchors
    nothing, it will not hold still, or the create loses a race.

    The caller has already established that *root* is a Mode B checkout whose
    ``.weld/`` is missing state (:mod:`weld._worktree_seed` gate 4), so the
    graph parse here runs once per checkout and never on a warm root -- which
    is what keeps it clear of the read-path budget ADR 0101 section 4 and bd
    aqqa defend.

    The graph is stat-ed, parsed, then tokened, and the token's own stat pair
    must match the first. Recording an inventory of one body under a token
    naming another is precisely the divergence the third amendment closed, and
    :func:`weld._discover_state_check.mark_state_published` guards its own
    claim the same way -- but a second digest is not needed to do it, because
    :func:`published_graph_token` already brackets its hash with a paired
    stat. Two stats around the parse extend that proof across the parse for
    the price of a ``stat``. Without a token the record would vouch for
    nothing, which reads as permanent doubt, so no token means no record.

    The claimed paths are graph vocabulary (:mod:`weld._rel_path`; identity on
    POSIX) while ``files_missing_from_inventory`` weighs them against the
    index spelling from ``git ls-files``. Off POSIX the two can disagree, and the
    disagreement can only leave an anchored file looking uncovered -- one
    refresh, which writes the real inventory in the index spelling and
    settles it.
    """
    state_path = root / ".weld" / STATE_FILENAME
    if state_path.exists():
        return None
    before = _stat_pair(graph_path)
    anchors = _graph_anchors(graph_path)
    if not anchors:
        return None
    token = published_graph_token(graph_path)
    if token is None or before is None:
        return None
    if (token["size"], token["mtime_ns"]) != before:
        return None
    record = DiscoveryState(
        files={rel: UNPROVEN for rel in sorted(anchors)},
        published_graph=token,
        config_fingerprint=None,
        strategy_fingerprint=UNPROVEN,
    )
    text = json.dumps(record.to_dict(), indent=2, ensure_ascii=False) + "\n"
    if not _create_only(state_path, text):
        return None
    return len(anchors)


def _stat_pair(path: Path) -> tuple[int, int] | None:
    """``(size, mtime_ns)`` for *path*, or ``None`` when it cannot be stat-ed.

    The identity pair the rest of this area already trusts for "is this still
    the same body" (:func:`weld._worktree_seed_copy.stat_snapshot`,
    :func:`weld._graph_meta_sidecar.read_staleness_meta`): weld writes
    ``graph.json`` by atomic rename, so any rewrite lands a fresh inode and
    therefore a fresh ``mtime_ns``.
    """
    try:
        info = path.stat()
    except OSError:
        return None
    return info.st_size, info.st_mtime_ns


def _graph_anchors(graph_path: Path) -> set[str]:
    """Repo-relative files the graph at *graph_path* anchors a node at.

    Empty for anything that will not parse as a graph object. A corrupt graph
    gets no coverage claim for the same reason ADR 0096 gate 4 gives it no
    basis: recording one would only mask it from the refresh that replaces it.
    """
    try:
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return set()
    if not isinstance(graph, dict):
        return set()
    return graph_files_with_nodes(graph)


def _create_only(path: Path, text: str) -> bool:
    """Create *path* with *text*; ``False`` if it exists or cannot be written.

    Deliberately **not** :func:`weld.discovery_state.save_state`, whose
    tmp-and-rename would replace whatever is there. The check above is a
    ``stat``, and a ``wd discover`` finishing in the window between it and the
    write would have its real inventory overwritten by this derived one --
    losing both the declined set and the incremental basis. ``O_EXCL`` makes
    losing that race a no-op instead. A torn write is unlinked here and, if
    even that fails, read back as corrupt by
    :func:`weld.discovery_state.load_state`, which falls back to full
    discovery.

    Not going through ``save_state`` also keeps its auto-stamp out: a
    ``strategy_fingerprint`` taken from the *local* strategy code, which would
    claim this graph was built by code that never saw it.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except OSError:
        return False
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
    except OSError:
        try:
            path.unlink()
        except OSError:
            pass
        return False
    return True
