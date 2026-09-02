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
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Iterable, Iterator

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


_GlobKey = tuple[str, str, tuple[str, ...]]

_GLOB_SCOPE: ContextVar[dict[_GlobKey, list[Path]] | None] = ContextVar(
    "weld_glob_scope", default=None,
)


@contextmanager
def glob_scope() -> Iterator[None]:
    """Memoize :func:`walk_glob` results for the duration of one operation.

    A glob result is a point-in-time observation of the tree, so the memo is
    bound to an *operation*, never to the process -- the same rule
    :func:`weld.repo_boundary.repo_boundary_scope` follows, and for the same
    reason (bd jbpb): the warm-refresh entry runs inside a long-lived host
    (the MCP stdio server, weld embedded as a library), which must never
    inherit an earlier run's file listing.

    Inside the scope each distinct ``(root, pattern, excludes)`` triple is
    walked once and reused. A discovery run asks for the identical triple
    several times: once when the source-file resolver builds the file map,
    then once more inside *every* strategy that re-resolves the same glob in
    its own ``extract()``. ``resolve_source_file_map`` already collapses the
    entries sharing a glob (bd 85tb.2), but that memo cannot reach inside a
    strategy -- on this repo the three sources on ``weld/**/*.py``
    (python_module / python_callgraph / python_package) each re-walked the
    whole tree, so one glob cost four traversals plus four runs of the
    per-path repo-boundary filter (bd cjij).

    Re-entrant: a nested scope joins the enclosing one instead of starting a
    fresh memo. Leaving the outermost scope drops it, so the next operation
    observes the tree as it is then.

    With no scope open :func:`walk_glob` never memoizes, so direct callers
    outside a bracketed operation are unaffected.
    """
    if _GLOB_SCOPE.get() is not None:
        yield  # Join the enclosing operation; it owns the memo.
        return
    token = _GLOB_SCOPE.set({})
    try:
        yield
    finally:
        _GLOB_SCOPE.reset(token)


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

    For patterns without ``**`` whose directory part is literal, delegates to
    pathlib (no recursion, single-directory glob) and drops the directories
    ``Path.glob`` matches, so both branches return the same kind of thing (bd
    0d73). A wildcard in that part takes the traversal branch (bd uhxjc).

    Symlinks are never followed (``followlinks=False`` default). The
    repo-boundary filter is applied to the final list so git-hidden and
    nested-repo-copy files are dropped as usual.

    Inside a :func:`glob_scope` a repeated identical request is served from
    that operation's memo rather than re-walking the tree.
    """
    excl = [p for p in (excludes or []) if p]

    scope = _GLOB_SCOPE.get()
    if scope is None:
        return _walk_glob_uncached(root, pattern, excl)

    # Keyed on the literal root form: the walker consumes *root* as given for
    # both ``os.walk`` and its ``relative_to`` calls, so two spellings of one
    # directory can yield differently-rooted paths. Distinct spellings simply
    # miss the memo -- never a wrong answer for a cheaper one.
    key = (str(root), pattern, tuple(excl))
    hit = scope.get(key)
    # ``is None``, not falsiness: a glob that legitimately matches nothing is
    # memoized too, or the emptiest patterns would re-walk on every request.
    if hit is None:
        hit = _walk_glob_uncached(root, pattern, excl)
        scope[key] = hit
    # Hand out a copy: callers own their list and some sort or extend it
    # in place, which must not corrupt what the next hit serves.
    return list(hit)


def expand_braces(pattern: str) -> list[str]:
    """Expand a single top-level ``{a,b,...}`` group into concrete patterns.

    Neither ``Path.glob`` nor :func:`_glob_pattern_to_regex` understands
    ``{``, so a ``discover.yaml`` entry like ``src/**/*.{ts,tsx}`` matches
    *nothing at all* without this. One group is rewritten into one pattern
    per trimmed, non-empty alternative, duplicates collapsed.

    Patterns with no braces, nested braces, or more than one group pass
    through unchanged rather than half-expanded: a half-expanded pattern is a
    wrong answer, an unexpanded one is the pre-existing (empty) answer, and no
    shipped config needs the multi-group form. Add it when one does.

    This lives beside :func:`matches_exclude` rather than in the strategy
    layer because brace expansion is glob *syntax*, and this module owns the
    user-visible "how does ``glob:`` match?" contract (ADR 0020, ADR 0112).
    Putting it anywhere else would give the strategies a wider glob language
    than :func:`weld._source_resolve.resolve_source_files` -- the call that
    decides which files discovery records as in scope -- so a brace glob would
    emit nodes for files the ADR 0101 accounting never knew were in scope, and
    editing one of them would never mark the graph stale. That accounting
    calls this from its own read path too, and did not once (bd 2z5no).

    *Exclude* patterns are deliberately not expanded: that is
    :func:`matches_exclude`'s vocabulary, which ADR 0020 fixed and bd ds5g
    explicitly held out of scope.
    """
    open_idx = pattern.find("{")
    if open_idx == -1:
        return [pattern]
    close_idx = pattern.find("}", open_idx + 1)
    if close_idx == -1:
        return [pattern]
    inner = pattern[open_idx + 1 : close_idx]
    if "{" in inner or "{" in pattern[close_idx + 1 :]:
        return [pattern]
    prefix, suffix = pattern[:open_idx], pattern[close_idx + 1 :]
    out: list[str] = []
    seen: set[str] = set()
    for raw in inner.split(","):
        alt = raw.strip()
        if not alt:
            continue
        expanded = f"{prefix}{alt}{suffix}"
        if expanded not in seen:
            seen.add(expanded)
            out.append(expanded)
    return out or [pattern]


def _walk_glob_uncached(
    root: Path,
    pattern: str,
    excl: list[str],
) -> list[Path]:
    """Walk the tree for :func:`walk_glob`, bypassing any open memo.

    Brace alternatives are walked one at a time and unioned. The single-
    alternative case -- every pattern in every shipped config but the
    TypeScript monorepo example -- short-circuits to exactly one walk, so it
    is byte-identical to the pre-expansion behaviour rather than merely
    equivalent.
    """
    patterns = expand_braces(pattern)
    if len(patterns) == 1:
        return _walk_one(root, patterns[0], excl)
    seen: set[Path] = set()
    out: list[Path] = []
    for concrete in patterns:
        for path in _walk_one(root, concrete, excl):
            if path not in seen:
                seen.add(path)
                out.append(path)
    return out


_GLOB_META = "*?["  # What makes a component a pattern, not a name (`**` aside).


def _directory_part_is_literal(pattern: str) -> bool:
    """True when everything before the last ``/`` is a plain path."""
    # No ``/`` at all means no directory part -- literal by definition.
    head, sep, _ = pattern.rpartition("/")
    return not sep or not any(char in head for char in _GLOB_META)


def _walk_one(
    root: Path,
    pattern: str,
    excl: list[str],
) -> list[Path]:
    """Walk one brace-free pattern, split on a stattable directory part.

    The flat branch stats that part, so it means something only when the part
    names a real directory. For ``apps/*/package.json`` it does not -- the
    parent is the literal ``<root>/apps/*``, which no filesystem has -- so it
    returned ``[]`` and the pattern matched **nothing at all** (bd uhxjc):
    bd t06t's defect (``docs/**/*.md`` -> the literal ``docs/**``) one branch
    over, still live because that fix only reached the *strategies*. Silence
    rather than a subset, so no partial result gives the user a clue.

    Such a pattern takes the traversal below, not ``root.glob()``, which
    would resolve the paths but re-open three settled contracts: pathlib
    yields matching *directories* beside files (bd 0d73), has no descent to
    prune so the directory form of ``exclude:`` loses its meaning (bd eerc,
    bd 9gdq), and skips neither ``EXCLUDED_DIR_NAMES`` nor nested repo
    copies. The traversal does all three, and :func:`_glob_pattern_to_regex`
    already renders a lone ``*`` as ``[^/]*`` -- one segment, never spanning
    ``/`` -- so this shape needs no new vocabulary, only the branch already
    speaking it. It walks from *root*, not the literal prefix: that is what
    every ``**`` pattern already costs, and a prefix-rooted variant would be
    a third resolution path to hold in agreement with the other two, the
    drift ADR 0112 prevents.
    """
    if "**" not in pattern and _directory_part_is_literal(pattern):
        parent = (root / pattern).parent
        if not parent.is_dir():
            return []
        # ``not is_dir()``, not ``is_file()`` (bd 0d73). ``Path.glob`` yields
        # matching *directories* alongside files, while the ``**`` branch
        # below iterates ``os.walk``'s ``filenames`` and never does -- so the
        # two branches answered with different *kinds* of thing for the same
        # documented contract ("return files under root matching pattern").
        # A directory that leaked through reached ``build_file_hashes``, which
        # opens it, takes ``IsADirectoryError`` as ``OSError`` and drops it:
        # in scope, with no hash and no incremental basis, and counted by the
        # ADR 0101 accounting as a file discovery should have covered.
        #
        # The predicate matches the ``**`` branch's set exactly rather than
        # being merely stricter: ``filenames`` includes broken symlinks and
        # other non-regular entries (``is_file()`` would drop those), and
        # excludes symlinks-to-directories (which land in ``dirnames``, and
        # which ``is_dir()`` does resolve and reject). Making the flat branch
        # stricter than the recursive one would be the same disagreement in
        # the other direction.
        raw = sorted(p for p in parent.glob(Path(pattern).name) if not p.is_dir())
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
