"""Polyrepo workspace registry: ``workspaces.yaml`` schema, loader, validator, scanner.

ADR 0011 describes the federation model: a workspace root may contain several
child git repositories, each owning its own ``.weld/`` directory. The root
registry enumerates those children and declares which cross-repo resolvers are
active.

This module is intentionally self-contained -- it depends only on the standard
library and the minimal YAML parser in ``weld._yaml``. It is safe to import
from ``weld.init`` without triggering any heavyweight dependency.

Public surface
--------------

* :class:`WorkspaceConfig`, :class:`ScanConfig`, :class:`ChildEntry` -- schema
  dataclasses.
* :class:`WorkspaceConfigError` -- validation and load errors.
* :func:`load_workspaces_yaml` -- parse a YAML file and return a validated
  :class:`WorkspaceConfig` (auto-derives missing names and tags).
* :func:`dump_workspaces_yaml` -- write a canonical, deterministic YAML file.
* :func:`validate_config` -- raise :class:`WorkspaceConfigError` if the config
  violates the schema rules (version, child name charset, uniqueness,
  relative-path constraint, known cross-repo strategies).
* :func:`scan_nested_repos` -- walk a directory tree looking for nested
  ``.git`` repositories, honouring ``max_depth`` and ``exclude_paths``;
  returns :class:`ChildEntry` objects with auto-derived names and tags.
* :func:`auto_derive_name`, :func:`auto_derive_tags` -- pure helpers exposed
  for tests and for callers that want to derive without a full scan.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from weld._yaml import parse_yaml

__all__ = [
    "ChildEntry",
    "ScanConfig",
    "WorkspaceConfig",
    "WorkspaceConfigError",
    "auto_derive_name",
    "auto_derive_tags",
    "dump_workspaces_yaml",
    "load_workspaces_yaml",
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

# Cross-repo resolver names accepted by the validator. This list grows as new
# resolvers land; unknown names are rejected at load time so typos in
# ``workspaces.yaml`` fail loudly rather than silently skipping a resolver.
KNOWN_CROSS_REPO_STRATEGIES: frozenset[str] = frozenset({
    "grpc_service_binding",
    "compose_topology",
    "service_graph",
})

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
    excludes_raw = block.get("exclude_paths", list(DEFAULT_EXCLUDE_PATHS))
    excludes = _as_list(excludes_raw, "scan.exclude_paths")
    exclude_paths = [str(x) for x in excludes]
    return ScanConfig(max_depth=max_depth, exclude_paths=exclude_paths)


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
# Dumper (canonical, deterministic)
# ---------------------------------------------------------------------------

def _yaml_scalar(value: object) -> str:
    """Emit a YAML scalar. Quotes strings that contain YAML-special chars."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    s = str(value)
    if s == "":
        return '""'
    # Characters that benefit from quoting in block scalars.
    unsafe = set(": #[]{},&*!|>'\"%@`")
    if any(c in s for c in unsafe) or s[0] in " -?":
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return s


def _emit_inline_list(items: list[str]) -> str:
    if not items:
        return "[]"
    return "[" + ", ".join(_yaml_scalar(x) for x in items) + "]"


def dump_workspaces_yaml(cfg: WorkspaceConfig, path: Path | str) -> None:
    """Write ``cfg`` to ``path`` as canonical, deterministic YAML.

    The output is the ground truth for any round-trip; the same input config
    always produces byte-identical output. Children are emitted in the order
    stored on the config (callers typically sort before dumping), and each
    child's ``tags`` are emitted in sorted key order so hand-reordering the
    config does not create spurious diffs.
    """
    lines: list[str] = []
    lines.append(f"version: {cfg.version}")
    lines.append("scan:")
    lines.append(f"  max_depth: {cfg.scan.max_depth}")
    lines.append(f"  exclude_paths: {_emit_inline_list(cfg.scan.exclude_paths)}")
    if cfg.children:
        lines.append("children:")
        for child in cfg.children:
            lines.append(f"  - name: {_yaml_scalar(child.name)}")
            lines.append(f"    path: {_yaml_scalar(child.path)}")
            if child.tags:
                lines.append("    tags:")
                for key in sorted(child.tags):
                    lines.append(f"      {key}: {_yaml_scalar(child.tags[key])}")
            if child.remote:
                lines.append(f"    remote: {_yaml_scalar(child.remote)}")
    else:
        lines.append("children: []")
    lines.append(f"cross_repo_strategies: {_emit_inline_list(cfg.cross_repo_strategies)}")
    text = "\n".join(lines) + "\n"
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")


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
# Nested-repo scanner
# ---------------------------------------------------------------------------

def _should_skip_dir(name: str, extra: frozenset[str]) -> bool:
    if name in _BUILTIN_EXCLUDE_DIRS:
        return True
    if name in extra:
        return True
    if name.startswith("bazel-"):
        return True
    return False


def _normalised_exclude_paths(
    root: Path, exclude_paths: list[str] | None,
) -> tuple[frozenset[str], frozenset[Path]]:
    """Split user-provided exclude_paths into directory names vs absolute paths.

    A bare name applies to any directory of that name; a path with separators
    is resolved against ``root``. Root ``.gitignore`` is NOT consulted (tracked issue):
    a nested ``.git`` is a workspace child by definition. Pass project-specific
    exclusions via ``exclude_paths``.
    """
    defaults = list(DEFAULT_EXCLUDE_PATHS)
    raw = defaults if exclude_paths is None else list(exclude_paths) + defaults
    names: set[str] = set()
    absolute: set[Path] = set()
    for item in raw:
        s = str(item).strip().replace("\\", "/")
        if not s:
            continue
        if "/" in s:
            absolute.add((root / s).resolve())
        else:
            names.add(s)
    return frozenset(names), frozenset(absolute)


def scan_nested_repos(
    root: Path | str,
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
    exclude_paths: list[str] | None = None,
) -> list[ChildEntry]:
    """Walk ``root`` looking for nested ``.git`` directories.

    Returns a list of :class:`ChildEntry` values sorted lexicographically by
    relative path. Traversal stops descending into any directory that is a
    git repo, so repos-inside-repos are not registered as siblings. The
    workspace root itself is never registered (a root-level ``.git`` is
    ignored so that the root of a mixed mono+polyrepo layout still reports
    its children correctly).
    """
    if max_depth < 1:
        raise WorkspaceConfigError(
            f"max_depth must be >= 1, got {max_depth}",
        )
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        raise WorkspaceConfigError(f"scan root is not a directory: {root_path}")

    exclude_names, exclude_abs = _normalised_exclude_paths(root_path, exclude_paths)
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
            if _should_skip_dir(entry, exclude_names):
                continue
            if sub.resolve() in exclude_abs:
                continue
            _walk(sub, depth + 1)

    _walk(root_path, 0)
    found.sort(key=lambda c: c.path)
    return found
