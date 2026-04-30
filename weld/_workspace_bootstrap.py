"""One-shot polyrepo bootstrap orchestrator (ADR 0018).

``wd workspace bootstrap`` runs the complete federation-onboarding sequence
at a polyrepo root, in order:

1. Root init (``wd init`` on the root) when the root still lacks
   ``.weld/discover.yaml`` or ``.weld/workspaces.yaml``.
2. Scan the tree for nested git repositories (honouring ``--max-depth``),
   matching the scanner used by ``wd init``.
3. Per-child init: for every discovered child that has ``.git/`` but no
   ``.weld/discover.yaml``, run ``wd init`` inside the child.
4. In-process recurse-discover via :func:`weld._discover_recurse.recurse_children`
   so every child with status ``present`` or ``uninitialized`` gets a
   freshly built ``.weld/graph.json``.
5. Rebuild the ledger via :func:`weld.workspace_state.build_workspace_state`
   and re-emit the root meta-graph so
   :func:`weld.federation_root._present_children` observes the new state.

The orchestrator is intentionally a thin composition of existing
building blocks (``init``, ``init_workspace``, ``recurse_children``,
``build_workspace_state``, ``build_root_meta_graph``). It does not
introduce new lifecycle state and it never removes data it did not
create: a pre-existing child graph is refreshed by the in-process
recurse, never blown away. A fully-initialized workspace is a no-op
modulo ``meta.generated_at`` / ``meta.updated_at`` stamps.
"""

from __future__ import annotations

import sys
from pathlib import Path

from weld._gitignore_writer import write_weld_gitignore
from weld._workspace_bootstrap_result import BootstrapResult
from weld.init import init as _root_init
from weld.init_workspace import merge_yaml_and_scan_children
from weld.workspace import DEFAULT_MAX_DEPTH, ChildEntry, ScanConfig, WorkspaceConfig
from weld.workspace_state import (
    atomic_write_text,
    build_workspace_state,
    load_workspace_config,
    save_workspace_state,
)

__all__ = ["BootstrapResult", "bootstrap_workspace"]


def _run_root_init(root: Path) -> bool:
    """Run ``wd init`` at the root when the discover config is missing.

    Returns ``True`` when the root init wrote ``.weld/discover.yaml``.
    A pre-existing ``discover.yaml`` is a no-op because ``init()`` refuses
    to overwrite without ``--force``; this mirrors the idempotency rule
    from ADR 0018.
    """
    output = root / ".weld" / "discover.yaml"
    if output.exists():
        return False
    return _root_init(root, output, force=False)


def _child_has_discover_yaml(child_root: Path) -> bool:
    return (child_root / ".weld" / "discover.yaml").is_file()


def _init_child(
    child_root: Path,
    result: BootstrapResult,
    name: str,
    *,
    ignore_all: bool = False,
    track_graphs: bool = False,
) -> None:
    """Run ``wd init`` inside a child that has ``.git/`` but no discover.yaml.

    Per-child errors are recorded on *result* and do not abort the run;
    the recurse step will still inspect the child by its ledger status.
    Also seeds ``.weld/.gitignore`` if missing (idempotent), so a fresh
    child does not show its generated weld state as untracked git noise.
    """
    weld_dir = child_root / ".weld"
    if _child_has_discover_yaml(child_root):
        write_weld_gitignore(
            weld_dir, ignore_all=ignore_all, track_graphs=track_graphs,
        )
        return
    output = weld_dir / "discover.yaml"
    try:
        wrote = _root_init(child_root, output, force=False)
    except Exception as exc:  # noqa: BLE001 -- per-child isolation
        result.errors.append(f"init {name}: {type(exc).__name__}: {exc}")
        return
    if wrote:
        result.children_initialized.append(name)
    write_weld_gitignore(
        weld_dir, ignore_all=ignore_all, track_graphs=track_graphs,
    )


def _write_workspaces_yaml(
    output: Path,
    children: list[ChildEntry],
    max_depth: int,
    *,
    exclude_paths: list[str] | None = None,
    respect_gitignore: bool = False,
    cross_repo_strategies: list[str] | None = None,
) -> None:
    """Write a deterministic ``workspaces.yaml`` from the merged child set.

    Distinct from :func:`weld.init_workspace.init_workspace`, which only
    writes when the FS scan is non-empty. The bootstrap merge path may
    have a non-empty merged set composed entirely of yaml-listed children
    that the FS scan cannot see (gitignore / max_depth / excluded dirs);
    we still want to persist that set so step 4's loader sees a stable
    registry. ``exclude_paths`` and ``cross_repo_strategies`` are
    preserved verbatim so a rewrite never silently drops user
    configuration that the merge step did not touch.
    """
    from weld.workspace import dump_workspaces_yaml

    scan = ScanConfig(max_depth=max_depth)
    if exclude_paths is not None:
        scan.exclude_paths = list(exclude_paths)
    scan.respect_gitignore = respect_gitignore
    cfg = WorkspaceConfig(
        scan=scan,
        children=list(children),
        cross_repo_strategies=list(cross_repo_strategies or []),
    )
    dump_workspaces_yaml(cfg, output)


