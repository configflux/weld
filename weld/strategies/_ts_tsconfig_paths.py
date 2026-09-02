"""``tsconfig`` path aliases: the other first-party spelling (ADR 0142 D3).

``import { greeting } from "@/lib/greeting"`` is what every ``create-next-app``
scaffold writes, and it is not a package. It is ``src/lib/greeting`` spelled
through the ``compilerOptions.paths`` map in the app's own ``tsconfig.json``.
Before this module weld read that file nowhere in discovery, so the specifier
reached the closure as a bare name and came back as an *external* package node
-- an outright claim that first-party code lives outside the repository.

Two properties make an alias different from a workspace member name and are
why this is a separate map rather than more entries in that one:

* **It is scoped.** ``@/*`` means ``apps/web/src/*`` inside ``apps/web`` and
  ``apps/admin/src/*`` inside ``apps/admin``. A monorepo with two Next.js apps
  has two live definitions of the same spelling, so the map has to be chosen
  by *who is importing*, never registered globally. Nearest ``tsconfig`` wins.
* **It is a pattern language.** ``paths`` keys may carry one ``*``, several may
  match one specifier, and TypeScript settles the tie by the longest literal
  prefix -- with an exact key beating every wildcard.

Scope is deliberately one file: ``extends`` chains are not followed, so a
monorepo that keeps its aliases in a shared base config resolves nothing here
rather than resolving something from a file this module never validated. That
is a recorded limitation with a follow-up, not an oversight.

**Untrusted input**, exactly as in :mod:`weld.strategies._ts_workspace_members`:
config files are read with a size cap and parsed as data, pattern counts are
bounded, and every substituted target is confined under *root* before it is
allowed to name a file.
"""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any, NamedTuple

from weld.strategies._ts_module_files import (
    clean_relative,
    join_relative,
    read_config_text,
    resolve_module_file,
)

#: Config filenames that may carry a ``paths`` map, in the order a directory
#: is asked. ``jsconfig.json`` is the JavaScript spelling of the same file and
#: carries the same aliases for a Next.js app written in JS.
CONFIG_FILENAMES: tuple[str, ...] = ("tsconfig.json", "jsconfig.json")

#: Most ``paths`` keys one config may contribute. Real configs declare a
#: handful; the bound is here so a hostile config cannot make every import in
#: the repository pay for thousands of pattern comparisons.
MAX_PATTERNS = 256

#: Most target paths one pattern may offer. TypeScript itself tries them in
#: order and takes the first that resolves.
MAX_TARGETS = 16


class AliasMap(NamedTuple):
    """The ``paths`` map of one config, ready to match against."""

    #: Repo-relative POSIX directory targets resolve against (``baseUrl``
    #: applied when the config declares one, else the config's own directory
    #: -- which is what TypeScript does for a ``paths`` map without a
    #: ``baseUrl``).
    base: str
    #: ``(pattern, targets)`` pairs, exact keys first and wildcards after them
    #: in longest-literal-prefix order, so the first match is the winner.
    patterns: tuple[tuple[str, tuple[str, ...]], ...]


def strip_jsonc(text: str) -> str:
    """Return *text* with JSONC comments and trailing commas removed.

    Walks the text once tracking whether it is inside a string literal, so a
    ``//`` or ``/*`` that is part of a *value* (``"@/*": ["src/*"]`` carries
    neither, but a path value easily could) survives. Escapes inside strings
    are honoured, which is what keeps a trailing ``\\\\`` from swallowing the
    closing quote.

    The trailing-comma rule runs inside that same walk rather than as a regex
    over the result, and the difference is not cosmetic: a value containing
    ``", }"`` matches such a regex, and a pass that rewrote it would quietly
    change the config's data while returning something that still parses.
    """
    out: list[str] = []
    index, length, in_string = 0, len(text), False
    while index < length:
        char = text[index]
        if in_string:
            out.append(char)
            if char == "\\" and index + 1 < length:
                out.append(text[index + 1])
                index += 2
                continue
            if char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            out.append(char)
            index += 1
            continue
        if char == "/" and index + 1 < length and text[index + 1] == "/":
            while index < length and text[index] != "\n":
                index += 1
            continue
        if char == "/" and index + 1 < length and text[index + 1] == "*":
            end = text.find("*/", index + 2)
            index = length if end == -1 else end + 2
            continue
        if char in "}]":
            _drop_trailing_comma(out)
        out.append(char)
        index += 1
    return "".join(out)


def _drop_trailing_comma(out: list[str]) -> None:
    """Remove a comma that a closing brace or bracket is about to orphan."""
    cut = len(out)
    while cut and out[cut - 1].isspace():
        cut -= 1
    if cut and out[cut - 1] == ",":
        del out[cut - 1]


def read_config(path: Path) -> dict[str, Any]:
    """Return *path* parsed as a JSON object, tolerating JSONC, or ``{}``.

    Strict JSON is tried first so the overwhelmingly common comment-free file
    costs nothing extra; only a parse failure pays for the scan. Total on every
    input a repository can present: missing, oversized, undecodable, malformed,
    or a top-level non-object all answer ``{}``.
    """
    text = read_config_text(path)
    if not text:
        return {}
    for candidate in (text, strip_jsonc(text)):
        try:
            data: Any = json.loads(candidate)
        except (json.JSONDecodeError, ValueError, RecursionError):
            continue
        return data if isinstance(data, dict) else {}
    return {}


