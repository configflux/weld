"""npm workspace members: a package *name* that is first-party (ADR 0142 D3).

``import { formatPrice } from "@acme/shared"`` in a workspaces monorepo is not
a dependency on a published package. It is a reference to a sibling directory
in the same repository, resolved by npm through a symlink it plants in
``node_modules``. Weld saw only the specifier, found no manifest entry for it,
and minted ``package:typescript:acme-shared`` -- an *external* claim about code
sitting three directories away. That is the TypeScript edition of field-eval
finding N4, and ADR 0142 D3 is the decision to stop making it.

This module answers the one question that makes the claim avoidable: **which
package names does this repository declare for itself, and where does each
one's code start?** The answer comes from the root ``package.json``'s
``workspaces`` field -- npm's array form and yarn's ``{"packages": [...]}``
form -- expanded through the house glob resolver, then read back out of each
member's own manifest.

Scope is one repository. A dependency on a package another *repo* produces is
the cross-repo package graph's question (ADR 0142 D5), not this one.

**Untrusted input.** Every byte here comes from manifests in the repository
being discovered. Nothing is executed, imported or interpolated: manifests are
read with a size cap and parsed as plain JSON, glob patterns that are absolute
or climb out with ``..`` are refused before expansion, member counts are
bounded, and every resolved path is confined under *root* by
:mod:`weld.strategies._ts_module_files`. Expansion itself goes through
:func:`weld.strategies._glob_resolve.resolve_glob`, which prunes
``node_modules``, never follows symlinks, and applies the repo-boundary filter
-- so a vendored copy of someone else's workspace cannot register names here.
"""

from __future__ import annotations

import fnmatch
import json
from pathlib import Path, PurePosixPath
from typing import Any, NamedTuple

from weld.strategies._glob_resolve import resolve_glob
from weld.strategies._ts_module_files import (
    clean_relative,
    read_config_text,
    resolve_module_file,
)

#: The one glob this module walks. Every declared workspace pattern is then
#: matched against the *directories* it found, rather than each pattern being
#: walked on its own -- which is one walk instead of N, memoised alongside the
#: identical walk the ``manifest`` strategy already does, and immune to
#: ``weld.glob_match._walk_one`` answering nothing for a wildcard in a
#: directory segment (``apps/*/package.json``), the defect filed as its own bd
#: issue by the change that added this module.
_MANIFEST_GLOB = "**/package.json"

#: Bounds on how much a root manifest may ask for. Both are far above what a
#: real monorepo declares (the largest public ones run to a few hundred
#: members) and exist so a manifest cannot turn discovery into a walk.
MAX_WORKSPACE_PATTERNS = 64
MAX_MEMBERS = 1024

#: Deepest workspace pattern honoured. ``packages/*/*`` is already unusual;
#: this exists so a pattern cannot be arbitrarily long, and pairs with the
#: single-``**`` rule in :func:`_is_bounded`.
MAX_PATTERN_SEGMENTS = 16

#: ``package.json`` keys that name the module a bare import of this package
#: lands on, in the order Node and TypeScript consult them. ``exports`` is
#: read separately because its value is a conditional map rather than a path.
_ENTRY_KEYS: tuple[str, ...] = ("types", "typings", "main", "module")

#: Conditions inside ``exports["."]`` that name a path, best first. ``types``
#: outranks the runtime conditions because in a source checkout it is the one
#: that points at TypeScript rather than at a build output.
_EXPORT_CONDITIONS: tuple[str, ...] = (
    "types", "import", "module", "default", "require", "node",
)

#: Where a member's code starts when its manifest names no entry point at all
#: -- or names one that is a build artefact absent from the checkout, which is
#: the ``"main": "dist/index.js"`` case every TypeScript monorepo has.
_ENTRY_FALLBACKS: tuple[str, ...] = ("index", "src/index", "lib/index")


