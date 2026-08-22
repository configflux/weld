"""The canonical repo-relative path form, and the one place it is applied.

Two path *vocabularies* meet inside incremental discovery, and neither is
authoritative over the other's spelling:

* the **index** vocabulary -- built by weld from the filesystem
  (:mod:`weld._source_resolve` -> ``DiscoveryState.files`` -> the dirty and
  stale sets). Built through :func:`rel_to_root`, so POSIX (bd v552; it was
  ``str(p.relative_to(root))``, hence OS-native, until then).
* the **graph** vocabulary -- built by the strategies into ``props.file``,
  ``props.declared_in`` and ``props.provenance.file``. Mixed by construction:
  some strategies write ``as_posix()``, some write ``str()``.

On POSIX the two spellings are byte-identical, so every comparison between
them works and every test is green -- the divergence is invisible. Off POSIX
``str()`` yields ``lib\\thing.py`` where ``as_posix()`` yields ``lib/thing.py``
and the comparisons silently stop matching. For ADR 0074's incremental purge
that is not a cosmetic miss: a stale node survives its own file's edit, and an
edge that *does* carry provenance takes the "attributable" branch, finds its
producing file absent from the stale set, and is retained unconditionally --
strictly worse than the unattributed floor it replaced, and exactly the silent
staleness the ADR's second amendment rejected the widened floor to avoid
(bd pbi8).

Normalising the strategies instead cannot make this total: project-local
``.weld/strategies/`` overrides are third-party code weld does not own, and
they write ``props.file`` too. So the fold belongs at a weld-owned boundary,
and this module is it. Callers are the index<->graph comparison sites, and
they are a closed set -- keep this list whole, because a site that skips the
fold is invisible on every platform anyone runs the tests on:

1. :func:`weld.discovery_state.purge_stale_nodes` -- ADR 0074 tier 1.
2. :func:`weld._incremental_purge.purge_edges_by_provenance` -- tier 2.
3. :func:`weld._graph_anchors.files_missing_strategy_outputs`.
4. :func:`weld._graph_anchors.files_missing_from_graph`.
5. :func:`weld._graph_anchors.compute_files_with_no_nodes`.
6. :func:`weld._graph_anchors.files_with_no_nodes_and_failed`, whose
   ``strategy_failed`` bounding reads the same anchors (factored out of
   :func:`weld._discover_state_check.save_state_for_graph`, bd um00).

Site 3 and 4 misfire as a perpetual re-run (an anchored file reads as
unanchored, so its source is forced dirty every pass); 5 and 6 misfire as a
wrong *permanent* exemption or a standing failure record, which is silent
staleness of the same kind as 1 and 2.

**The fold is applied at the comparison, never to the sets that flow onward.**
The dirty and stale sets keep whatever spelling the index gives them, because
``dirty`` is handed to the strategies as ``IncrementalHint.dirty_files`` and
matched there against a path the strategy re-derives itself. That coupling is
why bd pbi8 folded rather than rewriting the index: POSIX-ifying the index
alone fixes the purge and breaks dirty scoping on the same platform, since
``python_callgraph`` would then match nothing and parse nothing.

bd v552 moved both ends together instead -- :mod:`weld._source_resolve` and
:func:`weld.strategies._incremental_hint.dirty_matched` now build their paths
through :func:`rel_to_root`, so the index vocabulary is POSIX end to end and
the two sides of that match still agree. The index side of the six folds
below is consequently already canonical, which makes this module
belt-and-braces at the index<->graph line rather than load-bearing. It stays:
the *graph* side is written by ~40 strategies plus project-local
``.weld/strategies/`` overrides that weld does not own, and those still spell
``props.file`` however they like.

**The fold is platform-aware, not an unconditional backslash replace.**
Conflating ``a\\b.py`` with ``a/b.py`` in a purge means one file's edit
silently drops the other's nodes, and nothing re-mints them because the other
file is not dirty. Folding only separators that are *actually* separators on
the running platform makes this the exact identity on POSIX, which is also why
landing it changed no byte of behavior on any platform weld currently
supports.

The read-side query and lint paths (:mod:`weld.impact_surfaces`,
:mod:`weld._graph_strategy_pair`) both used to hand-roll the unconditional
form and document taking that misread, on the grounds that a search result or
a lint finding is worth the simplicity. Since bd 244j the stored artifact is
canonicalized where it is written, so on POSIX there is nothing left for an
unconditional replace to repair -- the misread is all it still does.

:mod:`weld.impact_surfaces` therefore calls :func:`canonical_rel_path` now
(bd 3x85): identity on POSIX, still folding where a foreign spelling can
actually arise. :mod:`weld._graph_strategy_pair` deliberately does not, and
the difference is consequence, not oversight. It feeds a lint that fails the
gate, so losing the tolerance there turns a graph written by a pre-244j weld
off POSIX into a false-positive storm that blocks somebody -- worse than
misreading a pathological ``a\\b.py``. Its docstring records that, and its own
test pins it.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import PurePath

# Separators this platform honours that are not already the canonical "/".
# POSIX: ``os.sep == "/"`` and ``os.altsep is None`` -> empty, and every
# function below is the identity. Windows: ``("\\",)``. Tests simulate a
# non-POSIX platform by patching this tuple, which is the whole reason it is a
# module constant rather than an inline ``os.sep`` read -- CI has no Windows
# runner, so the platform is simulated, never actually exercised.
_FOREIGN_SEPARATORS: tuple[str, ...] = tuple(
    dict.fromkeys(s for s in (os.sep, os.altsep) if s and s != "/")
)


def needs_folding() -> bool:
    """True when this platform spells a separator the canonical form does not.

    ``False`` on POSIX, where every function in this module is the identity.
    Callers that would otherwise walk a whole structure to fold nothing use
    this to skip the walk entirely (:mod:`weld._discover_postprocess`), which
    is what keeps the stored graph byte-identical -- and free -- there.

    A function rather than a direct read of the module constant, so a caller
    is not reaching into a private *and* so the simulated-platform tests
    (which patch that constant) still reach every caller.
    """
    return bool(_FOREIGN_SEPARATORS)


def rel_to_root(path: PurePath, root: PurePath) -> str:
    """Return *path* relative to *root* in the canonical (POSIX) form.

    The construction counterpart to :func:`canonical_rel_path`: this is how a
    repo-relative path enters the index vocabulary, that is how a foreign
    spelling is folded once it is already a string. Both live here so the
    canonical form has one definition rather than a construction rule in one
    module and a folding rule in another (bd v552).

    ``str(path.relative_to(root))`` -- what every caller here used to write --
    is separator-native, so the same file is ``lib/thing.py`` to
    :mod:`weld.glob_match` and ``lib\\thing.py`` to the index off POSIX. The
    exact identity on POSIX, where the two already agreed.

    Raises ``ValueError`` when *path* is not under *root*, exactly as
    ``PurePath.relative_to`` does; callers that treat that as "skip this file"
    catch it themselves.
    """
    return path.relative_to(root).as_posix()


def canonical_rel_path(value: object) -> str:
    """Return *value* as a canonical (POSIX-separated) repo-relative path.

    Non-string input returns ``""``: ``props.file`` is contract-typed as a
    string (:mod:`weld._contract_validators`), and a value that is not one is
    not a path, so it anchors nothing rather than comparing equal to something.

    Idempotent, and the exact identity on POSIX.
    """
    if not isinstance(value, str):
        return ""
    for sep in _FOREIGN_SEPARATORS:
        value = value.replace(sep, "/")
    return value


def canonical_rel_paths(values: Iterable[str]) -> set[str]:
    """Canonicalize a collection of repo-relative paths into a lookup set.

    Use for the side of a comparison that is tested against repeatedly; the
    per-item side stays on :func:`canonical_rel_path`. Distinct inputs can
    collapse to one entry off POSIX (``a/b`` and ``a\\b`` name the same file
    there), which is the intended meaning, not a loss.
    """
    if not _FOREIGN_SEPARATORS:
        return set(values)
    return {canonical_rel_path(v) for v in values}


__all__ = [
    "canonical_rel_path",
    "canonical_rel_paths",
    "needs_folding",
    "rel_to_root",
]
