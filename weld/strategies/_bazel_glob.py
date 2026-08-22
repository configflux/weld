"""Bounded ``glob()`` evaluation for BUILD files (bd mhn7, ADR 0025/0108).

ADR 0108 resolved ``load()``, so ``//examples:example_files`` exists as a node
and "which tests execute against ``examples/``?" is answerable. The other half
stayed dark: *which files* that filegroup covers, because the package declares
``filegroup(srcs = glob(["**/*"]))`` and a call evaluated to ``UNEVALUATABLE``.
The chain ``file -> filegroup -> test`` broke at the first hop.

This module closes that hop, and it is written against one rule: **never invent
a member.** A wrong-but-real entry is worse than a missing one, because nothing
downstream can tell it from a real one -- the lesson ADR 0044/0105/0108 keep
re-learning. Two consequences follow.

*Bazel's semantics, not pathlib's.* The trap here is package boundaries: a
bazel glob does **not** descend into a directory that owns a ``BUILD`` or
``BUILD.bazel`` file, because those files belong to that subpackage and not to
the globbing one. Matching with a plain recursive walk would hand a filegroup
every file in every package beneath it. Directories are excluded too
(``exclude_directories = 1`` is the default), as is ``exclude``.

*Bounds, because the tree is untrusted.* weld runs discovery over repositories
it did not write. The walk prunes the directory names weld already refuses to
descend, never follows symlinks (the amplification path that let Bazel runfiles
leak into discovery), and stops at :data:`MAX_GLOB_FILES`. Hitting that cap
returns ``None``, which every caller treats as "unevaluatable" -- so a
pathological package contributes *nothing* rather than a truncated member list
that would read as complete.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from weld.strategies._bazel_eval import GLOB_RESOLVER_KEY

#: Files one ``glob()`` call may visit before it gives up. A package that
#: exceeds this returns ``None`` (unevaluatable) rather than a partial answer:
#: a truncated member list is indistinguishable from a complete one downstream,
#: which is exactly the failure this module refuses to introduce. The largest
#: package in this repo is ~700 files.
MAX_GLOB_FILES: int = 20_000

#: Directory names never descended into: VCS metadata, caches, and Bazel's own
#: output symlinks. Deliberately a *name* list and not "anything starting with a
#: dot" -- bazel globs dotted directories, and this repo's own packages carry
#: real sources in ``.weld/``, ``.github/`` and ``.claude/``. Pruning those made
#: the answer miss 34 of the 91 files bazel reports for ``//examples``, which
#: is the safe direction to be wrong in but still the wrong answer.
#:
#: Note ``.weld`` is absent for that exact reason: ``.weld/strategies/*.py`` is
#: a source file a filegroup legitimately claims.
_PRUNED_DIRS: frozenset[str] = frozenset({
    ".git", ".hg", ".svn", "__pycache__", "node_modules",
    ".venv", "venv", ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "bazel-out", "bazel-bin", "bazel-testlogs", ".cache",
})

#: Marks a directory as its own bazel package, which a parent's glob may not
#: cross into.
_PACKAGE_MARKERS: tuple[str, ...] = ("BUILD.bazel", "BUILD")


def _segment_regex(segment: str) -> str:
    """Translate one glob segment to regex, where ``*`` never crosses a ``/``."""
    out = []
    for char in segment:
        if char == "*":
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(char))
    return "".join(out)


def compile_pattern(pattern: str) -> re.Pattern[str]:
    """Compile a bazel glob *pattern* to a full-match regex.

    ``**`` is a whole path segment matching zero or more segments (so
    ``**/*.py`` matches ``a.py`` as well as ``x/y/a.py``), and ``*`` matches
    within a single segment only.

    Adjacent ``**`` segments are collapsed first. That is semantics-preserving
    (``**/**`` selects exactly what ``**`` selects) and it is what keeps this
    safe on a hostile BUILD file: each ``**`` compiles to a ``(?:[^/]+/)*``
    group, so ``**/**/**/...`` would stack nested quantifiers over the same
    input -- the classic catastrophic-backtracking shape. Measured before the
    collapse, a 12-deep ``**`` chain took **77 seconds** to reject a single
    24-segment path. weld runs discovery over repositories it did not write
    (ADR 0025), so an attacker-supplied pattern is squarely in the threat model.
    """
    parts: list[str] = []
    raw = pattern.split("/")
    segments: list[str] = []
    for segment in raw:
        if segment == "**" and segments and segments[-1] == "**":
            continue
        segments.append(segment)
    for index, segment in enumerate(segments):
        if segment == "**":
            # Zero-or-more segments, including the separator that would
            # otherwise be left dangling when it matches zero.
            parts.append("(?:[^/]+/)*" if index < len(segments) - 1 else "(?:.*)")
        else:
            if index > 0 and segments[index - 1] != "**":
                parts.append("/")
            parts.append(_segment_regex(segment))
    return re.compile("".join(parts) + r"\Z")


def package_files(pkg_dir: Path) -> list[str] | None:
    """Return package-relative paths of every file *pkg_dir* itself owns.

    Stops at subpackage boundaries and at :data:`MAX_GLOB_FILES`. Returns
    ``None`` when the cap is hit or the directory cannot be read, so the caller
    can degrade to "unevaluatable" rather than to a partial listing.
    """
    try:
        if not pkg_dir.is_dir():
            return None
    except OSError:
        return None

    found: list[str] = []
    visited_dirs = 0
    root = str(pkg_dir)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        # Directories are counted against the same cap as files: a tree that is
        # deep and wide but *empty* would never trip a file-only bound, and
        # walking it is the cost an attacker is buying.
        visited_dirs += 1
        if visited_dirs > MAX_GLOB_FILES:
            return None
        keep: list[str] = []
        for name in sorted(dirnames):
            if name in _PRUNED_DIRS:
                continue
            child = os.path.join(dirpath, name)
            if any(os.path.exists(os.path.join(child, m)) for m in _PACKAGE_MARKERS):
                # Its own bazel package: those files are not ours to claim.
                continue
            if os.path.islink(child):
                continue
            keep.append(name)
        dirnames[:] = keep

        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                continue
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            found.append(rel)
            if len(found) > MAX_GLOB_FILES:
                return None
    return sorted(found)


def evaluate_glob(
    include: list[str],
    exclude: list[str],
    pkg_dir: Path,
    cache: dict | None = None,
) -> list[str] | None:
    """Return the sorted files *include* matches under *pkg_dir*, minus *exclude*.

    Returns ``None`` when the package listing is unavailable or over the cap --
    never a partial list. *cache* memoises the directory listing per package for
    one discovery run, the way the ``.bzl`` load cache does: a repo's BUILD
    files ask for overlapping globs and re-walking per call is the difference
    between one traversal and one per ``glob()``.
    """
    if not include:
        return []
    key = str(pkg_dir)
    if cache is not None and key in cache:
        listing = cache[key]
    else:
        listing = package_files(pkg_dir)
        if cache is not None:
            cache[key] = listing
    if listing is None:
        return None

    includes = [compile_pattern(p) for p in include]
    excludes = [compile_pattern(p) for p in exclude]
    return [
        rel for rel in listing
        if any(rx.match(rel) for rx in includes)
        and not any(rx.match(rel) for rx in excludes)
    ]


def glob_bindings(pkg_path: Path, cache: dict | None = None) -> dict:
    """Return the evaluator bindings that make ``glob()`` resolvable in a package.

    The glob module owns both the reserved key and the resolver, so a caller
    wiring this up needs one import and cannot bind the resolver under the
    wrong name. ``None`` from :func:`evaluate_glob` (over the file cap,
    unreadable directory) propagates as ``UNEVALUATABLE``, so the attribute
    contributes nothing rather than a partial list.
    """
    return {
        GLOB_RESOLVER_KEY: lambda include, exclude: evaluate_glob(
            include, exclude, pkg_path, cache,
        ),
    }
