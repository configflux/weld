"""Per-child git fan-out for federated ``wd impact`` seeds (ADR 0089).

``--from-diff`` / ``--working-tree`` shell out to git to discover which files
changed, then resolve those paths to graph seed nodes. At a single repo this
runs once against ``--root``. At a *federated* root the children are the git
repos (the root often is not), so seed discovery must fan out: run ``git diff``
/ ``git status`` inside every PRESENT child, resolve each child's paths against
that child's own graph, and federation-prefix the resolved seed ids
(``<child>\\x1f<local>``) so they line up with the read-time flattened graph the
reverse-BFS runs over. ``target`` / ``--files`` already resolve against that
flattened graph; this brings the two git-seeded modes to parity.

Why per-child (not one resolve over the flattened graph): a child node keeps its
child-relative ``props.file`` after flattening, so two children that share a
relative path (``src/app.py``) are indistinguishable by path in the union. By
resolving each child's diff against *its own* graph and then prefixing the
resulting seed ids, a change in child A's ``src/app.py`` seeds only child A.

Single-repo behavior is untouched: this module is entered only when
``.weld/workspaces.yaml`` is present (see :mod:`weld.impact_cli`).
"""

from __future__ import annotations

from pathlib import Path

from weld._git import is_git_repo
from weld._impact_git import (
    _git_diff_files,
    _git_status_files,
    _reject_dash_ref,
)
from weld._sqlite_reader import SqliteBackedGraph
from weld.federation_support import prefix_node_id
from weld.graph import Graph
from weld.impact_core import _low_capability_inputs, _resolve_paths_to_seeds

__all__ = ["federated_seed_resolution"]


def _scope_paths(scope_dir: Path, diff_ref: str | None) -> list[str]:
    """Return changed paths in *scope_dir* for the active seed mode.

    *diff_ref* set -> ``--from-diff`` (git diff, tolerant so a ref absent in
    this scope yields no paths rather than aborting). ``None`` ->
    ``--working-tree`` (git status porcelain).
    """
    if diff_ref is not None:
        return _git_diff_files(scope_dir, diff_ref, tolerant=True)
    return _git_status_files(scope_dir)


def _prefix_paths(child_path: str, paths: list[str]) -> list[str]:
    """Render child-relative *paths* as workspace-root-relative for display."""
    return [f"{child_path}/{path}" for path in paths]


def federated_seed_resolution(
    fg,
    root: Path,
    *,
    diff_ref: str | None,
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Resolve ``--from-diff`` / ``--working-tree`` seeds across a federation.

    Returns ``(seed_ids, unresolved_inputs, display_paths, low_capability)`` --
    all sorted and deduped. *diff_ref* selects the mode: a git ref for
    ``--from-diff`` or ``None`` for ``--working-tree``.

    ``low_capability`` is computed the same per-scope way as the seeds: each
    scope's changed paths are checked against *that scope's own graph* for
    file-only evidence (:func:`weld.impact_core._low_capability_inputs`), then
    the low results are re-noted with the child prefix. Computing it here (not
    once over the flattened union) is required because a flattened child keeps
    its child-relative ``props.file``, so a child-prefixed display path can
    never match in the union.

    Scopes that contribute seeds:

    - the workspace *root* itself, iff it is a git repo -- root-level nodes are
      un-prefixed in the flattened graph, so their seeds resolve against
      ``fg._root_graph`` and need no prefix. In the common polyrepo (root is a
      plain container) this scope is skipped entirely;
    - every PRESENT child (``_load_child`` returns a readable graph handle)
      that is a git repo -- its paths resolve against the child graph and the
      resulting seed ids are federation-prefixed with the child name.

    A ``--from-diff`` ref is validated once up front (leading-dash refs are
    rejected as a git-option-injection guard) so the check fires even when no
    scope ultimately contributes.
    """
    if diff_ref is not None:
        _reject_dash_ref(diff_ref)

    seeds: set[str] = set()
    unresolved: list[str] = []
    display_paths: list[str] = []
    low_capability: list[str] = []

    if is_git_repo(root):
        paths = _scope_paths(root, diff_ref)
        root_seeds, root_unresolved = _resolve_paths_to_seeds(fg._root_graph, paths)
        seeds.update(root_seeds)
        unresolved.extend(root_unresolved)
        display_paths.extend(paths)
        # Root nodes are un-prefixed in the union, so display == local path.
        low_capability.extend(
            _low_capability_inputs(fg._root_graph, root_seeds, paths),
        )

    for name in sorted(fg._children):
        loaded = fg._load_child(name)
        if not isinstance(loaded, (Graph, SqliteBackedGraph)):
            # missing / uninitialized / corrupt children contribute nothing,
            # exactly as the flatten and callers fan-out already skip them.
            continue
        child_path = fg._children[name].path
        child_dir = root / child_path
        if not is_git_repo(child_dir):
            continue
        paths = _scope_paths(child_dir, diff_ref)
        child_seeds, child_unresolved = _resolve_paths_to_seeds(loaded, paths)
        seeds.update(prefix_node_id(name, seed) for seed in child_seeds)
        unresolved.extend(_prefix_paths(child_path, child_unresolved))
        display_paths.extend(_prefix_paths(child_path, paths))
        # Child-relative paths vs the child's OWN graph, then child-prefixed to
        # match the display path -- the union match in impact() would miss these.
        low_capability.extend(
            _prefix_paths(child_path, _low_capability_inputs(loaded, child_seeds, paths)),
        )

    return (
        sorted(seeds),
        sorted(set(unresolved)),
        sorted(display_paths),
        sorted(set(low_capability)),
    )
