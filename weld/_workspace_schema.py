"""Workspace registry schema -- the dependency-free leaf under
``weld.workspace`` / ``weld.workspace_scan`` / ``weld.workspace_dump``.

Split out of :mod:`weld.workspace` (bd 5038-zw6w4, ADR 0130 disposition
#14): ``workspace.py`` defined the shared schema (constants, exception, and
dataclasses) and imported ``workspace_scan.py``/``workspace_dump.py`` back
at its own bottom for re-export (the "facade re-exports a sibling split out
for the line-count cap" shape), while ``workspace_scan.py`` needed seven of
those symbols (``DEFAULT_EXCLUDE_PATHS``, ``DEFAULT_MAX_DEPTH``,
``ChildEntry``, ``NestedRepoScanResult``, ``WorkspaceConfigError``,
``auto_derive_name``, ``auto_derive_tags``) back from ``workspace.py`` at
real top level, and ``workspace_dump.py`` needed ``WorkspaceConfig`` back
under a ``TYPE_CHECKING`` guard -- a real 3-member SCC composed of two
different edges (one load-bearing, one type-only but still graph-visible:
the static extractor walks into ``if TYPE_CHECKING:`` bodies the same way
it walks into deferred function-local imports, so neither guard hides the
edge from the cycle detector).

``WorkspaceConfig`` moves too, even though only ``workspace_scan.py``'s
seven symbols were strictly required to break its own edge: leaving
``WorkspaceConfig`` behind in ``workspace.py`` would still let
``workspace_dump.py``'s ``TYPE_CHECKING`` edge close a residual 2-member
loop on its own. It brings ``SCHEMA_VERSION``/``ScanConfig`` with it --
both are its own field defaults -- so it does not need to import them back
from ``workspace.py``, which would recreate the exact cycle this split
removes.

This module holds no import of :mod:`weld.workspace`,
:mod:`weld.workspace_scan`, or :mod:`weld.workspace_dump`, so nothing
importing it can cycle back. ``workspace.py`` imports from here and
re-exports everything for its existing public surface (``from
weld.workspace import ChildEntry`` and friends keep working unchanged);
``workspace_scan.py`` imports its seven symbols from here directly instead
of from ``workspace.py``, and ``workspace_dump.py``'s ``TYPE_CHECKING``
import of ``WorkspaceConfig`` points here too -- the two edges that broke
the cycle.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1
DEFAULT_MAX_DEPTH = 4
DEFAULT_EXCLUDE_PATHS: tuple[str, ...] = (".worktrees", "vendor")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class WorkspaceConfigError(ValueError):
    """Raised when ``workspaces.yaml`` is missing, malformed, or invalid."""


# ---------------------------------------------------------------------------
# Schema dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ScanConfig:
    max_depth: int = DEFAULT_MAX_DEPTH
    respect_gitignore: bool = False
    exclude_paths: list[str] = field(
        default_factory=lambda: list(DEFAULT_EXCLUDE_PATHS),
    )


@dataclass
class ChildEntry:
    name: str
    path: str
    tags: dict[str, str] = field(default_factory=dict)
    remote: str | None = None


@dataclass
class WorkspaceConfig:
    version: int = SCHEMA_VERSION
    scan: ScanConfig = field(default_factory=ScanConfig)
    children: list[ChildEntry] = field(default_factory=list)
    cross_repo_strategies: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class NestedRepoScanResult:
    children: list[ChildEntry]
    skipped_by_gitignore: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Auto-derivation helpers
# ---------------------------------------------------------------------------

def auto_derive_name(rel_path: str) -> str:
    """Return the child name implied by ``rel_path``.

    The default rule replaces path separators with ``-`` so that a child at
    ``services/api`` is named ``services-api``. Both POSIX and Windows-style
    separators are normalised for robustness against hand-written YAML.
    """
    normalised = rel_path.replace("\\", "/")
    parts = [p for p in normalised.split("/") if p and p != "."]
    return "-".join(parts)


def auto_derive_tags(rel_path: str) -> dict[str, str]:
    """Return auto-filled tag metadata implied by ``rel_path``.

    The immediate parent directory becomes ``category: <segment>``; deeper
    ancestors become ``category_<depth>: <segment>`` with ``depth`` counting
    from 2 at the grandparent. A child at the workspace root (single segment)
    gets no category tag at all -- there is no parent to infer one from.
    """
    normalised = rel_path.replace("\\", "/")
    parts = [p for p in normalised.split("/") if p and p != "."]
    if len(parts) < 2:
        return {}
    ancestors = parts[:-1]  # everything except the leaf
    # ancestors[-1] is the immediate parent -> "category"
    # ancestors[-2] is the grandparent    -> "category_2"
    # ancestors[-3] is one above          -> "category_3"
    tags: dict[str, str] = {"category": ancestors[-1]}
    for offset, segment in enumerate(reversed(ancestors[:-1]), start=2):
        tags[f"category_{offset}"] = segment
    return tags
