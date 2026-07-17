"""Git helpers for the connected structure tooling.

Provides functions to query git state without importing external
libraries -- uses subprocess to call the git CLI directly.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

def get_git_sha(root: Path) -> str | None:
    """Return the current HEAD SHA for the repo at *root*, or None.

    Returns ``None`` when *root* is not inside a git repository or
    when ``git`` is not available.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=str(root),
            timeout=5,
            env={**os.environ, "LC_ALL": "C"},
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None

def git_main_checkout_path(root: Path) -> Path | None:
    """Return the main git worktree's checkout for *root*, or ``None``.

    When *root* lives inside a linked git worktree (created via
    ``git worktree add ...``), ``git rev-parse --git-common-dir`` resolves
    to the main checkout's ``.git`` directory; the parent of that path is
    the main worktree itself -- where sibling repositories registered in
    a federated ``workspaces.yaml`` actually live (ADR 0028).

    Returns ``None`` when *root* is not inside a git repository, when
    ``git`` is not available, when the lookup fails, or when the resolved
    main checkout is the same directory as *root* (i.e. *root* is already
    the main worktree, so there is nothing to fall back to).
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            cwd=str(root),
            timeout=5,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    common_dir = result.stdout.strip()
    if not common_dir:
        return None
    # ``git rev-parse --git-common-dir`` may return a path relative to
    # *root* (the typical case is just ".git"); resolve it before taking
    # the parent so the result is absolute and stable.
    common_path = (Path(root) / common_dir).resolve()
    main_checkout = common_path.parent
    try:
        if main_checkout.resolve() == Path(root).resolve():
            # *root* is already the main worktree; nothing to fall back to.
            return None
    except OSError:
        return None
    return main_checkout


def is_git_repo(root: Path) -> bool:
    """Return True if *root* is inside a git working tree."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            cwd=str(root),
            timeout=5,
            env={**os.environ, "LC_ALL": "C"},
        )
        return result.returncode == 0 and result.stdout.strip() == "true"
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False

def ancestor_shas(root: Path, max_count: int = 50) -> list[str]:
    """Return HEAD and up to *max_count*-1 ancestors, nearest first.

    Wraps ``git rev-list --max-count=N HEAD``. The first element is HEAD; each
    subsequent element is its next ancestor on the rev-list walk. Used by
    ``wd warm`` (ADR 0067) to probe a CI artifact store for the nearest commit
    that has a published graph.

    Returns ``[]`` when *root* is not a git checkout, ``git`` is unavailable,
    the command fails, or *max_count* is not positive -- callers treat an empty
    list as "no candidates" and fall back to a full local discover.
    """
    if max_count <= 0:
        return []
    try:
        result = subprocess.run(
            ["git", "rev-list", f"--max-count={max_count}", "HEAD"],
            capture_output=True,
            text=True,
            cwd=str(root),
            timeout=10,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def commits_behind(root: Path, old_sha: str, new_sha: str) -> int:
    """Count commits between *old_sha* and *new_sha*.

    Returns the number of commits reachable from *new_sha* that are not
    reachable from *old_sha* (i.e. ``git rev-list --count old..new``).

    Returns ``-1`` if the count cannot be determined (e.g. force-push
    removed the old SHA from history).
    """
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", f"{old_sha}..{new_sha}"],
            capture_output=True,
            text=True,
            cwd=str(root),
            timeout=10,
            env={**os.environ, "LC_ALL": "C"},
        )
        if result.returncode == 0:
            return int(result.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError, ValueError):
        pass
    return -1


