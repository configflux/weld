"""Scaffold ``.weld/workspaces.yaml`` for polyrepo roots.

Thin wrappers over :mod:`weld.workspace` that the ``wd init`` CLI calls when
the user runs initialisation at a directory containing nested git
repositories. Split into its own module to keep ``weld.init`` focused on the
single-repo ``discover.yaml`` bootstrap flow.

Public surface
--------------
* :func:`discover_children` -- walk ``root`` for nested ``.git`` directories
  and return :class:`~weld.workspace.ChildEntry` values.
* :func:`init_workspace` -- discover children, write
  ``.weld/workspaces.yaml`` (honouring ``force``), return whether anything
  was written.
* :func:`safe_load_workspace_config` -- read ``workspaces.yaml`` if present,
  returning ``(config | None, error_message | None)``. Never raises on
  parser/validation errors -- corrupt yaml downgrades to a fall-back signal
  for the caller.
* :func:`merge_yaml_and_scan_children` -- the unified federation predicate
  used by ``wd workspace bootstrap`` and ``wd init`` at a polyrepo root:
  yaml is authoritative when present; FS scan augments. Returns the merged
  set plus diagnostics (yaml-only-missing, scan-only-but-gitignored,
  yaml-corrupt-error).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from weld._gitignore_scan import load_root_gitignore_dirs
from weld.workspace import (
    DEFAULT_MAX_DEPTH,
    NAME_PATTERN,
    ChildEntry,
    ScanConfig,
    WorkspaceConfig,
    WorkspaceConfigError,
    auto_derive_name,
    auto_derive_tags,
    dump_workspaces_yaml,
    scan_nested_repos,
)

__all__ = [
    "MergedChildren",
    "discover_children",
    "init_polyrepo_children",
    "init_workspace",
    "merge_yaml_and_scan_children",
    "safe_load_workspace_config",
]


def _partition_by_name_validity(
    entries: list[ChildEntry],
) -> tuple[list[ChildEntry], list[str]]:
    """Split scan entries into ``(valid, invalid_paths)`` by auto-derived name.

    Yaml-listed children stay subject to the strict validator at config
    load time; only scan-only entries are filtered here so a single stray
    nested repo with a dotted directory name does not abort the run.
    """
    kept: list[ChildEntry] = []
    invalid: list[str] = []
    for entry in entries:
        if NAME_PATTERN.match(entry.name):
            kept.append(entry)
        else:
            invalid.append(entry.path)
    return kept, invalid


def discover_children(
    root: Path | str,
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
    exclude_paths: list[str] | None = None,
) -> list[ChildEntry]:
    """Return :class:`ChildEntry` values for every nested git repo under ``root``.

    Wraps :func:`weld.workspace.scan_nested_repos` and silently filters
    scan-only entries whose auto-derived child name would fail the
    workspace name validator. Callers needing the skipped paths should
    use :func:`merge_yaml_and_scan_children`, which surfaces them on
    :class:`MergedChildren.excluded_by_invalid_name`.
    """
    entries = scan_nested_repos(
        root,
        max_depth=max_depth,
        exclude_paths=exclude_paths,
    )
    kept, _invalid = _partition_by_name_validity(entries)
    return kept


def init_workspace(
    root: Path | str,
    output: Path | str,
    *,
    force: bool = False,
    max_depth: int = DEFAULT_MAX_DEPTH,
    exclude_paths: list[str] | None = None,
) -> bool:
    """Scaffold ``workspaces.yaml`` at ``output`` if ``root`` has nested repos.

    Returns ``True`` when a file was written. Returns ``False`` -- without
    raising -- when:

    * no nested git repositories were found (nothing to register), or
    * ``output`` already exists and ``force`` is not set.

    Writes a :class:`WorkspaceConfig` with the discovered children, the
    caller-supplied ``scan.max_depth``, and the default exclude list. The file
    is deterministic: running on an unchanged tree twice with ``force=True``
    produces byte-identical output.
    """
    out = Path(output)
    if out.exists() and not force:
        return False

    children = discover_children(
        root,
        max_depth=max_depth,
        exclude_paths=exclude_paths,
    )
    if not children:
        # Linked-worktree fallback (tracked issue): a linked git worktree does
        # not contain copies of its sibling child repos -- those live only
        # at the main checkout. When main has a workspaces.yaml, mirror it
        # so federation discover can resolve each child via
        # resolve_child_root's worktree fallback (ADR 0028).
        from weld._git import git_main_checkout_path
        main_checkout = git_main_checkout_path(Path(root))
        if main_checkout is not None:
            main_yaml = main_checkout / ".weld" / "workspaces.yaml"
            if main_yaml.is_file():
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(
                    main_yaml.read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
                return True
        return False

    cfg = WorkspaceConfig(
        scan=ScanConfig(max_depth=max_depth),
        children=children,
    )
    dump_workspaces_yaml(cfg, out)
    return True


def safe_load_workspace_config(
    root: Path | str,
) -> tuple[WorkspaceConfig | None, str | None]:
    """Best-effort load of ``workspaces.yaml`` -- never raises.

    Returns ``(config, None)`` on success, ``(None, None)`` when the file is
    absent, and ``(None, "<reason>")`` when the file is present but invalid.
    The "yaml is corrupt -> fall back to FS scan" recovery path in
    :func:`merge_yaml_and_scan_children` depends on this never-raising
    contract; callers must surface the returned message in their result for
    operator visibility.
    """
    # Local import to avoid pulling workspace_state into discover.py via this
    # module's import graph at startup.
    from weld.workspace_state import find_workspaces_yaml, load_workspace_config

    config_path = find_workspaces_yaml(root)
    if config_path is None:
        return None, None
    try:
        cfg = load_workspace_config(root)
    except WorkspaceConfigError as exc:
        return None, f"workspaces.yaml: {exc}"
    except Exception as exc:  # noqa: BLE001 -- defensive; keep bootstrap alive
        return None, f"workspaces.yaml: {type(exc).__name__}: {exc}"
    if cfg is None:
        return None, None
    return cfg, None


@dataclass
class MergedChildren:
    """Outcome of :func:`merge_yaml_and_scan_children`.

    ``children`` is the effective merged set (yaml-authoritative, scan
    augments). ``yaml_listed_but_missing`` carries names whose declared
    path does not resolve on disk. ``excluded_by_gitignore`` carries
    scan-found names that the yaml does not list AND that root
    ``.gitignore`` masks (informational; they are NOT auto-added).
    ``excluded_by_invalid_name`` carries scan-found paths whose
    auto-derived name failed ``NAME_PATTERN`` and were therefore
    skipped. ``yaml_error`` is the reason a present yaml could not be
    parsed, or ``None`` when yaml was absent or valid.
    """

    children: list[ChildEntry]
    yaml_listed_but_missing: list[str]
    excluded_by_gitignore: list[str]
    yaml_error: str | None
    excluded_by_invalid_name: list[str] = field(default_factory=list)


def _entry_for_scan_path(rel_path: str) -> ChildEntry:
    return ChildEntry(
        name=auto_derive_name(rel_path),
        path=rel_path,
        tags=auto_derive_tags(rel_path),
    )


def merge_yaml_and_scan_children(
    root: Path | str,
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
    exclude_paths: list[str] | None = None,
) -> MergedChildren:
    """Unified federation predicate: yaml is authoritative; FS scan augments.

    Behaviour matrix (bd-...-9slg):

    * yaml present and valid -> children = yaml ++ scan-only entries.
      Yaml entries whose path does not exist on disk land in
      ``yaml_listed_but_missing`` but stay in ``children`` so the bootstrap
      can attempt per-child init in case the path materialises during
      recovery.
    * yaml present but corrupt -> children = scan only;
      ``yaml_error`` describes the parse failure for operator visibility.
    * yaml absent -> children = scan only.

    Scan-only directories that the root ``.gitignore`` masks are reported
    in ``excluded_by_gitignore`` as a diagnostic but never added to
    ``children`` (the yaml is the only authority for over-riding gitignore).

    The effective scan exclusion set is the union of the caller-supplied
    ``exclude_paths`` and any ``scan.exclude_paths`` configured in the
    existing ``workspaces.yaml``. Scan-only entries whose auto-derived
    name fails ``NAME_PATTERN`` are skipped and surfaced on
    ``excluded_by_invalid_name`` so a stray dotted nested repo cannot
    abort the bootstrap.
    """
    root_path = Path(root)
    cfg, yaml_error = safe_load_workspace_config(root_path)

    effective_exclude = list(exclude_paths or [])
    if cfg is not None:
        for item in cfg.scan.exclude_paths:
            if item not in effective_exclude:
                effective_exclude.append(item)

    raw_entries = scan_nested_repos(
        root_path,
        max_depth=max_depth,
        exclude_paths=effective_exclude or None,
    )
    scan_entries, invalid_scan_paths = _partition_by_name_validity(raw_entries)
    scan_by_path: dict[str, ChildEntry] = {e.path: e for e in scan_entries}

    merged_by_path: dict[str, ChildEntry] = {}
    yaml_missing: list[str] = []
    if cfg is not None:
        for child in cfg.children:
            merged_by_path[child.path] = child
            child_dir = root_path / child.path
            if not (child_dir.is_dir() and (child_dir / ".git").exists()):
                yaml_missing.append(child.name)

    # Augment with scan entries the yaml did not list.
    for path, entry in scan_by_path.items():
        merged_by_path.setdefault(path, entry)

    # Diagnostics: scan-found-and-yaml-listed paths covered above; the
    # reciprocal "scan would have found this if not gitignored" is
    # interesting only when the yaml lists the path -- the operator
    # explicitly opted in. We surface every yaml-listed path that the
    # gitignore would have masked.
    excluded: list[str] = []
    if cfg is not None:
        ignored_dirs = load_root_gitignore_dirs(root_path)
        for child in cfg.children:
            resolved = (root_path / child.path).resolve()
            if any(resolved == g or _is_under(resolved, g)
                   for g in ignored_dirs):
                excluded.append(child.name)

    children = sorted(merged_by_path.values(), key=lambda e: e.path)
    return MergedChildren(
        children=children,
        yaml_listed_but_missing=sorted(yaml_missing),
        excluded_by_gitignore=sorted(excluded),
        yaml_error=yaml_error,
        excluded_by_invalid_name=sorted(invalid_scan_paths),
    )


def _is_under(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def maybe_bootstrap_polyrepo(
    root: Path | str,
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> None:
    """Run full bootstrap_workspace if a workspaces.yaml exists at ``root``.

    Called from ``wd init`` so a polyrepo root materialises the per-child
    init + ledger + federated graph in one shot, instead of leaving the
    operator to also run ``wd workspace bootstrap``. Imports lazily because
    ``weld._workspace_bootstrap`` already imports ``weld.init`` (line 35),
    so a top-level import here would cycle. (tracked issue)
    """
    if not (Path(root) / ".weld" / "workspaces.yaml").exists():
        return
    from weld._workspace_bootstrap import bootstrap_workspace
    bootstrap_workspace(root, max_depth=max_depth)


def init_polyrepo_children(
    root: Path | str,
    *,
    force: bool = False,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> list[str]:
    """Run ``wd init`` inside every effective child of a polyrepo ``root``.

    Mirrors the per-child init step of ``wd workspace bootstrap`` so that
    ``wd init`` at a polyrepo root produces the same on-disk shape: every
    child has its own ``.weld/discover.yaml``. Returns the list of child
    relative paths whose init wrote a fresh discover.yaml. Per-child
    failures and missing paths are silent here -- the caller logs them via
    higher-level reporting; this function exists only to keep
    :mod:`weld.init` lean.
    """
    # Local import: weld.init imports this module, so we cannot import
    # it at module top-level without a circular dependency.
    from weld.init import init as _root_init

    merged = merge_yaml_and_scan_children(root, max_depth=max_depth)
    written: list[str] = []
    for child in merged.children:
        child_root = Path(root) / child.path
        if not child_root.is_dir():
            continue
        output = child_root / ".weld" / "discover.yaml"
        try:
            wrote = _root_init(child_root, output, force=force)
        except Exception:  # noqa: BLE001 -- per-child isolation
            continue
        if wrote:
            written.append(child.path)
    return written
