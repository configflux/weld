"""Filtering helpers for federated workspace child scans."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Iterable

from weld.glob_match import matches_exclude

__all__ = [
    "gitignored_child_paths",
    "normalise_scan_exclude_patterns",
    "path_matches_scan_exclude",
]


def normalise_scan_exclude_patterns(
    exclude_paths: Iterable[str] | None,
    defaults: Iterable[str],
) -> tuple[str, ...]:
    """Return deterministic workspace scan exclude patterns."""
    raw = list(defaults) if exclude_paths is None else list(exclude_paths) + list(defaults)
    patterns: list[str] = []
    seen: set[str] = set()
    for item in raw:
        s = str(item).strip().replace("\\", "/")
        if not s:
            continue
        s = s.lstrip("/")
        if s.startswith("./"):
            s = s[2:]
        s = s.rstrip("/")
        if not s or s in seen:
            continue
        seen.add(s)
        patterns.append(s)
    return tuple(patterns)


def path_matches_scan_exclude(root: Path, path: Path, patterns: Iterable[str]) -> bool:
    """Return True when ``path`` should be skipped by scan exclude patterns."""
    try:
        rel_posix = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return True
    if not rel_posix or rel_posix == ".":
        return False
    return matches_exclude(rel_posix, patterns)


def _candidate_prefixes(rel_posix: str) -> list[str]:
    parts = [p for p in rel_posix.split("/") if p]
    return ["/".join(parts[:idx]) for idx in range(1, len(parts) + 1)]


def _gitignored_rel_paths(root: Path, rel_paths: Iterable[str]) -> frozenset[str]:
    rels = sorted({p for p in rel_paths if p})
    if not rels:
        return frozenset()
    payload = "\0".join(rels) + "\0"
    proc = subprocess.run(
        ["git", "-C", str(root), "check-ignore", "--stdin", "-z"],
        check=False,
        capture_output=True,
        input=payload,
        text=True,
        env={**os.environ, "LC_ALL": "C"},
    )
    if proc.returncode not in (0, 1):
        return frozenset()
    return frozenset(p for p in proc.stdout.split("\0") if p)


def gitignored_child_paths(root: Path, rel_paths: Iterable[str]) -> frozenset[str]:
    """Return child repo paths ignored by Git standard exclude rules.

    ``git check-ignore`` reports tracked paths as non-ignored by default,
    which is the desired behavior here: only untracked ignored child repos
    are skipped when the workspace scan opts into respecting gitignore.
    Ancestors are checked too, so a rule such as ``services/`` masks
    ``services/api`` even if the child path itself is not named directly.
    """
    candidates: dict[str, set[str]] = {}
    for rel in sorted({p.strip("/") for p in rel_paths if p}):
        for prefix in _candidate_prefixes(rel):
            candidates.setdefault(prefix, set()).add(rel)
    ignored_candidates = _gitignored_rel_paths(root, candidates)
    ignored_children: set[str] = set()
    for candidate in ignored_candidates:
        ignored_children.update(candidates.get(candidate, set()))
    return frozenset(ignored_children)
