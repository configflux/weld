"""Nested-repo filesystem scanner for polyrepo workspace auto-discovery.

Walks a workspace root looking for nested ``.git`` directories and turns each
into a :class:`~weld.workspace.ChildEntry` with an auto-derived name and tags
(ADR 0011). Split out of :mod:`weld.workspace` (which re-exports the public
``scan_nested_repos*`` functions) so the schema/loader/validator module stays
within the line-count cap. The schema symbols below import from the
dependency-free :mod:`weld._workspace_schema` leaf rather than from
``weld.workspace`` directly -- importing them back from ``workspace.py``
was a real top-level import cycle (bd 5038-zw6w4, ADR 0130 disposition #14).
"""

from __future__ import annotations

import os
from pathlib import Path

from weld._workspace_schema import (
    DEFAULT_EXCLUDE_PATHS,
    DEFAULT_MAX_DEPTH,
    ChildEntry,
    NestedRepoScanResult,
    WorkspaceConfigError,
    auto_derive_name,
    auto_derive_tags,
)
from weld.workspace_scan_filter import (
    gitignored_child_paths,
    normalise_scan_exclude_patterns,
    path_matches_scan_exclude,
)

__all__ = [
    "scan_nested_repos",
    "scan_nested_repos_with_diagnostics",
]

# Directory names that the scanner always skips, independent of user
# configuration. ``.git`` is special: we stop *descending* into it but do not
# treat the parent as excluded. Items in this set apply to the directory name
# itself and cover weld's own storage plus common vendoring/cache patterns.
_BUILTIN_EXCLUDE_DIRS: frozenset[str] = frozenset({
    ".git",
    ".weld",
    ".hg",
    ".svn",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    "bazel-bin",
    "bazel-out",
    "bazel-testlogs",
    "bazel-project",
})


def _should_skip_dir(name: str) -> bool:
    if name in _BUILTIN_EXCLUDE_DIRS:
        return True
    if name.startswith("bazel-"):
        return True
    return False


def scan_nested_repos_with_diagnostics(
    root: Path | str,
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
    exclude_paths: list[str] | None = None,
    respect_gitignore: bool = False,
) -> NestedRepoScanResult:
    """Walk ``root`` looking for nested ``.git`` directories."""
    if max_depth < 1:
        raise WorkspaceConfigError(
            f"max_depth must be >= 1, got {max_depth}",
        )
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        raise WorkspaceConfigError(f"scan root is not a directory: {root_path}")

    exclude_patterns = normalise_scan_exclude_patterns(
        exclude_paths, DEFAULT_EXCLUDE_PATHS,
    )
    found: list[ChildEntry] = []

    def _walk(current: Path, depth: int) -> None:
        # At the workspace root we never register the root itself; at deeper
        # levels a .git directory means "stop descending and register this dir".
        if depth > 0 and (current / ".git").is_dir():
            rel = current.relative_to(root_path).as_posix()
            found.append(
                ChildEntry(
                    name=auto_derive_name(rel),
                    path=rel,
                    tags=auto_derive_tags(rel),
                ),
            )
            return
        if depth >= max_depth:
            return
        try:
            entries = sorted(os.listdir(current))
        except OSError:
            return
        for entry in entries:
            sub = current / entry
            if not sub.is_dir() or sub.is_symlink():
                continue
            if _should_skip_dir(entry):
                continue
            if path_matches_scan_exclude(root_path, sub, exclude_patterns):
                continue
            _walk(sub, depth + 1)

    _walk(root_path, 0)
    found.sort(key=lambda c: c.path)
    if not respect_gitignore:
        return NestedRepoScanResult(children=found)
    skipped = gitignored_child_paths(root_path, [entry.path for entry in found])
    if not skipped:
        return NestedRepoScanResult(children=found)
    kept = [entry for entry in found if entry.path not in skipped]
    return NestedRepoScanResult(
        children=kept,
        skipped_by_gitignore=sorted(skipped),
    )


def scan_nested_repos(
    root: Path | str,
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
    exclude_paths: list[str] | None = None,
    respect_gitignore: bool = False,
) -> list[ChildEntry]:
    """Walk ``root`` looking for nested ``.git`` directories."""
    return scan_nested_repos_with_diagnostics(
        root,
        max_depth=max_depth,
        exclude_paths=exclude_paths,
        respect_gitignore=respect_gitignore,
    ).children
