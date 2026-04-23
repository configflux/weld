"""Glob-style pattern matching and traversal for weld discovery.

This module owns the user-visible "how does `exclude:` and `glob:` match?"
contract. See ``docs/adrs/0020-exclude-semantics-and-boundary-hardening.md``.

Split out of :mod:`weld.repo_boundary` so the boundary module stays under
the 400-line soft cap; the two modules are intentionally coupled.
"""

from __future__ import annotations

import fnmatch
import os
import re
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Iterable

from weld.repo_boundary import (
    filter_repo_paths,
    is_excluded_dir_name,
    is_nested_repo_copy,
)


def matches_exclude(rel_posix: str, patterns: Iterable[str]) -> bool:
    """Check whether *rel_posix* is matched by any exclude *pattern*.

    *rel_posix* must be a posix-form repo-relative path (no leading ``/``).

    Each pattern is tested three ways; any match returns True:

    1. ``PurePosixPath(rel_posix).match(pattern)`` -- right-match on path
       segments. Handles bare patterns (``foo.py``, ``*.pyc``) and segmented
       ones without globstar (``tests/*.py``).
    2. ``fnmatch(rel_posix, pattern)`` for patterns containing ``/`` or
       ``**``. fnmatch treats ``*`` as matching any character including
       ``/``, so patterns like ``.cache/**``, ``compiler/**`` and
       ``foo/**/*.py`` behave as "anywhere under this subtree".
    3. ``fnmatch(basename, pattern)`` as a last-resort fallback -- preserves
       the pre-fix behaviour where bare filename patterns worked.
    """
    if not patterns:
        return False
    basename = rel_posix.rsplit("/", 1)[-1]
    for pattern in patterns:
        if not pattern:
            continue
        try:
            if PurePosixPath(rel_posix).match(pattern):
                return True
        except ValueError:
            pass
        if ("/" in pattern or "**" in pattern) and fnmatch.fnmatchcase(
            rel_posix, pattern
        ):
            return True
        if fnmatch.fnmatchcase(basename, pattern):
            return True
    return False


@lru_cache(maxsize=256)
def _glob_pattern_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a glob-style pattern into an anchored regex.

    Semantics:

    - ``**/`` at the start of a segment matches zero or more path segments
      (each ending in ``/``); rendered as ``(?:.*/)?``.
    - ``**`` elsewhere matches any run of characters including ``/``.
    - ``*`` matches any run of characters that does not contain ``/``.
    - ``?`` matches one character that is not ``/``.
    - ``[...]`` is a character class (``[!...]`` for negation); passes
      through to the regex engine with minimal massaging.

    The return value anchors at both ends so callers can use
    ``regex.match(rel_posix)``.
    """
    i = 0
    n = len(pattern)
    out = ["^"]
    while i < n:
        c = pattern[i]
        if c == "*":
            if pattern[i : i + 2] == "**":
                j = i + 2
                if pattern[j : j + 1] == "/":
                    out.append("(?:.*/)?")
                    i = j + 1
                    continue
                out.append(".*")
                i = j
                continue
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "[":
            close = pattern.find("]", i + 1)
            if close == -1:
                out.append(re.escape(c))
                i += 1
            else:
                inner = pattern[i + 1 : close]
                if inner.startswith("!"):
                    inner = "^" + inner[1:]
                out.append("[" + inner + "]")
                i = close + 1
        else:
            out.append(re.escape(c))
            i += 1
    out.append("$")
    return re.compile("".join(out))


def walk_glob(
    root: Path,
    pattern: str,
    *,
    excludes: Iterable[str] | None = None,
) -> list[Path]:
    """Return files under *root* matching *pattern*, pruning excluded dirs.

    For patterns containing ``**``, performs an :func:`os.walk`-based
    traversal that prunes excluded directories (``EXCLUDED_DIR_NAMES``,
    nested repo copies, and user *excludes*) before descent. This avoids
    paying the traversal cost of large ignored trees like ``.cache/bazel``
    or ``node_modules`` and removes the symlink amplification path that
    previously let Bazel runfiles leak into discovery.

    For patterns without ``**``, delegates to pathlib (no recursion,
    single-directory glob).

    Symlinks are never followed (``followlinks=False`` default). The
    repo-boundary filter is applied to the final list so git-hidden and
    nested-repo-copy files are dropped as usual.
    """
    excl = [p for p in (excludes or []) if p]

    if "**" not in pattern:
        parent = (root / pattern).parent
        if not parent.is_dir():
            return []
        raw = sorted(parent.glob(Path(pattern).name))
        filtered = filter_repo_paths(root, raw)
        if not excl:
            return filtered
        kept: list[Path] = []
        for path in filtered:
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:
                kept.append(path)
                continue
            if matches_exclude(rel, excl):
                continue
            kept.append(path)
        return kept

    regex = _glob_pattern_to_regex(pattern)
    results: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if not is_excluded_dir_name(d)
        )
        try:
            rel_dir = Path(dirpath).relative_to(root)
        except ValueError:
            continue
        if is_nested_repo_copy(rel_dir.parts):
            dirnames.clear()
            continue
        if excl:
            kept_dirs: list[str] = []
            for d in dirnames:
                dir_rel_posix = (rel_dir / d).as_posix()
                if matches_exclude(dir_rel_posix, excl):
                    continue
                kept_dirs.append(d)
            dirnames[:] = kept_dirs

        for filename in sorted(filenames):
            filepath = Path(dirpath) / filename
            try:
                rel_posix = filepath.relative_to(root).as_posix()
            except ValueError:
                continue
            if excl and matches_exclude(rel_posix, excl):
                continue
            if regex.match(rel_posix):
                results.append(filepath)

    return filter_repo_paths(root, results)