def alias_map(config_dir: str, config: dict[str, Any]) -> AliasMap | None:
    """Build the :class:`AliasMap` *config* declares, or ``None``.

    ``None`` -- not an empty map -- when the config declares no usable
    ``paths``, because the caller keeps walking upward in that case rather
    than concluding that this directory's config is the answer.
    """
    options = config.get("compilerOptions")
    if not isinstance(options, dict):
        return None
    raw_paths = options.get("paths")
    if not isinstance(raw_paths, dict) or not raw_paths:
        return None

    base_url = options.get("baseUrl")
    base = clean_relative(config_dir)
    if isinstance(base_url, str) and base_url:
        joined = join_relative(base, base_url)
        if joined is None:
            return None  # a baseUrl climbing out of the repo is refused whole
        base = joined

    patterns: list[tuple[str, tuple[str, ...]]] = []
    for pattern, targets in list(raw_paths.items())[:MAX_PATTERNS]:
        if not isinstance(pattern, str) or pattern.count("*") > 1:
            continue
        if isinstance(targets, str):
            targets = [targets]
        if not isinstance(targets, list):
            continue
        cleaned = tuple(
            target for target in targets[:MAX_TARGETS]
            if isinstance(target, str) and target
        )
        if cleaned:
            patterns.append((pattern, cleaned))
    if not patterns:
        return None
    patterns.sort(key=_pattern_rank)
    return AliasMap(base=base, patterns=tuple(patterns))


def nearest_alias_map(
    root: Path, importer: str, cache: dict[str, AliasMap | None],
) -> AliasMap | None:
    """The alias map in scope for the file *importer*, or ``None``.

    Walks from the importing file's own directory up to the repository root,
    taking the first config that declares a usable ``paths`` map. A config
    with no ``paths`` does not stop the walk: it is a real and common shape
    (a per-package ``tsconfig.json`` that only sets ``include``), and treating
    it as the answer would hide the app-level map above it.

    *cache* is keyed by directory and shared across the run, so a repository
    with a thousand files under one app reads its ``tsconfig.json`` once. The
    walk needs no depth bound of its own: it is bounded by the importing
    file's own path, and every rung is memoised.
    """
    directory = clean_relative(PurePosixPath(importer).parent.as_posix())
    parts = [part for part in directory.split("/") if part] if directory else []
    for depth in range(len(parts), -1, -1):
        current = "/".join(parts[:depth])
        if current not in cache:
            cache[current] = _load_alias_map(root, current)
        found = cache[current]
        if found is not None:
            return found
    return None


def alias_targets(aliases: AliasMap, specifier: str) -> list[str]:
    """Repo-relative module paths *specifier* names through *aliases*.

    Empty when no pattern matches. The first matching pattern wins -- the
    order :func:`alias_map` sorted them into is TypeScript's own tie-break --
    and its target list is returned in the order the config wrote it.
    """
    if not specifier:
        return []
    for pattern, targets in aliases.patterns:
        substitution = _match(pattern, specifier)
        if substitution is None:
            continue
        resolved: list[str] = []
        for target in targets:
            path = target.replace("*", substitution, 1) if "*" in target else target
            joined = join_relative(aliases.base, path)
            if joined and joined not in resolved:
                resolved.append(joined)
        return resolved
    return []


def resolve_alias(
    root: Path, aliases: AliasMap, specifier: str,
) -> str:
    """The repo-relative file *specifier* names through *aliases*, or ``""``."""
    for candidate in alias_targets(aliases, specifier):
        resolved = resolve_module_file(root, candidate)
        if resolved:
            return resolved
    return ""


def _load_alias_map(root: Path, directory: str) -> AliasMap | None:
    base = root / directory if directory else root
    for filename in CONFIG_FILENAMES:
        found = alias_map(directory, read_config(base / filename))
        if found is not None:
            return found
    return None


def _match(pattern: str, specifier: str) -> str | None:
    """What ``*`` stands for when *pattern* matches, or ``None``.

    An exact (starless) pattern matches only itself and substitutes the empty
    string, which is why the return type distinguishes ``""`` from ``None``.
    """
    if "*" not in pattern:
        return "" if pattern == specifier else None
    prefix, _, suffix = pattern.partition("*")
    if len(specifier) < len(prefix) + len(suffix):
        return None
    if not specifier.startswith(prefix) or not specifier.endswith(suffix):
        return None
    return specifier[len(prefix):len(specifier) - len(suffix)] if suffix else \
        specifier[len(prefix):]


def _pattern_rank(entry: tuple[str, tuple[str, ...]]) -> tuple[int, int, str]:
    """Sort key putting exact keys first, then the longest literal prefix."""
    pattern = entry[0]
    prefix = pattern.partition("*")[0]
    return (1 if "*" in pattern else 0, -len(prefix), pattern)


__all__ = [
    "CONFIG_FILENAMES",
    "MAX_PATTERNS",
    "MAX_TARGETS",
    "AliasMap",
    "alias_map",
    "alias_targets",
    "nearest_alias_map",
    "read_config",
    "resolve_alias",
    "strip_jsonc",
]
