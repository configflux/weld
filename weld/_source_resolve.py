"""Resolve ``.weld/discover.yaml`` source entries to concrete file lists.

Carved out of :mod:`weld.discovery_state`, which documents itself as
"a content-hash index" -- resolving a source entry's globs to files is
not that, and the split also keeps that module under the 400-line cap
(CLAUDE.md "Line-Count Policy"). Consumers are the discovery
orchestrator (:mod:`weld.discover`) and ``wd watch``.

This module is where the **index** path vocabulary is spelled, so every
repo-relative path it returns goes through :func:`weld._rel_path.rel_to_root`
and is POSIX. See that module for why: :mod:`weld.glob_match` builds the
in-scope listing with ``as_posix`` and the two are compared against each
other, so an OS-native spelling here made every in-scope file read as
uncovered off POSIX -- permanent ``coverage_stale``, discovery re-run on
every read (bd v552).
"""

from __future__ import annotations

from pathlib import Path

from weld._rel_path import rel_to_root


def resolve_source_files(
    root: Path,
    source: dict,
) -> list[str]:
    """Resolve files matched by a source entry's glob or files key.

    Returns repo-relative paths. The source-level ``exclude`` list is
    applied here so that every entry under ``.weld/discover.yaml`` honours
    excludes uniformly, independent of whether the dispatched strategy
    opts into its own per-file check.
    """
    from weld.glob_match import matches_exclude, walk_glob

    excludes = [p for p in (source.get("exclude") or []) if p]
    files: list[str] = []

    glob_pattern = source.get("glob")
    if glob_pattern:
        matched = walk_glob(root, glob_pattern, excludes=excludes)
        files = [rel_to_root(p, root) for p in matched]

    path_entry = source.get("path")
    if path_entry and (root / path_entry).exists():
        rel = rel_to_root(root / path_entry, root)
        if not excludes or not matches_exclude(rel, excludes):
            files.append(rel)

    for f in source.get("files", []):
        if not (root / f).exists():
            continue
        rel = rel_to_root(root / f, root)
        if excludes and matches_exclude(rel, excludes):
            continue
        files.append(rel)

    return files


def resolve_source_file_map(
    root: Path,
    sources: list[dict],
) -> list[list[str]]:
    """Resolve every source entry to its file list, memoizing duplicate globs.

    Returns one list per entry in *sources*, preserving order. Many
    ``.weld/discover.yaml`` configs point several strategy sources at the
    *same* glob (e.g. ``python_module`` / ``python_callgraph`` /
    ``python_package`` all on ``weld/*.py``); resolving each independently
    re-walks the tree once per duplicate. Memoizing by the
    resolution-relevant keys (glob, path, files, exclude) collapses those
    to one walk apiece -- ~280 ms on this repo's 21-source / 13-glob config
    (bd 85tb.2) -- while returning byte-identical lists (the same shared
    list object is reused for identical keys, which downstream code only
    reads).
    """
    cache: dict[tuple, list[str]] = {}
    out: list[list[str]] = []
    for source in sources:
        key = (
            source.get("glob"), source.get("path"),
            tuple(source.get("files") or ()), tuple(source.get("exclude") or ()),
        )
        resolved = cache.get(key)
        if resolved is None:
            resolved = resolve_source_files(root, source)
            cache[key] = resolved
        out.append(resolved)
    return out
