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
"""

from __future__ import annotations

from pathlib import Path

from weld.workspace import (
    DEFAULT_MAX_DEPTH,
    ChildEntry,
    ScanConfig,
    WorkspaceConfig,
    dump_workspaces_yaml,
    scan_nested_repos,
)

__all__ = ["discover_children", "init_workspace"]


def discover_children(
    root: Path | str,
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
    exclude_paths: list[str] | None = None,
) -> list[ChildEntry]:
    """Return :class:`ChildEntry` values for every nested git repo under ``root``.

    Thin wrapper over :func:`weld.workspace.scan_nested_repos` so callers can
    say "discover children" without knowing the scanner's function name.
    """
    return scan_nested_repos(
        root,
        max_depth=max_depth,
        exclude_paths=exclude_paths,
    )


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
        return False

    cfg = WorkspaceConfig(
        scan=ScanConfig(max_depth=max_depth),
        children=children,
    )
    dump_workspaces_yaml(cfg, out)
    return True
