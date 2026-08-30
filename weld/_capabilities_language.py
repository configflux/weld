"""Per-language attribution for the runtime capability matrix (Finding 03).

Split from :mod:`weld.capabilities` (which sits against the 400-line cap) so
the language-attribution rules have room to be correct in both directions:

- **Multi-language strategies** (``test_peer``): a strategy that declares
  several languages under one flat extension set must only flip a language's
  row when the graph holds a file *of that language*. Otherwise a Python-only
  repo reports ``csharp/go/java/rust/typescript`` as present because a single
  ``.py`` hit flips every declared language. Mirrors how
  :data:`MULTI_FRAMEWORK_FILES` already partitions multi-framework strategies.

- **``tree_sitter``**: declares no languages of its own -- its language is
  wired per source entry in ``discover.yaml`` via the ``language:`` key, which
  the registry never reads. So a C#-only repo whose every node is stamped
  ``source_strategy: tree_sitter`` reports ``csharp`` all-no. This module reads
  those per-entry ``language:`` keys and attributes ``tree_sitter``'s evidence
  (``file``/``symbols``/``imports``/``calls``) to each wired language, gated on
  the graph containing a file of that language.

The compute loop in :mod:`weld.capabilities` calls into the two public
functions here; the graph-file matching primitive (``_matches_capability``)
stays there and is passed in to avoid a circular import.
"""

from __future__ import annotations

from typing import Callable

from weld._capabilities_registry import (
    LANGUAGE_FILE_EXTENSIONS,
    MULTI_LANGUAGE_FILES,
    StrategyCapability,
)

# A predicate deciding whether any graph file matches a capability's
# extension/basename signature. Injected from :mod:`weld.capabilities` so the
# single ``_matches_capability`` implementation is shared, not duplicated.
_MatchFn = Callable[[str, StrategyCapability], bool]


def _has_file_for(
    exts: tuple[str, ...],
    basenames: tuple[str, ...],
    graph_files: list[str],
    match_fn: _MatchFn,
) -> bool:
    """True iff a graph file matches *exts*/*basenames* (empty -> False)."""
    if not exts and not basenames:
        return False
    probe = StrategyCapability(
        file_extensions=frozenset(exts),
        file_basenames=frozenset(basenames),
    )
    return any(match_fn(path, probe) for path in graph_files)


def language_has_evidence_in_graph(
    stem: str,
    cap: StrategyCapability,
    language: str,
    graph_files: list[str],
    match_fn: _MatchFn,
) -> bool:
    """True iff the graph has evidence for *language* under strategy *stem*.

    For a single-language strategy this collapses to the strategy-wide
    signature check (its declared extensions already name exactly one
    language). For a multi-language strategy listed in
    :data:`MULTI_LANGUAGE_FILES` (``test_peer``), it consults the per-language
    split so only the language whose own extension is present in the graph
    flips true -- a ``.py`` file never lights up ``csharp``.
    """
    split = MULTI_LANGUAGE_FILES.get(stem)
    if split is None or language not in split:
        # Single-language strategy: its extension set is language-specific,
        # so the strategy-wide presence check is already per-language.
        if not cap.file_extensions and not cap.file_basenames:
            # Structural strategy with no file signature (its wiring alone is
            # the evidence); the caller has already ensured it is wired.
            return True
        return any(match_fn(path, cap) for path in graph_files)
    exts, basenames = split[language]
    return _has_file_for(exts, basenames, graph_files, match_fn)


def tree_sitter_language_rows(
    wired_languages: frozenset[str],
    cap: StrategyCapability,
    graph_files: list[str],
    match_fn: _MatchFn,
) -> dict[str, frozenset[str]]:
    """Evidence tags ``tree_sitter`` contributes, keyed by wired language.

    ``tree_sitter`` declares no ``languages`` in the registry; the languages
    it serves are wired per source entry in ``discover.yaml`` via ``language:``
    (*wired_languages*). Its evidence (``cap.evidence``) is attributed to each
    such language only when the graph contains a file of that language, so a
    C#-only repo lights up ``csharp`` (file/symbols/imports/calls) without
    inventing rows for languages that have no files present.

    Returns ``{language: evidence_tags}`` for languages with graph evidence;
    languages with none are omitted (the caller still surfaces them via the
    registry-completeness pass with all flags False).
    """
    out: dict[str, frozenset[str]] = {}
    for language in wired_languages:
        exts = LANGUAGE_FILE_EXTENSIONS.get(language)
        if not exts:
            # Unknown language name: no extension signature to gate on, so we
            # cannot honestly claim graph evidence. Skip rather than fabricate.
            continue
        if _has_file_for(exts, (), graph_files, match_fn):
            out[language] = cap.evidence
    return out
