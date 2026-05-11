"""C++ header/source pairing as graph edges (ADR 0057 Wave 2).

The layer-2 include resolver (:mod:`weld.strategies.cpp_resolver`)
already walks every project header to populate its symbol index, and
walks every project impl to rewrite call edges. The pairing
``header.h <-> source.cpp`` is implicit in that walk: an impl that
includes a header is likely the implementation of that header.

This module surfaces that relationship as an explicit graph edge so
``wd impact`` on a header propagates to its peer ``.cpp`` naturally:

    file:<header>  --implemented_by-->  file:<source>

Confidence policy (ADR 0057 Wave 2):

  * ``definite``: a same-directory or conventional-search-dir source
    with the same stem as the header
    (``include/foo.h`` <-> ``src/foo.cpp``,
    ``lib_alpha/alpha.hpp`` <-> ``lib_alpha/alpha.cpp``).
  * ``inferred``: same-directory fallback when no exact-stem peer
    exists but exactly one ``.cpp``/``.cc``/``.cxx`` lives in the
    header's directory.

The two passes do not fight each other: a header that has an exact
stem peer is paired definitively and does not trigger the fallback,
even if other sources live in the same directory.

The module is pure: it mutates the *edges* list its caller passes in
and never touches the filesystem.
"""

from __future__ import annotations

from pathlib import Path

from weld._node_ids import file_id as _file_id

#: Header extensions Wave 2 pairs. ``.h``/``.hh``/``.hpp``/``.hxx``
#: cover the conventional spellings; ``.ipp``/``.tpp`` (template impl)
#: are intentionally excluded because they have no ``.cpp`` peer by
#: convention.
PAIRABLE_HEADER_EXTS: frozenset[str] = frozenset(
    {".h", ".hh", ".hpp", ".hxx"},
)

#: Source extensions Wave 2 pairs. Mirrors the ``.cpp``/``.cc`` set
#: cpp_resolver already recognises plus ``.cxx`` and ``.c++`` for
#: completeness.
PAIRABLE_SOURCE_EXTS: frozenset[str] = frozenset(
    {".cpp", ".cc", ".cxx", ".c++", ".c"},
)


def _is_header(rel_path: str) -> bool:
    return Path(rel_path).suffix.lower() in PAIRABLE_HEADER_EXTS


def _is_source(rel_path: str) -> bool:
    return Path(rel_path).suffix.lower() in PAIRABLE_SOURCE_EXTS


def emit_header_source_pairs(
    per_file: list[dict],
    edges: list[dict],
    *,
    source_strategy: str = "tree_sitter",
) -> int:
    """Append ``header --implemented_by--> source`` edges.

    Args:
        per_file: The per-file state list assembled by
            :mod:`weld.strategies.tree_sitter` and augmented with
            headers by
            :func:`weld.strategies.cpp_resolver.augment_state_with_headers`.
            Each entry carries ``rel_path`` (POSIX repo-relative path).
        edges: Mutable list of graph edges that receives the new pair
            edges in deterministic order.
        source_strategy: ``props.source_strategy`` stamp on each emitted
            edge. Defaults to ``"tree_sitter"`` so the edge attributes
            to the same strategy the resolver runs under.

    Returns:
        The number of edges appended. Useful for tests and for the
        future ``wd capabilities`` evidence flag.
    """
    if not per_file:
        return 0

    # Bucket every project file by directory so the fallback can find
    # all sources in the header's dir without an O(N^2) rescan.
    by_dir: dict[str, list[dict]] = {}
    by_stem: dict[tuple[str, str], list[dict]] = {}
    for entry in per_file:
        rel = str(entry.get("rel_path") or "")
        if not rel:
            continue
        p = Path(rel)
        parent_posix = p.parent.as_posix()
        by_dir.setdefault(parent_posix, []).append(entry)
        by_stem.setdefault((parent_posix, p.stem), []).append(entry)

    # Iterate headers in a deterministic order (sorted by rel_path)
    # so the resulting edge list is reproducible across runs.
    headers = sorted(
        (e for e in per_file if _is_header(str(e.get("rel_path") or ""))),
        key=lambda e: str(e["rel_path"]),
    )
    appended = 0
    seen_pairs: set[tuple[str, str]] = set()
    for header in headers:
        rel_header = str(header.get("rel_path") or "")
        if not rel_header:
            continue
        peer = _find_stem_peer(rel_header, by_stem)
        confidence: str
        if peer is not None:
            confidence = "definite"
        else:
            peer = _find_single_dir_peer(rel_header, by_dir)
            if peer is None:
                continue
            confidence = "inferred"

        rel_source = str(peer.get("rel_path") or "")
        if not rel_source:
            continue
        header_id = _file_id(rel_header)
        source_id = _file_id(rel_source)
        if header_id == source_id:
            # Defensive: cpp_resolver records headers and impls under
            # the same per_file shape, but the same file should never
            # be both header and source.
            continue
        if (header_id, source_id) in seen_pairs:
            continue
        seen_pairs.add((header_id, source_id))
        edges.append(
            {
                "from": header_id,
                "to": source_id,
                "type": "implemented_by",
                "props": {
                    "source_strategy": source_strategy,
                    "confidence": confidence,
                },
            },
        )
        appended += 1
    return appended