# Weld's own bookkeeping files written by ``wd discover`` / ``wd touch``.
# These are never user *source*: they are outputs of discovery and must
# not contribute to ``source_stale`` (tracked issue), even when a broad
# ``discovered_from`` (e.g. ``['./']`` from default ``wd init``) would
# otherwise match them. Keep this set small and explicit; do not extend
# it to user-visible files.
_WELD_BOOKKEEPING_PATHS = frozenset({
    ".weld/graph.json",
    ".weld/discovery-state.json",
    # Persisted query-state cache written alongside graph.json by
    # ``wd discover`` and refreshed on cache misses by ``Graph.load``
    # (ADR 0031). Same trust boundary, same "never user source" rule.
    ".weld/query_state.bin",
    # Keyword-to-file index written by ``wd discover`` and ``wd
    # build-index``. Functionally a sibling of graph.json -- output of
    # discovery, never user source. Without this entry a user who
    # commits .weld/file-index.json alongside graph.json would see
    # ``wd prime`` report spurious source drift and fall into the same
    # touch/commit loop the other bookkeeping entries already prevent.
    ".weld/file-index.json",
    # SQLite sidecar written alongside graph.json by ``wd discover``
    # (ADR 0058). Pure derived index; same trust boundary as graph.json
    # itself. Must be in this set so a commit that includes graph.db
    # alongside a wd-touched graph.json does not trip the source-drift
    # detector and force a spurious rebuild.
    ".weld/graph.db",
    # Volatile-meta sidecar written alongside graph.json by ``wd discover``
    # / ``wd touch`` (ADR 0065): holds the wall-clock ``updated_at`` and the
    # ``git_sha`` that used to live in graph.json. Gitignored by default,
    # but a user (or a test) who commits it alongside graph.json must not
    # see it counted as a changed *source* file -- it is pure weld output,
    # the same trust boundary as graph.json itself.
    ".weld/graph-meta.json",
    # Surface-hash companion to file-index.json written by ``wd discover``
    # (bd 85tb.2): records the SHA of every indexed file so the next refresh
    # re-tokenizes only what changed. Pure weld output, same trust boundary
    # as file-index.json. Without this entry every read self-heal writes a
    # fresh, untracked ``.weld/file-index-state.json`` that the working-tree
    # drift probe counts as source change -- making every repo perpetually
    # ``source_stale`` and defeating the cheap refresh-on-read contract.
    ".weld/file-index-state.json",
})