class WorkspaceMember(NamedTuple):
    """One package this repository declares for itself."""

    #: The declared npm name, e.g. ``@acme/shared``.
    name: str
    #: Repo-relative POSIX directory holding the member, no trailing slash.
    directory: str
    #: Repo-relative POSIX file a bare import of :attr:`name` lands on, or
    #: ``""`` when the checkout holds no readable entry point.
    entry: str


def read_manifest(path: Path) -> dict[str, Any]:
    """Return *path* parsed as a JSON object, or ``{}``.

    Total: a missing file, an oversized one, undecodable bytes, malformed
    JSON and a top-level non-object all answer ``{}``. Callers read manifests
    from the repository under discovery, so every one of those is an input a
    stranger controls rather than an error condition worth raising on.

    Strict JSON, deliberately: npm's own ``package.json`` contract is strict,
    and the JSONC tolerance ``tsconfig.json`` needs
    (:func:`weld.strategies._ts_tsconfig_paths.read_config`) would accept a
    manifest here that npm itself would reject.
    """
    try:
        data: Any = json.loads(read_config_text(path) or "null")
    except (json.JSONDecodeError, ValueError, RecursionError):
        return {}
    return data if isinstance(data, dict) else {}


def workspace_patterns(manifest: dict[str, Any]) -> list[str]:
    """Return the workspace globs *manifest* declares, cleaned and bounded.

    Both shapes in the wild are read: npm's ``"workspaces": ["packages/*"]``
    and yarn's ``"workspaces": {"packages": ["packages/*"]}``. A pattern that
    is absolute or contains a ``..`` segment is dropped rather than expanded
    -- a root manifest has no business naming anything outside its own tree,
    and refusing here means the glob resolver is never handed one.
    """
    raw = manifest.get("workspaces")
    if isinstance(raw, dict):
        raw = raw.get("packages")
    if not isinstance(raw, list):
        return []
    patterns: list[str] = []
    for entry in raw:
        if not isinstance(entry, str):
            continue
        cleaned = clean_relative(entry.rstrip("/"))
        if not cleaned or cleaned in patterns or not _is_bounded(cleaned):
            continue
        patterns.append(cleaned)
        if len(patterns) >= MAX_WORKSPACE_PATTERNS:
            break
    return patterns


def _is_bounded(pattern: str) -> bool:
    """Refuse a pattern whose matching cost is not linear in the path.

    :func:`matches_pattern` resolves each ``**`` by trying every split point,
    so *k* of them cost O(n^k) against a path of depth *n*. One is what a
    workspace declaration ever needs (``packages/**``); more than one is a
    manifest asking discovery to do exponential work on its behalf, and this
    reads manifests from repositories nobody vetted. The segment count is
    bounded for the same reason and far above any real layout.
    """
    segments = [part for part in pattern.split("/") if part]
    return len(segments) <= MAX_PATTERN_SEGMENTS and segments.count("**") <= 1


def matches_pattern(directory: str, pattern: str) -> bool:
    """Does the repo-relative *directory* satisfy a workspace *pattern*?

    npm's own semantics: the pattern is matched **segment by segment**, so
    ``packages/*`` names the direct children of ``packages/`` and not their
    descendants. That distinction is the whole point -- a nested package
    inside a member is that member's business, not a workspace of its own --
    and it is why this is a hand-written matcher rather than an ``fnmatch``
    over the joined path, where ``*`` would happily eat a separator.

    ``**`` is honoured as "zero or more segments" for the yarn-style
    ``packages/**`` spelling; every other segment goes through
    ``fnmatch.fnmatchcase``, which gives ``*``, ``?`` and ``[...]`` their
    usual meanings within one name.
    """
    return _match_segments(
        [part for part in directory.split("/") if part],
        [part for part in pattern.split("/") if part],
    )


def _match_segments(parts: list[str], globs: list[str]) -> bool:
    if not globs:
        return not parts
    head, rest = globs[0], globs[1:]
    if head == "**":
        return any(
            _match_segments(parts[index:], rest)
            for index in range(len(parts) + 1)
        )
    if not parts:
        return False
    return fnmatch.fnmatchcase(parts[0], head) and _match_segments(parts[1:], rest)


