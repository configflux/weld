"""Shared repo-boundary helpers for Weld discovery, init, and indexing.

Git-backed repositories use ``git ls-files --cached --others --exclude-standard``
as the source of truth for which files are in scope. This keeps tracked files
visible even if they also match ``.gitignore``, while dropping ignored
untracked files by default. Non-git directories fall back to the legacy
directory-exclusion policy.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Collection

# Directory names that are always excluded from Weld discovery results or the
# file index, even when they are tracked in git.
EXCLUDED_DIR_NAMES: frozenset[str] = frozenset([
    ".git",
    "node_modules",
    "__pycache__",
    ".weld",
    "bazel-bin",
    "bazel-out",
    "bazel-testlogs",
    "bazel-project",
    ".worktrees",
])

_BAZEL_DIR_RE = re.compile(r"^bazel-")

# Paths under nested repo copies must be ignored even if a git command can see
# them from the outer repository.
EXCLUDED_NESTED_REPO_SEGMENTS: tuple[tuple[str, ...], ...] = (
    (".claude", "worktrees"),
)

def is_excluded_dir_name(name: str) -> bool:
    """Return True when *name* is always outside the weld repo boundary."""
    if name in EXCLUDED_DIR_NAMES:
        return True
    if _BAZEL_DIR_RE.match(name):
        return True
    return False

def is_nested_repo_copy(path_parts: tuple[str, ...]) -> bool:
    """Return True if *path_parts* live under a nested repo-copy segment."""
    for segments in EXCLUDED_NESTED_REPO_SEGMENTS:
        seg_len = len(segments)
        for idx in range(len(path_parts) - seg_len + 1):
            if path_parts[idx : idx + seg_len] == segments:
                return True
    return False

def _is_dir_name_excluded(
    name: str,
    *,
    extra_excluded_dir_names: Collection[str],
) -> bool:
    return name in extra_excluded_dir_names or is_excluded_dir_name(name)

def _parts_excluded(
    parts: tuple[str, ...],
    *,
    is_dir: bool,
    extra_excluded_dir_names: Collection[str],
) -> bool:
    dir_parts = parts if is_dir else parts[:-1]
    for part in dir_parts:
        if _is_dir_name_excluded(
            part,
            extra_excluded_dir_names=extra_excluded_dir_names,
        ):
            return True
    return is_nested_repo_copy(parts)

@dataclass(frozen=True)
class RepoBoundary:
    """Git-backed repo visibility snapshot for one root."""

    root: Path
    visible_files: frozenset[str] | None
    visible_dirs: frozenset[str]

    @property
    def uses_git(self) -> bool:
        return self.visible_files is not None

def _git_repo_context(root: Path) -> tuple[Path, str] | None:
    proc = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "LC_ALL": "C"},
    )
    if proc.returncode != 0:
        return None

    repo_root = Path(proc.stdout.strip()).resolve()
    try:
        prefix_path = root.resolve().relative_to(repo_root)
    except ValueError:
        return None

    prefix = prefix_path.as_posix()
    return repo_root, prefix

def _strip_prefix(path: str, prefix: str) -> str | None:
    if not prefix or prefix == ".":
        return path
    prefix_with_sep = f"{prefix}/"
    if path == prefix:
        return ""
    if path.startswith(prefix_with_sep):
        return path[len(prefix_with_sep) :]
    return None

@lru_cache(maxsize=None)
def _load_repo_boundary(root_str: str) -> RepoBoundary:
    root = Path(root_str)
    context = _git_repo_context(root)
    if context is None:
        return RepoBoundary(root=root, visible_files=None, visible_dirs=frozenset())

    repo_root, prefix = context
    proc = subprocess.run(
        [
            "git",
            "-C",
            str(repo_root),
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "--full-name",
            "-z",
        ],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "LC_ALL": "C"},
    )
    if proc.returncode != 0:
        return RepoBoundary(root=root, visible_files=None, visible_dirs=frozenset())

    files: set[str] = set()
    dirs: set[str] = set()
    for raw in proc.stdout.split("\0"):
        if not raw:
            continue
        rel = _strip_prefix(raw, prefix)
        if rel is None or not rel:
            continue
        rel_path = Path(rel)
        rel_str = rel_path.as_posix()
        if _parts_excluded(
            rel_path.parts,
            is_dir=False,
            extra_excluded_dir_names=(),
        ):
            continue
        files.add(rel_str)

        parent = rel_path.parent
        while str(parent) != ".":
            dirs.add(parent.as_posix())
            parent = parent.parent

    return RepoBoundary(
        root=root,
        visible_files=frozenset(files),
        visible_dirs=frozenset(dirs),
    )

def get_repo_boundary(root: Path) -> RepoBoundary:
    """Return the cached repo-boundary snapshot for *root*."""
    return _load_repo_boundary(str(root.resolve()))

def path_within_repo_boundary(
    root: Path,
    path: Path,
    *,
    fallback_excluded_dir_names: Collection[str] = (),
) -> bool:
    """Return True when *path* is inside weld's repo-visible boundary."""
    root = root.resolve()
    try:
        rel = path.resolve().relative_to(root)
    except ValueError:
        return False

    if str(rel) == ".":
        return True

    boundary = get_repo_boundary(root)
    is_dir = path.is_dir()

    if _parts_excluded(rel.parts, is_dir=is_dir, extra_excluded_dir_names=()):
        return False

    rel_str = rel.as_posix()
    if boundary.uses_git:
        if is_dir:
            return rel_str in boundary.visible_dirs
        return rel_str in (boundary.visible_files or frozenset())

    if _parts_excluded(
        rel.parts,
        is_dir=is_dir,
        extra_excluded_dir_names=fallback_excluded_dir_names,
    ):
        return False

    return True

def filter_repo_paths(
    root: Path,
    paths: list[Path],
    *,
    fallback_excluded_dir_names: Collection[str] = (),
) -> list[Path]:
    """Filter *paths* down to weld-visible repo paths."""
    return [
        path
        for path in paths
        if path_within_repo_boundary(
            root,
            path,
            fallback_excluded_dir_names=fallback_excluded_dir_names,
        )
    ]

def iter_repo_files(
    root: Path,
    *,
    fallback_excluded_dir_names: Collection[str] = (),
) -> list[Path]:
    """Return repo-visible files under *root* in stable order."""
    root = root.resolve()
    boundary = get_repo_boundary(root)
    if boundary.uses_git:
        return [root / rel for rel in sorted(boundary.visible_files or ())]

    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # ADR 0012 §2 row 2: materialize + sort walk order so traversal is a
        # property of the tree, not of filesystem enumeration (scandir on ext4
        # and many overlays is hash/creation order, not lex order). Sort in
        # place so os.walk picks up the sorted view for subsequent descent.
        dirnames[:] = sorted(
            d
            for d in dirnames
            if not _is_dir_name_excluded(
                d,
                extra_excluded_dir_names=fallback_excluded_dir_names,
            )
        )

        rel_dir = Path(dirpath).relative_to(root)
        if is_nested_repo_copy(rel_dir.parts):
            dirnames.clear()
            continue

        for filename in sorted(filenames):
            filepath = Path(dirpath) / filename
            rel_path = filepath.relative_to(root)
            if _parts_excluded(
                rel_path.parts,
                is_dir=False,
                extra_excluded_dir_names=fallback_excluded_dir_names,
            ):
                continue
            files.append(filepath)

    return files
