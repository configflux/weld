"""Polyrepo workspace registry schema, validator, dumper, and scanner.

ADR 0011 defines the federation model: a workspace root enumerates child git
repositories and declares cross-repo resolvers. This module owns the YAML
contract plus auto-discovery helpers used by ``wd init`` and bootstrap.

The canonical YAML dumper lives in :mod:`weld.workspace_dump` and the
nested-repo filesystem scanner in :mod:`weld.workspace_scan`; both are
re-exported here so ``weld.workspace`` remains the single public facade.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from weld._yaml import parse_yaml

__all__ = [
    "ChildEntry",
    "NestedRepoScanResult",
    "ScanConfig",
    "WorkspaceConfig",
    "WorkspaceConfigError",
    "auto_derive_name",
    "auto_derive_tags",
    "dump_workspaces_yaml",
    "load_workspaces_yaml",
    "scan_nested_repos_with_diagnostics",
    "scan_nested_repos",
    "validate_config",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1
DEFAULT_MAX_DEPTH = 4
DEFAULT_EXCLUDE_PATHS: tuple[str, ...] = (".worktrees", "vendor")
NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
UNIT_SEPARATOR = "\x1f"

# Cross-repo resolver names accepted by the validator. Unknown names are
# rejected at load time so typos in ``workspaces.yaml`` fail loudly.
KNOWN_CROSS_REPO_STRATEGIES: frozenset[str] = frozenset({
    "compose_topology",
    "grpc_service_binding",
    "package_import_resolver",
    "service_graph",
})


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


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def _as_list(value: object, field_name: str) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    raise WorkspaceConfigError(f"{field_name} must be a list, got {type(value).__name__}")


def _as_dict(value: object, field_name: str) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    raise WorkspaceConfigError(f"{field_name} must be a mapping, got {type(value).__name__}")


def _parse_scan_block(raw: object) -> ScanConfig:
    if raw is None or raw == "":
        return ScanConfig()
    block = _as_dict(raw, "scan")
    max_depth = block.get("max_depth", DEFAULT_MAX_DEPTH)
    if not isinstance(max_depth, int) or isinstance(max_depth, bool):
        raise WorkspaceConfigError("scan.max_depth must be an integer")
    if max_depth < 1:
        raise WorkspaceConfigError(
            f"scan.max_depth must be >= 1, got {max_depth}",
        )
    respect_gitignore = block.get("respect_gitignore", False)
    if not isinstance(respect_gitignore, bool):
        raise WorkspaceConfigError("scan.respect_gitignore must be a boolean")
    excludes_raw = block.get("exclude_paths", list(DEFAULT_EXCLUDE_PATHS))
    excludes = _as_list(excludes_raw, "scan.exclude_paths")
    exclude_paths = [str(x) for x in excludes]
    return ScanConfig(
        max_depth=max_depth,
        respect_gitignore=respect_gitignore,
        exclude_paths=exclude_paths,
    )


def _parse_child(raw: object, index: int) -> ChildEntry:
    entry = _as_dict(raw, f"children[{index}]")
    if "path" not in entry:
        raise WorkspaceConfigError(
            f"children[{index}]: required field 'path' is missing",
        )
    path = str(entry["path"]).strip()
    if not path:
        raise WorkspaceConfigError(f"children[{index}]: 'path' must not be empty")
    name_raw = entry.get("name")
    name = str(name_raw).strip() if name_raw not in (None, "") else auto_derive_name(path)
    tags_raw = entry.get("tags")
    if tags_raw in (None, ""):
        tags = auto_derive_tags(path)
    else:
        tags = {str(k): str(v) for k, v in _as_dict(tags_raw, f"children[{index}].tags").items()}
    remote_raw = entry.get("remote")
    remote = str(remote_raw) if remote_raw not in (None, "") else None
    return ChildEntry(name=name, path=path, tags=tags, remote=remote)


def load_workspaces_yaml(path: Path | str) -> WorkspaceConfig:
    """Load and validate a ``workspaces.yaml`` file.

    Missing ``name``/``tags`` are auto-derived at load time so downstream
    callers can assume every :class:`ChildEntry` is fully populated.
    """
    p = Path(path)
    if not p.is_file():
        raise WorkspaceConfigError(f"workspaces.yaml not found: {p}")
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise WorkspaceConfigError(f"failed to read {p}: {exc}") from exc
    try:
        data = parse_yaml(text)
    except Exception as exc:  # parser raises generic errors; normalise
        raise WorkspaceConfigError(f"failed to parse {p}: {exc}") from exc
    if not isinstance(data, dict):
        raise WorkspaceConfigError(
            f"{p}: top-level must be a mapping, got {type(data).__name__}",
        )

    version = data.get("version", SCHEMA_VERSION)
    if isinstance(version, bool) or not isinstance(version, int):
        raise WorkspaceConfigError("version must be an integer")

    scan = _parse_scan_block(data.get("scan"))
    children_raw = _as_list(data.get("children"), "children")
    children = [_parse_child(c, i) for i, c in enumerate(children_raw)]
    strategies = [
        str(x)
        for x in _as_list(data.get("cross_repo_strategies"), "cross_repo_strategies")
    ]

    cfg = WorkspaceConfig(
        version=version,
        scan=scan,
        children=children,
        cross_repo_strategies=strategies,
    )
    validate_config(cfg)
    return cfg


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

def validate_config(cfg: WorkspaceConfig) -> None:
    """Raise :class:`WorkspaceConfigError` if ``cfg`` is invalid.

    Rules enforced:
    - ``version`` must equal :data:`SCHEMA_VERSION`.
    - Each child name matches ``^[A-Za-z0-9_-]+$`` and does not contain the
      ASCII Unit Separator (reserved as the namespace delimiter).
    - Child names are unique across the registry.
    - Child paths are relative (no leading ``/``) and contain no ``..`` segment.
    - Every cross-repo strategy is in :data:`KNOWN_CROSS_REPO_STRATEGIES`.
    """
    if cfg.version != SCHEMA_VERSION:
        raise WorkspaceConfigError(
            f"unsupported workspaces.yaml version: {cfg.version} "
            f"(this release supports version {SCHEMA_VERSION})",
        )

    if not isinstance(cfg.scan.max_depth, int) or cfg.scan.max_depth < 1:
        raise WorkspaceConfigError(
            f"scan.max_depth must be >= 1, got {cfg.scan.max_depth!r}",
        )
    if not isinstance(cfg.scan.respect_gitignore, bool):
        raise WorkspaceConfigError(
            "scan.respect_gitignore must be a boolean",
        )

    seen: dict[str, int] = {}
    for index, child in enumerate(cfg.children):
        _validate_child_name(child.name, index)
        _validate_child_path(child.path, index)
        if child.name in seen:
            raise WorkspaceConfigError(
                f"duplicate child name: {child.name!r} appears at "
                f"children[{seen[child.name]}] and children[{index}]",
            )
        seen[child.name] = index

    for strategy in cfg.cross_repo_strategies:
        if strategy not in KNOWN_CROSS_REPO_STRATEGIES:
            known = ", ".join(sorted(KNOWN_CROSS_REPO_STRATEGIES)) or "(none)"
            raise WorkspaceConfigError(
                f"unknown cross_repo_strategy: {strategy!r}. "
                f"Known strategies: {known}",
            )


def _validate_child_name(name: str, index: int) -> None:
    if not name:
        raise WorkspaceConfigError(
            f"children[{index}]: name must not be empty",
        )
    if UNIT_SEPARATOR in name:
        raise WorkspaceConfigError(
            f"children[{index}]: name {name!r} contains the reserved "
            "ASCII Unit Separator (0x1f); choose another name",
        )
    if not NAME_PATTERN.match(name):
        raise WorkspaceConfigError(
            f"children[{index}]: invalid character in name {name!r}; "
            "names must match ^[A-Za-z0-9_-]+$",
        )


def _validate_child_path(path: str, index: int) -> None:
    if not path:
        raise WorkspaceConfigError(
            f"children[{index}]: path must not be empty",
        )
    normalised = path.replace("\\", "/")
    if normalised.startswith("/"):
        raise WorkspaceConfigError(
            f"children[{index}]: path {path!r} must be relative, not absolute",
        )
    parts = [p for p in normalised.split("/") if p]
    if any(p == ".." for p in parts):
        raise WorkspaceConfigError(
            f"children[{index}]: path {path!r} must not contain '..' segments",
        )


# ---------------------------------------------------------------------------
# Re-exported facade members
# ---------------------------------------------------------------------------
#
# The dumper and the nested-repo scanner live in sibling modules to keep this
# file within the line-count cap. They depend on the schema/helpers defined
# above, so these imports sit at the bottom of the module (after every name
# they consume is bound) and are re-exported via ``__all__``. Importers keep
# using ``weld.workspace`` as the single public entry point.

from weld.workspace_dump import dump_workspaces_yaml  # noqa: E402
from weld.workspace_scan import (  # noqa: E402
    scan_nested_repos,
    scan_nested_repos_with_diagnostics,
)