def member_entry(root: Path, directory: str, manifest: dict[str, Any]) -> str:
    """Return the file a bare import of this member lands on, or ``""``.

    Reads ``exports["."]``, then ``types`` / ``typings`` / ``main`` /
    ``module``, then falls back to the conventional ``index`` locations. The
    fallback is not politeness: a TypeScript workspace routinely publishes
    ``"main": "dist/index.js"``, and in a source checkout that path does not
    exist, so an entry-point rule that stopped at the declaration would bind
    nothing for exactly the repositories this fix is for.
    """
    for candidate in (*_declared_entries(manifest), *_ENTRY_FALLBACKS):
        resolved = resolve_module_file(root, f"{directory}/{candidate}")
        if resolved:
            return resolved
    return ""


def load_workspace_members(root: Path) -> dict[str, WorkspaceMember]:
    """Map every declared workspace member name to where its code lives.

    Empty for a repository that is not a workspaces monorepo, which is the
    common case and costs one ``package.json`` read. Members are keyed by
    their declared name; a name declared twice keeps the first member in
    sorted directory order, so the map does not depend on walk order.
    """
    manifest = read_manifest(root / "package.json")
    patterns = workspace_patterns(manifest)
    if not patterns:
        return {}

    candidates: list[tuple[str, Path]] = []
    for manifest_path in resolve_glob(root, _MANIFEST_GLOB):
        directory = _relative_directory(root, manifest_path)
        if not directory:
            continue  # the root manifest itself is not one of its own members
        if any(matches_pattern(directory, pattern) for pattern in patterns):
            candidates.append((directory, manifest_path))

    members: dict[str, WorkspaceMember] = {}
    for directory, manifest_path in sorted(candidates)[:MAX_MEMBERS]:
        member_manifest = read_manifest(manifest_path)
        name = member_manifest.get("name")
        if not isinstance(name, str) or not name or name in members:
            continue
        members[name] = WorkspaceMember(
            name=name,
            directory=directory,
            entry=member_entry(root, directory, member_manifest),
        )
    return members


def _declared_entries(manifest: dict[str, Any]) -> list[str]:
    """Entry-point paths *manifest* declares, best first."""
    entries: list[str] = []
    for candidate in (*_export_entries(manifest), *(
        manifest.get(key) for key in _ENTRY_KEYS
    )):
        if isinstance(candidate, str) and candidate and candidate not in entries:
            entries.append(candidate)
    return entries


def _export_entries(manifest: dict[str, Any]) -> list[str]:
    """The paths ``exports["."]`` names, best condition first.

    Only the root (``"."``) entry is read. Sub-path export maps are a
    published-package concern and resolving them would mean re-implementing
    the conditional-exports algorithm; a sub-path import that this map does
    not answer falls through to the member directory instead, which is where
    the file actually is in a source checkout.
    """
    exports = manifest.get("exports")
    if isinstance(exports, str):
        return [exports]
    if not isinstance(exports, dict):
        return []
    root_entry = exports.get(".", exports)
    if isinstance(root_entry, str):
        return [root_entry]
    if not isinstance(root_entry, dict):
        return []
    return [
        value
        for condition in _EXPORT_CONDITIONS
        if isinstance(value := root_entry.get(condition), str) and value
    ]


def _relative_directory(root: Path, manifest_path: Path) -> str:
    """The member directory *manifest_path* sits in, repo-relative."""
    try:
        relative = manifest_path.parent.relative_to(root)
    except ValueError:  # pragma: no cover - resolve_glob answers under root
        return ""
    return clean_relative(PurePosixPath(relative).as_posix())


__all__ = [
    "MAX_MEMBERS",
    "MAX_PATTERN_SEGMENTS",
    "MAX_WORKSPACE_PATTERNS",
    "WorkspaceMember",
    "load_workspace_members",
    "matches_pattern",
    "member_entry",
    "read_manifest",
    "workspace_patterns",
]