def _persisted_scan_fields(
    root: Path,
    cli_excludes: list[str] | None,
    cli_respect_gitignore: bool | None,
) -> tuple[list[str], bool, list[str]]:
    """Return ``(exclude_paths, respect_gitignore, strategies)`` to persist.

    Reads the current ``workspaces.yaml`` (when valid) and unions its
    ``scan.exclude_paths`` with the caller-supplied ``cli_excludes`` so a
    rewrite never silently drops user configuration. The opt-in gitignore
    flag and ``cross_repo_strategies`` are preserved from existing config.
    """
    from weld.init_workspace import safe_load_workspace_config

    cfg, _err = safe_load_workspace_config(root)
    merged: list[str] = list(cli_excludes or [])
    respect_gitignore = bool(cli_respect_gitignore)
    strategies: list[str] = []
    if cfg is not None:
        for item in cfg.scan.exclude_paths:
            if item not in merged:
                merged.append(item)
        respect_gitignore = respect_gitignore or cfg.scan.respect_gitignore
        strategies = list(cfg.cross_repo_strategies)
    return merged, respect_gitignore, strategies


def bootstrap_workspace(
    root: Path | str,
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
    exclude_paths: list[str] | None = None,
    respect_gitignore: bool | None = None,
    ignore_all: bool = False,
    track_graphs: bool = False,
) -> BootstrapResult:
    """Run the 5-step polyrepo bootstrap sequence on *root* and return a summary.

    Parameters
    ----------
    root:
        Workspace root directory. Must exist.
    max_depth:
        Maximum directory depth for the nested-repo scan, mirroring the
        ``--max-depth`` flag on ``wd init``.
    exclude_paths:
        Caller-supplied scan exclusions (typically ``--exclude-path`` from
        the CLI). Combined with any ``scan.exclude_paths`` already in the
        existing ``workspaces.yaml`` and persisted into the rewritten yaml
        so subsequent runs stay excluded without re-passing the flag.
    respect_gitignore:
        When ``True``, skip scan-only child repos masked by Git standard
        ignore rules and persist ``scan.respect_gitignore: true``. ``None``
        preserves any setting already present in ``workspaces.yaml``.
    ignore_all:
        When ``True``, the per-child and root ``.weld/.gitignore`` files
        ignore every weld file (``*`` / ``!.gitignore``). Mutually
        exclusive with ``track_graphs``. See :mod:`weld._gitignore_writer`.
    track_graphs:
        When ``True``, the per-child and root ``.weld/.gitignore`` files
        track the canonical graphs (``graph.json`` + ``agent-graph.json``)
        in addition to config. Default ignores generated graphs.
        Mutually exclusive with ``ignore_all``.

    Returns
    -------
    BootstrapResult
        Structured record of what ran and the resulting workspace shape.
        Callers render ``result.summary_lines()`` for human output or
        inspect the individual list fields for programmatic use.
    """
    # Local imports avoid a circular dependency: ``discover`` imports from
    # ``federation_root`` and ``workspace_state``; this module imports
    # ``discover`` for recurse. Importing at call time keeps module-load
    # order clean whichever one is imported first.
    from weld._discover_recurse import recurse_children
    from weld.federation_root import build_root_meta_graph
    from weld.serializer import dumps_graph
    from weld.workspace_state import WorkspaceLock

    root_path = Path(root).resolve()
    if not root_path.is_dir():
        raise FileNotFoundError(f"bootstrap root is not a directory: {root_path}")

    result = BootstrapResult()

    # Step 1: root init if needed.
    result.root_init_ran = _run_root_init(root_path)
    write_weld_gitignore(
        root_path / ".weld",
        ignore_all=ignore_all,
        track_graphs=track_graphs,
    )

    # Step 2: unified federation predicate.
    # ``merge_yaml_and_scan_children`` is the single source of truth: yaml
    # is authoritative when present, FS scan augments. This matches
    # ``wd discover``'s config-based federation predicate
    # (``load_workspace_config(root_path) is not None``) so a polyrepo
    # root cannot misroute to single-service when the yaml lists children
    # that the FS scan misses (gitignore, max_depth, _BUILTIN_EXCLUDE_DIRS).
    workspaces_yaml = root_path / ".weld" / "workspaces.yaml"
    merged = merge_yaml_and_scan_children(
        root_path,
        max_depth=max_depth,
        exclude_paths=exclude_paths,
        respect_gitignore=respect_gitignore,
    )
    if merged.yaml_error is not None:
        result.errors.append(merged.yaml_error)
    result.yaml_listed_but_missing = list(merged.yaml_listed_but_missing)
    result.excluded_by_gitignore = list(merged.excluded_by_gitignore)
    result.excluded_by_invalid_name = list(merged.excluded_by_invalid_name)
    result.skipped_by_gitignore = list(merged.skipped_by_gitignore)
    children = merged.children
    result.children_discovered = sorted(c.name for c in children)

    if not children:
        # Single-repo root: nothing further to do. Root init has already
        # run above if it was needed. Callers treat an empty
        # children_discovered list as "non-polyrepo".
        return result

    # Surface yaml-listed-but-missing children in errors so callers see
    # them without inspecting the dedicated field.
    for name in merged.yaml_listed_but_missing:
        result.errors.append(
            f"workspaces.yaml lists child {name!r} whose path does not "
            f"resolve under the workspace root",
        )

    # Compute the persisted scan/strategy fields once: union of CLI-supplied
    # exclusions and the existing yaml's exclusions, plus any pre-existing
    # cross_repo_strategies. Both are preserved verbatim across rewrites.
    (
        persisted_exclude_paths,
        persisted_respect_gitignore,
        persisted_strategies,
    ) = _persisted_scan_fields(
        root_path, exclude_paths, respect_gitignore,
    )

    # Persist the effective set to workspaces.yaml so step 4's loader sees
    # the merged registry. Three cases land here:
    #   (a) yaml absent -> write from the merged set (which is scan-only).
    #   (b) yaml present + scan augments -> rewrite to capture new children.
    #   (c) yaml corrupt -> rewrite from the merged set (scan only) so the
    #       loader downstream can succeed.
    if not workspaces_yaml.is_file():
        _write_workspaces_yaml(
            workspaces_yaml, children, max_depth,
            exclude_paths=persisted_exclude_paths,
            respect_gitignore=persisted_respect_gitignore,
            cross_repo_strategies=persisted_strategies,
        )
        result.workspace_yaml_written = True
    else:
        persisted_names: set[str] = set()
        persisted_excludes_existing: list[str] | None = None
        persisted: WorkspaceConfig | None = None
        if merged.yaml_error is None:
            persisted = load_workspace_config(root_path)
            if persisted is not None:
                persisted_names = {entry.name for entry in persisted.children}
                persisted_excludes_existing = list(persisted.scan.exclude_paths)
        merged_names = {c.name for c in children}
        excludes_changed = (
            persisted_excludes_existing is not None
            and persisted_excludes_existing != persisted_exclude_paths
        )
        respect_changed = (
            persisted is not None
            and persisted.scan.respect_gitignore != persisted_respect_gitignore
        )
        if (
            merged.yaml_error is not None
            or merged_names != persisted_names
            or excludes_changed
            or respect_changed
        ):
            _write_workspaces_yaml(
                workspaces_yaml, children, max_depth,
                exclude_paths=persisted_exclude_paths,
                respect_gitignore=persisted_respect_gitignore,
                cross_repo_strategies=persisted_strategies,
            )
            result.workspace_yaml_written = True

    # Step 3: per-child init for children lacking discover.yaml. Iterates
    # the merged set so yaml-only children (outside FS scan reach) are
    # initialised when their paths exist on disk; missing paths are
    # skipped and logged.
    for child in children:
        child_root = root_path / child.path
        if not child_root.is_dir():
            continue
        _init_child(
            child_root,
            result,
            child.name,
            ignore_all=ignore_all,
            track_graphs=track_graphs,
        )

    # Step 4 + 5: recurse-discover then rebuild root meta-graph + ledger
    # inside the workspace lock. This matches the ordering used by
    # ``wd discover --recurse`` so crash-safety invariants from ADR 0011
    # section 8 carry over.
    config = load_workspace_config(root_path)
    if config is None:
        # init_workspace() returned False without writing (or the file was
        # removed mid-run). Record an error and bail without attempting to
        # discover/write a meta-graph for a non-existent registry.
        result.errors.append(
            "workspaces.yaml missing after scan; bootstrap cannot recurse",
        )
        return result

    with WorkspaceLock(root_path):
        state = build_workspace_state(root_path, config)
        recurse_outcome = recurse_children(
            root_path, config, state, incremental=False,
        )
        result.children_recursed = sorted(recurse_outcome.discovered)
        # Mirror per-child recurse failures into result.errors so
        # programmatic callers see them (the docstring contract on
        # children_recursed requires this). Sorted for deterministic
        # output.
        for name in sorted(recurse_outcome.errors):
            result.errors.append(f"recurse {name}: {recurse_outcome.errors[name]}")
        # Rebuild ledger AFTER recurse so federation_root sees fresh state.
        state = build_workspace_state(root_path, config)
        graph = build_root_meta_graph(root_path, config, state)
        atomic_write_text(
            root_path / ".weld" / "graph.json", dumps_graph(graph),
        )
        save_workspace_state(root_path, state)

    result.children_present = sorted(
        name for name, entry in state.children.items()
        if entry.status == "present"
    )

    # Emit a user-visible hint when the bootstrap could not reach its
    # nominal goal so operators do not have to parse the JSON summary.
    missing = [
        name for name in result.children_discovered
        if name not in result.children_present
    ]
    if missing:
        print(
            "[weld] bootstrap: children not present after run: "
            + ", ".join(missing),
            file=sys.stderr,
        )

    return result