def drift_is_graph_only(root: Path, graph_sha: str) -> bool:
    """Return True if every file changed between *graph_sha* and HEAD is
    a weld-bookkeeping file (ADR 0017, tracked issue, tracked issue).

    ``wd touch`` stamps ``meta.git_sha = HEAD`` before the user commits
    the graph. Committing ``.weld/graph.json`` (and possibly
    ``.weld/discovery-state.json``) then moves HEAD forward while the
    recorded ``graph_sha`` still points at pre-commit HEAD. The SHA
    drift that results is purely bookkeeping: the graph matches its
    inputs, there is nothing to do. Reporting it as drift makes
    ``wd prime`` suggest another ``wd touch``, which then requires
    another commit, which bumps HEAD again -- an infinite touch/commit
    loop.

    This helper detects that exact situation. It returns True only when
    the diff is non-empty AND every changed path is a weld-bookkeeping
    file (see ``_WELD_BOOKKEEPING_PATHS``). When the diff cannot be
    computed (git missing, SHA unreachable, force-push) or when the
    diff is empty, the answer is False and callers fall back to the
    normal ``sha_behind`` signal.
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{graph_sha}..HEAD"],
            capture_output=True, text=True, cwd=str(root), timeout=10,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    if result.returncode != 0:
        return False
    paths = [p for p in result.stdout.splitlines() if p]
    if not paths:
        return False
    return all(p in _WELD_BOOKKEEPING_PATHS for p in paths)


def _path_is_tracked(path: str, tracked: list[str]) -> bool:
    """Return True if *path* falls under any prefix in *tracked*.

    *tracked* is a list of directory prefixes or file paths (as stored
    in ``meta.discovered_from``). Directory prefixes may end in ``/`` or
    be bare names; both forms match descendants. The root marker ``"./"``
    / ``"."`` means every path is tracked (strategies that scan from the
    repo root record their ``discovered_from`` that way).

    Weld bookkeeping files (``.weld/graph.json`` and siblings) are never
    source and return False regardless of *tracked* -- a broad ``['./']``
    (default ``wd init``) would otherwise match them.
    """
    if path in _WELD_BOOKKEEPING_PATHS:
        return False
    for prefix in tracked:
        if not isinstance(prefix, str) or not prefix:
            continue
        if (
            prefix in (".", "./")
            or (prefix.endswith("/") and path.startswith(prefix))
            or path == prefix
            or path.startswith(prefix.rstrip("/") + "/")
        ):
            return True
    return False


def source_files_changed_since(
    root: Path, graph_sha: str, tracked: list[str]
) -> list[str]:
    """Return files changed between *graph_sha* and HEAD that fall under
    any path in *tracked* (ADR 0017).

    *tracked* is a list of directory prefixes or file paths (as stored
    in ``meta.discovered_from``); see :func:`_path_is_tracked` for the
    prefix-match rules. An empty *tracked* yields an empty result --
    nothing can be intersected.

    Weld's own bookkeeping files (``.weld/graph.json``,
    ``.weld/discovery-state.json``, and siblings) are always excluded --
    they are outputs of discovery, never user source, and a broad
    ``tracked`` such as ``['./']`` (default ``wd init``) would otherwise
    match them on every graph-commit and produce a false ``source_stale``
    (tracked issue).

    Returns ``[]`` when the diff cannot be computed (git missing, SHA
    unreachable, force-push): callers must treat that as "unknown" and
    fall back to other staleness signals.
    """
    if not tracked:
        return []
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{graph_sha}..HEAD"],
            capture_output=True, text=True, cwd=str(root), timeout=10,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    if result.returncode != 0:
        return []
    return [
        path
        for path in result.stdout.splitlines()
        if path and _path_is_tracked(path, tracked)
    ]


def _parse_porcelain_v2_paths(stdout: str) -> list[str]:
    """Extract changed file paths from ``git status --porcelain=v2 -z``.

    Record types (porcelain v2): ``1`` ordinary changed entry, ``2``
    rename/copy (followed by an extra NUL-separated original-path token
    we discard), ``u`` unmerged, ``?`` untracked, ``!`` ignored. Header
    lines start with ``#`` and are skipped. ``-z`` emits NUL-separated
    records; the trailing NUL after the last record yields an empty tail
    token dropped by the truthy filter. Mirrors the proven parser in
    :mod:`weld.impact_cli`.
    """
    tokens = [tok for tok in stdout.split("\0") if tok]
    paths: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        i += 1
        prefix = tok[0]
        if prefix == "#":
            continue
        if prefix == "1":
            parts = tok.split(" ", 8)  # 9 fields; path is the last.
            if len(parts) == 9:
                paths.append(parts[8])
        elif prefix == "2":
            parts = tok.split(" ", 9)  # 10 fields; path then orig-path.
            if len(parts) == 10:
                paths.append(parts[9])
            i += 1  # consume and discard the original-path token.
        elif prefix == "u":
            parts = tok.split(" ", 10)  # 11 fields; path is the last.
            if len(parts) == 11:
                paths.append(parts[10])
        elif prefix in ("?", "!"):
            parts = tok.split(" ", 1)
            if len(parts) == 2:
                paths.append(parts[1])
    return paths


def working_tree_dirty_sources(
    root: Path, tracked: list[str], *, detect_renames: bool = True
) -> list[str]:
    """Return uncommitted-change paths under *tracked* prefixes (ADR 0017).

    Refines the freshness signal: ``source_files_changed_since`` only
    sees committed ``graph_sha..HEAD`` diffs, so an agent editing a
    tracked source file *without committing* would query a graph that
    ignores its own changes. This helper detects that dirty state by
    parsing ``git status --porcelain=v2 --untracked-files=all -z`` and
    intersecting the changed paths (staged, unstaged, untracked, renamed,
    unmerged) with *tracked* via :func:`_path_is_tracked`.

    Weld bookkeeping dirt (``.weld/graph.json`` and siblings) is excluded
    so that committing/touching the graph -- the only "dirt" present in
    the bookkeeping-only case -- never trips the signal.

    *detect_renames* (bd o18k): ``False`` adds ``--no-renames`` so a rename
    surfaces its vacated original as an explicit deletion -- needed by a caller
    keying a content cache on the dirty *set* (:mod:`weld._refresh_cache`).

    Cheap by construction: an empty *tracked* short-circuits before any
    git call, and a clean tree returns the empty list after a single
    ``git status`` (no per-file hashing). ``-c core.quotePath=false``
    keeps unicode/quoted filenames as UTF-8 and prevents a filename
    containing ``" -> "`` from being misread as a rename. The argv is
    fixed and *tracked* is never interpolated into the command, so there
    is no shell-injection surface. Read-only: no git state is mutated.

    Returns ``[]`` when the status cannot be computed (git missing,
    non-git root, timeout): callers fall back to other staleness signals.
    """
    if not tracked:
        return []
    argv = [
        # ``--untracked-files=all`` lists every untracked file by its full
        # path instead of collapsing a fully-untracked directory into a
        # single ``dir/`` summary entry. The summary form would defeat the
        # bookkeeping filter: an untracked ``.weld/`` (its files not yet
        # committed) would arrive as the bare ``.weld/`` directory, which is
        # not in ``_WELD_BOOKKEEPING_PATHS`` and would wrongly count as
        # source drift under a broad ``./`` prefix.
        "git", "-c", "core.quotePath=false",
        "status", "--porcelain=v2", "--untracked-files=all", "-z",
    ]
    if not detect_renames:
        argv.append("--no-renames")
    try:
        result = subprocess.run(
            argv,
            capture_output=True, text=True, cwd=str(root), timeout=10,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    if result.returncode != 0:
        return []
    return [
        path
        for path in _parse_porcelain_v2_paths(result.stdout)
        if _path_is_tracked(path, tracked)
    ]
