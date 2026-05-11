"""Compile-database (``compile_commands.json``) discovery and parsing.

Extracted from :mod:`weld.strategies.cpp_libclang` so the public
dispatch module stays under the 400-line cap (ADR 0057 § Line-count
discipline).

The parser is deliberately defensive:

- Bounded read size (``_MAX_DB_BYTES``). A pathological multi-GB
  ``compile_commands.json`` would otherwise let an untrusted repo
  starve the discovery process. The bound is generous enough for
  every real-world database we have seen and small enough to refuse
  a fork-bomb-shaped payload.
- Tolerant entry validation. Missing or malformed entries are
  *dropped*, not fatal; the strategy must remain dormant-friendly so a
  partially-broken database does not crash discovery.
- Read-only. We never write the database; the only write is the
  documentation stub the discover CLI offers via
  ``--emit-compile-db-stub`` (which lives in :mod:`weld.discover`).

The active path (libclang index walk) lives in
:mod:`weld.strategies._cpp_libclang_macros`,
:mod:`weld.strategies._cpp_libclang_templates`, and
:mod:`weld.strategies._cpp_libclang_calls` and only runs when
:func:`is_libclang_active` returns True.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

#: Repo-relative candidate locations for ``compile_commands.json``.
#: Most CMake projects emit it into ``build/``; Bazel-with-Hedron emits
#: into the repo root. The order is deterministic so the strategy's
#: dormant decision is reproducible.
DEFAULT_DB_CANDIDATES: tuple[str, ...] = (
    "compile_commands.json",
    "build/compile_commands.json",
    "out/compile_commands.json",
    ".build/compile_commands.json",
)

#: Environment variable that gates the libclang path entirely. Without
#: this opt-in the strategy stays dormant even when the bindings and
#: the database are present, matching ADR 0057 § Wave 3's "explicit
#: opt-in to avoid surprise heavy-dep activation" rule.
ENABLE_ENV_VAR: str = "WELD_CPP_LIBCLANG"

#: Maximum bytes we will read from ``compile_commands.json`` before
#: refusing the database. Real-world databases for million-line
#: codebases are well under 100MB; the bound below stops a malicious or
#: corrupted file from starving discovery.
_MAX_DB_BYTES: int = 50 * 1024 * 1024  # 50 MiB

#: Maximum number of entries we will keep from the database. Bounds the
#: working set for downstream passes. A database with more entries gets
#: truncated and the strategy still operates on the first N.
_MAX_DB_ENTRIES: int = 250_000


@dataclass(frozen=True)
class CompileEntry:
    """One ``compile_commands.json`` entry, normalised.

    The raw shape has ``directory`` / ``file`` plus *either* ``command``
    (a single shell string) *or* ``arguments`` (a pre-split list). We
    normalise both into ``arguments`` to keep downstream passes simple.
    Empty or malformed entries are filtered out by :func:`parse_entries`
    so callers never see them.
    """

    file_abs: str
    file_rel: str
    directory: str
    arguments: tuple[str, ...]


def is_libclang_available() -> bool:
    """Return True when ``clang.cindex`` is importable.

    The import is wrapped in a broad ``Exception`` catch because libclang
    is a native binding -- a broken install (missing shared library,
    version skew) typically raises something more exotic than
    ``ImportError`` at import time.
    """
    try:
        import clang.cindex  # noqa: F401  -- import side-effect probe only
    except Exception:  # noqa: BLE001 -- native deps may raise OSError, etc.
        return False
    return True


def env_enabled() -> bool:
    """Return True when the opt-in env var is set to ``"1"``."""
    return os.environ.get(ENABLE_ENV_VAR, "").strip() == "1"


def find_compile_db(root: Path) -> Path | None:
    """Return the path of ``compile_commands.json`` if found, else None.

    Scans :data:`DEFAULT_DB_CANDIDATES` in order. The first existing
    file wins; we do not walk arbitrarily deep because real databases
    live near the project root by convention.
    """
    for rel in DEFAULT_DB_CANDIDATES:
        candidate = root / rel
        if candidate.is_file():
            return candidate
    return None


def is_libclang_active(root: Path) -> tuple[bool, str]:
    """Return ``(active, reason)`` describing whether libclang should run.

    The tuple's ``reason`` is a short, stable string we surface in
    ``wd doctor --cpp`` so users can tell *why* the strategy is dormant
    without reading source. Order of checks matches ADR 0057 § Wave 3:

    1. extra installed
    2. compile_commands.json present
    3. env var explicitly set

    All three are required; any failure returns ``(False, <reason>)``.
    """
    if not is_libclang_available():
        return False, "libclang extra not installed"
    if find_compile_db(root) is None:
        return False, "compile_commands.json not found"
    if not env_enabled():
        return False, f"{ENABLE_ENV_VAR} not set"
    return True, "active"


def parse_entries(
    db_path: Path,
    *,
    root: Path,
) -> list[CompileEntry]:
    """Return the well-formed entries from a compile-database file.

    Args:
        db_path: Absolute path to ``compile_commands.json``.
        root: Repository root. Used to compute ``file_rel`` for graph
            ID minting.

    Returns:
        A list of :class:`CompileEntry`. Malformed entries are silently
        dropped; the total list is truncated to :data:`_MAX_DB_ENTRIES`.
        Returns an empty list on any read or parse failure.
    """
    if not db_path.is_file():
        return []
    try:
        size = db_path.stat().st_size
    except OSError:
        return []
    if size > _MAX_DB_BYTES:
        return []
    try:
        raw = db_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(data, list):
        return []

    entries: list[CompileEntry] = []
    for raw_entry in data[:_MAX_DB_ENTRIES]:
        entry = _normalise_entry(raw_entry, root)
        if entry is not None:
            entries.append(entry)
    return entries


def _normalise_entry(raw: object, root: Path) -> CompileEntry | None:
    """Convert one raw dict into a :class:`CompileEntry` or None.

    Returns None for any of:
    - non-dict raw entry,
    - missing or empty ``file`` / ``directory``,
    - missing ``command`` and ``arguments``,
    - non-string types where strings are required.
    """
    if not isinstance(raw, dict):
        return None
    file_field = raw.get("file")
    directory_field = raw.get("directory")
    if not isinstance(file_field, str) or not file_field:
        return None
    if not isinstance(directory_field, str) or not directory_field:
        return None

    arguments = _coerce_arguments(raw)
    if not arguments:
        return None

    # Resolve relative ``file`` against ``directory`` so absolute paths
    # are stable regardless of which working dir invoked discovery.
    file_path = Path(file_field)
    if not file_path.is_absolute():
        file_path = Path(directory_field) / file_field
    # Best-effort relativise to the repo root. Files outside the root
    # are kept with an empty ``file_rel`` so callers know they cannot
    # mint a project-local ``file:`` node ID for them.
    try:
        rel = file_path.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        rel = ""
    return CompileEntry(
        file_abs=str(file_path),
        file_rel=rel,
        directory=directory_field,
        arguments=arguments,
    )


def _coerce_arguments(raw: dict) -> tuple[str, ...]:
    """Return the entry's argv as a tuple of strings.

    Prefers the pre-split ``arguments`` list when present; falls back
    to a naive whitespace split of ``command`` when only that form is
    available. Real compile-db emitters now mostly emit ``arguments``
    so the split path is rare.
    """
    args = raw.get("arguments")
    if isinstance(args, list):
        coerced = [a for a in args if isinstance(a, str)]
        return tuple(coerced)
    command = raw.get("command")
    if isinstance(command, str) and command:
        # Crude split is fine here: we only use this for coverage
        # reporting (counting files), not to actually invoke clang.
        return tuple(command.split())
    return ()


def covered_files(entries: Iterable[CompileEntry]) -> frozenset[str]:
    """Return the set of ``file_rel`` covered by the database."""
    return frozenset(e.file_rel for e in entries if e.file_rel)


__all__ = [
    "CompileEntry",
    "DEFAULT_DB_CANDIDATES",
    "ENABLE_ENV_VAR",
    "covered_files",
    "env_enabled",
    "find_compile_db",
    "is_libclang_active",
    "is_libclang_available",
    "parse_entries",
]
