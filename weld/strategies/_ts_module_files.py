"""Which file on disk a Node module path names (ADR 0142 D3).

A TypeScript or JavaScript import spelling names a *module*, and the module
resolver -- not the import statement -- decides which file that is. Both
first-party spellings this repo now resolves (an npm workspace member name and
a ``tsconfig`` ``paths`` alias) end the same way: with an extensionless
repo-relative path that has to be turned into a file. ``@acme/shared`` reduces
to ``packages/shared/index``; ``@/lib/greeting`` reduces to
``apps/web/src/lib/greeting``. Neither is a path a reader typed, and neither
exists until an extension or an ``index`` file is found for it.

That one step lives here so the workspace map and the alias map cannot drift
on the extension list -- the failure this repo has paid for elsewhere
(fourteen private ``_resolve_glob`` copies, ADR 0112).

**This module touches the filesystem and nothing else.** It never parses, never
imports, never runs anything, and it never leaves *root*: a candidate is
refused before any syscall when its spelling is absolute or climbs out with
``..``, and refused again after the syscall when a symlink took it out anyway.
The paths it is asked about come from ``package.json`` and ``tsconfig.json``
files in the repository under discovery, which are untrusted input.

Reading one of those config files -- bounded, total, no parsing -- is the one
other thing here, and it is here so the manifest reader and the ``tsconfig``
reader cannot drift apart on the size cap or on which errors count as "absent".
"""

from __future__ import annotations

import os
from pathlib import Path

#: Largest config file this family will read. A ``package.json`` or a
#: ``tsconfig.json`` is a hand-written file of tens of lines; anything past
#: this is not one, and reading it would only buy a memory spike on a hostile
#: checkout.
MAX_CONFIG_BYTES = 1 << 20

#: Extensions a bare module path may acquire, in resolution order. TypeScript
#: sources outrank their compiled JavaScript so a checkout that also holds a
#: build output directory still binds to the source a reader edits; ``.d.ts``
#: comes last of the TS family because a declaration file describes a module
#: rather than defining it.
MODULE_EXTENSIONS: tuple[str, ...] = (
    ".ts", ".tsx", ".mts", ".cts", ".d.ts",
    ".js", ".jsx", ".mjs", ".cjs",
)

#: Filenames a *directory* resolves through, in order. Node's own rule is
#: ``index`` plus the extension list; the stem is separate from the extensions
#: because only the directory branch uses it.
_INDEX_STEM = "index"


def read_config_text(path: Path) -> str:
    """Return the text of a config file at *path*, or ``""``.

    Total on every input a repository can present: missing, a directory,
    oversized, unreadable and undecodable all answer ``""``. Callers read
    files a stranger wrote, so each of those is an input rather than an error
    condition worth raising on -- and an empty answer means the same thing to
    all of them (this repository declares nothing here).
    """
    try:
        if not path.is_file() or path.stat().st_size > MAX_CONFIG_BYTES:
            return ""
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, ValueError):
        return ""


def join_relative(base: str, path: str) -> str | None:
    """Join *path* onto repo-relative *base*, or ``None`` if it is refused.

    ``..`` is *resolved*, not refused: a ``tsconfig`` in ``apps/web`` routinely
    aliases ``@shared/*`` to ``../../packages/shared/src/*``, and refusing
    every ``..`` outright would leave exactly those monorepos unresolved. What
    is refused is a path that climbs *past* the repository root, an absolute
    path, and a Windows drive spelling -- each answered with ``None`` rather
    than an exception, before any syscall happens.

    ``None`` and ``""`` are different answers on purpose: ``""`` is the
    repository root, which a ``baseUrl`` of ``"../.."`` two directories down
    legitimately names, and which a refused path must not be mistaken for.
    Otherwise the result is a clean repo-relative POSIX path with ``.``
    segments and duplicate separators dropped, directly comparable with the
    ``props.file`` spellings the graph already holds.
    """
    text = (path or "").replace("\\", "/").strip()
    if not text or text.startswith("/") or ":" in text.split("/", 1)[0]:
        return None
    parts = [part for part in base.split("/") if part and part != "."]
    for part in text.split("/"):
        if not part or part == ".":
            continue
        if part == "..":
            if not parts:
                return None  # climbs out of the repository
            parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def clean_relative(path: str) -> str:
    """:func:`join_relative` from the root, with a refusal read as ``""``.

    The convenience spelling for every caller that has no use for the repo
    root as an answer -- a module path, a glob pattern, a member directory --
    and so needs only one empty value rather than two.
    """
    return join_relative("", path) or ""


def contained_path(root: Path, rel: str) -> Path | None:
    """Return the absolute path of *rel* under *root*, or ``None``.

    The second half of the confinement rule: :func:`clean_relative` refuses a
    spelling that climbs out, and this refuses a spelling that *resolves* out
    -- which is the symlink case, where ``packages/shared`` is a link to
    ``/etc``. ``os.path.realpath`` is used rather than ``Path.resolve`` only
    because it answers for a path that does not exist without raising on any
    supported version.
    """
    cleaned = clean_relative(rel)
    if not cleaned:
        return None
    candidate = root / cleaned
    real_root = os.path.realpath(root)
    real = os.path.realpath(candidate)
    if real != real_root and not real.startswith(real_root + os.sep):
        return None
    return candidate


def resolve_module_file(root: Path, rel: str) -> str:
    """Return the repo-relative file the module path *rel* names, or ``""``.

    Three readings, in Node's own order: the path as written (an entry point
    that already carries its extension, ``index.ts``), the path plus each
    extension in :data:`MODULE_EXTENSIONS`, then the path as a directory
    holding an ``index`` file. The first that is a readable file wins -- a
    directory never does, because every reading is settled by ``is_file``.

    Total by construction: an unreadable path, a refused spelling and a
    directory with no index all answer ``""``, so a caller never has to guard
    the call. The answer is a repo-relative POSIX path, which is the spelling
    ``props.file`` and the closure's path index both use.
    """
    cleaned = clean_relative(rel)
    if not cleaned:
        return ""
    absolute = contained_path(root, cleaned)
    if absolute is None:
        return ""
    for candidate_rel, candidate in _candidates(cleaned, absolute):
        try:
            if candidate.is_file():
                return candidate_rel
        except OSError:  # pragma: no cover - unreadable mount mid-walk
            continue
    return ""


def _candidates(cleaned: str, absolute: Path) -> list[tuple[str, Path]]:
    """``(repo-relative, absolute)`` readings of one module path, best first."""
    readings: list[tuple[str, Path]] = [(cleaned, absolute)]
    for ext in MODULE_EXTENSIONS:
        readings.append((f"{cleaned}{ext}", Path(f"{absolute}{ext}")))
    for ext in MODULE_EXTENSIONS:
        readings.append((
            f"{cleaned}/{_INDEX_STEM}{ext}",
            absolute / f"{_INDEX_STEM}{ext}",
        ))
    return readings


__all__ = [
    "MAX_CONFIG_BYTES",
    "MODULE_EXTENSIONS",
    "clean_relative",
    "contained_path",
    "join_relative",
    "read_config_text",
    "resolve_module_file",
]