def _find_stem_peer(
    rel_header: str,
    by_stem: dict[tuple[str, str], list[dict]],
) -> dict | None:
    """Return the same-directory source entry with the header's stem."""
    p = Path(rel_header)
    parent_posix = p.parent.as_posix()
    stem = p.stem
    # 1. Same directory.
    for cand in by_stem.get((parent_posix, stem), []):
        if _is_source(str(cand.get("rel_path") or "")):
            return cand
    # 2. Conventional search dirs: a header under ``include/foo.h``
    #    pairs with ``src/foo.cpp`` (and analogous ``inc`` / ``src``
    #    / repo-root layouts) when stems match.
    candidate_dirs = _conventional_pair_dirs(parent_posix)
    for cand_dir in candidate_dirs:
        for cand in by_stem.get((cand_dir, stem), []):
            if _is_source(str(cand.get("rel_path") or "")):
                return cand
    return None


def _conventional_pair_dirs(header_dir_posix: str) -> list[str]:
    """Return source-dir candidates for a header dir.

    The mapping mirrors the conventional cpp project layouts:
    ``include/X`` -> ``src/X`` (and ``X``), ``include`` -> ``src``,
    a bare directory -> repo root. Deterministic, no fs access.
    """
    parts = list(Path(header_dir_posix).parts) if header_dir_posix else []
    if not parts or parts == ["."]:
        return []
    candidates: list[str] = []
    if parts[0] == "include":
        # include/foo/ -> src/foo/, include/foo/ -> foo/
        tail = parts[1:]
        if tail:
            candidates.append(Path("src", *tail).as_posix())
            candidates.append(Path(*tail).as_posix())
        else:
            candidates.append("src")
    elif parts[0] == "inc":
        tail = parts[1:]
        if tail:
            candidates.append(Path("src", *tail).as_posix())
            candidates.append(Path(*tail).as_posix())
        else:
            candidates.append("src")
    # Also try the dir's siblings ``src``/``source`` if the header is
    # nested (``module/include/foo.h`` -> ``module/src/foo.cpp``).
    if "include" in parts:
        idx = parts.index("include")
        prefix = parts[:idx]
        tail = parts[idx + 1:]
        if prefix:
            base = Path(*prefix)
            if tail:
                candidates.append((base / "src" / Path(*tail)).as_posix())
                candidates.append((base / Path(*tail)).as_posix())
            else:
                candidates.append((base / "src").as_posix())
                candidates.append(base.as_posix())
    return candidates


def _find_single_dir_peer(
    rel_header: str,
    by_dir: dict[str, list[dict]],
) -> dict | None:
    """Return the single same-directory source, or None.

    Used only when :func:`_find_stem_peer` returns None. The check is
    deliberately conservative: more than one source in the directory
    means the fallback is ambiguous and we emit no edge.
    """
    p = Path(rel_header)
    parent_posix = p.parent.as_posix()
    sources_in_dir: list[dict] = []
    for entry in by_dir.get(parent_posix, []):
        rel = str(entry.get("rel_path") or "")
        if _is_source(rel):
            sources_in_dir.append(entry)
    if len(sources_in_dir) == 1:
        return sources_in_dir[0]
    return None


__all__ = [
    "PAIRABLE_HEADER_EXTS",
    "PAIRABLE_SOURCE_EXTS",
    "emit_header_source_pairs",
]
