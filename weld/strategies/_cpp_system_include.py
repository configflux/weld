"""Toolchain-aware resolver for C++ angle-bracket ``<...>`` includes.

Companion helper for :mod:`weld.strategies.cpp_resolver`. The resolver
proper handles ``#include "foo.h"`` (string-literal) form by walking
the project tree. This module handles the ``<...>`` form by consulting
the host's known toolchain include roots (libstdc++, libc++, clang
builtins, system C headers) so a directive like ``<vector>`` resolves
to a real path on a typical Linux/macOS install. Once resolved, the
result feeds :func:`weld.strategies._cpp_origin.classify_resolved_include`
unchanged.

Implements the ADR 0042 §C++ follow-up: prior to this module only
the ``std::`` namespace heuristic flagged stdlib callees, leaving
unqualified callees of ``<vector>`` / ``<string>`` headers stuck on
``origin="unresolved"``.

The resolver is best-effort and read-only. It never invokes ``cc -E``
or any external tool (out of scope per the bd issue) and never returns
a path it has not actually stat'd on disk. Directory listings are
cached per-prefix in ``_SYSTEM_INCLUDE_DIR_CACHE`` so repeated probes
are O(1) after the first walk.
"""

from __future__ import annotations

from pathlib import Path

from weld.strategies._cpp_origin import STDLIB_INCLUDE_ROOTS

#: System C include prefixes consulted after every populated
#: ``STDLIB_INCLUDE_ROOTS`` entry has been tried. Covers plain C
#: system headers (``<unistd.h>``, ``<sys/stat.h>``) found in mixed
#: C/C++ trees. Anything resolved here lands in
#: ``classify_resolved_include``'s ``"external"`` bucket because these
#: roots are not under the C++ stdlib tree -- correct per ADR 0042.
_SYSTEM_C_INCLUDE_ROOTS: tuple[str, ...] = (
    "/usr/include/",
    "/usr/local/include/",
)

#: Cache: prefix string -> list of concrete header search directories.
#: Populated lazily on first probe and held for the process lifetime.
_SYSTEM_INCLUDE_DIR_CACHE: dict[str, list[Path]] = {}


def _enumerate_versioned_root(base: Path) -> list[Path]:
    """Return immediate subdirs of *base* (e.g. /usr/include/c++/13)."""
    out: list[Path] = []
    try:
        for child in sorted(base.iterdir()):
            if child.is_dir():
                out.append(child)
    except OSError:
        return []
    return out


def _enumerate_glob_prefix(parent: Path, base_name: str) -> list[Path]:
    """Return canonical libc++/clang/libstdc++ dirs under glob roots.

    For a prefix like ``/usr/lib/llvm-`` (parent ``/usr/lib``,
    base_name ``llvm-``) we keep entries whose name starts with
    ``base_name`` and descend into the conventional sub-layouts:

      * ``<match>/include/c++/<ver>``  (libc++)
      * ``<match>/include``            (gcc libstdc++ short form)
      * ``<match>/<triple>/include``   (gcc libstdc++ multi-arch form)
      * ``<match>/lib/clang/<ver>/include`` (clang builtins)
    """
    out: list[Path] = []
    try:
        children = sorted(parent.iterdir())
    except OSError:
        return []
    for child in children:
        if not child.name.startswith(base_name):
            continue
        if not child.is_dir():
            continue
        cpp_root = child / "include" / "c++"
        if cpp_root.is_dir():
            for ver in _enumerate_versioned_root(cpp_root):
                out.append(ver)
        triple_inc = child / "include"
        if triple_inc.is_dir():
            out.append(triple_inc)
        try:
            for triple_ver in sorted(child.iterdir()):
                if not triple_ver.is_dir():
                    continue
                deep_inc = triple_ver / "include"
                if deep_inc.is_dir():
                    out.append(deep_inc)
        except OSError:
            pass
        lib_clang = child / "lib" / "clang"
        if lib_clang.is_dir():
            for ver in _enumerate_versioned_root(lib_clang):
                deep_inc = ver / "include"
                if deep_inc.is_dir():
                    out.append(deep_inc)
    return out


def _system_include_dirs(prefix: str) -> list[Path]:
    """Return concrete header search directories rooted at *prefix*.

    Cached: first call walks the disk; subsequent calls return the
    pre-computed list. Returns an empty list when *prefix* (or any
    parent we would scan) is missing or unreadable.
    """
    cached = _SYSTEM_INCLUDE_DIR_CACHE.get(prefix)
    if cached is not None:
        return cached

    base = Path(prefix.rstrip("/")) if prefix.endswith("/") else Path(prefix)
    parent = base.parent
    base_name = base.name

    dirs: list[Path] = []
    try:
        if base.is_dir():
            # Versioned root (/usr/include/c++/, /usr/local/include/c++/, ...)
            # plus the literal system-C roots themselves.
            dirs.extend(_enumerate_versioned_root(base))
            for sysc in _SYSTEM_C_INCLUDE_ROOTS:
                if base == Path(sysc.rstrip("/")):
                    dirs.append(base)
                    break
        elif parent.is_dir() and base_name:
            dirs.extend(_enumerate_glob_prefix(parent, base_name))
    except OSError:
        dirs = []

    _SYSTEM_INCLUDE_DIR_CACHE[prefix] = dirs
    return dirs


def resolve_system_include(header_text: str) -> Path | None:
    """Resolve an angle-bracket ``<...>`` payload to a real header path.

    Args:
        header_text: The text *between* the angle brackets, e.g.
            ``"vector"`` or ``"sys/stat.h"``. Empty string returns None.

    Returns:
        Absolute :class:`pathlib.Path` to the resolved header, or None
        if no toolchain root on the host provides the header. Stdlib
        roots (``STDLIB_INCLUDE_ROOTS``) are tried first so a libstdc++
        ``vector`` always beats a same-named file under
        ``/usr/include/``.
    """
    if not header_text:
        return None
    for prefix in STDLIB_INCLUDE_ROOTS + _SYSTEM_C_INCLUDE_ROOTS:
        for inc_dir in _system_include_dirs(prefix):
            candidate = inc_dir / header_text
            try:
                if candidate.is_file():
                    return candidate
            except OSError:
                continue
    return None


__all__ = ["resolve_system_include"]
