"""Macro extraction for the libclang-driven C++ semantic layer (ADR 0057 § Wave 3).

Tree-sitter cannot follow ``#define FOO bar`` -> ``bar()``: the
preprocessor is its own language. libclang's translation-unit cursor
walk exposes ``MACRO_DEFINITION`` and ``MACRO_INSTANTIATION`` cursors
which let us mint two kinds of edges:

- ``file:<rel> --defines_macro--> macro:<name>``
  One per ``#define`` *defined inside the translation unit*. We ignore
  macros defined in system headers (``-isystem``) because they would
  flood the graph and add no signal for the user's code.

- ``macro:<name> --expands_to--> symbol:<callee>``
  One per ``MACRO_INSTANTIATION`` cursor whose expansion contains a
  callable identifier. The mapping is best-effort: libclang exposes
  the *spelling* of the macro and the cursor's expansion location, but
  it does not always tell us *what* the expansion resolves to. When
  the resolution is unambiguous (the macro expands to a single callee
  in the same TU) we emit the edge with ``confidence: definite``;
  otherwise we omit it.

The module is imported only after :func:`_cpp_libclang_db.is_libclang_active`
returns True, so the ``clang.cindex`` import below is reached only on
the active path. The top-level guard in :mod:`weld.strategies.cpp_libclang`
catches any import failure and degrades to dormant.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from weld._node_ids import file_id

STRATEGY: str = "cpp_libclang"

#: Cap on how many distinct macro nodes we mint per discovery run.
#: Real codebases rarely define more than a few thousand macros; the
#: cap prevents a pathological generated header from ballooning the
#: graph.
_MAX_MACROS: int = 50_000

#: Cap on how many ``expands_to`` edges we emit. Bounded by the same
#: reasoning as above.
_MAX_EXPANSIONS: int = 100_000


def macro_id(name: str) -> str:
    """Return the canonical ``macro:`` node ID for a preprocessor macro."""
    # We deliberately do NOT lowercase the name: ``BOOST_PP_STRINGIZE`` and
    # ``boost_pp_stringize`` are different macros in C++.
    return f"macro:{name}"


def walk_translation_unit(
    cindex_module: Any,
    tu: Any,
    *,
    root: Path,
    nodes: dict[str, dict],
    edges: list[dict],
) -> int:
    """Walk *tu* and emit macro defines/expansions. Returns edges appended.

    Args:
        cindex_module: The imported ``clang.cindex`` module. Passed in
            so this module has zero import-time dep on libclang.
        tu: A ``clang.cindex.TranslationUnit`` instance.
        root: Repo root for ``file_rel`` minting.
        nodes: Mutable graph nodes dict.
        edges: Mutable graph edges list.

    Returns:
        Number of edges appended for telemetry / tests.
    """
    macros_seen = 0
    expansions = 0
    appended = 0

    CursorKind = cindex_module.CursorKind
    for cursor in tu.cursor.walk_preorder():
        kind = cursor.kind
        if kind == CursorKind.MACRO_DEFINITION:
            if macros_seen >= _MAX_MACROS:
                continue
            if _emit_define(cursor, root, nodes, edges):
                appended += 1
                macros_seen += 1
        elif kind == CursorKind.MACRO_INSTANTIATION:
            if expansions >= _MAX_EXPANSIONS:
                continue
            if _emit_expansion(cursor, nodes, edges):
                appended += 1
                expansions += 1
    return appended


def _emit_define(
    cursor: Any,
    root: Path,
    nodes: dict[str, dict],
    edges: list[dict],
) -> bool:
    """Emit a ``file --defines_macro--> macro`` edge. Returns True on emit."""
    name = cursor.spelling or ""
    if not name:
        return False
    location = cursor.location
    if location is None or location.file is None:
        return False
    file_name = str(location.file)
    # Skip macros from outside the repo (system headers, toolchain).
    try:
        rel = Path(file_name).resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return False
    nid_file = file_id(rel)
    nid_macro = macro_id(name)
    nodes.setdefault(
        nid_macro,
        {
            "type": "macro",
            "label": name,
            "props": {
                "name": name,
                "file": rel,
                "source_strategy": STRATEGY,
                "authority": "canonical",
                "confidence": "definite",
                "roles": ["implementation"],
            },
        },
    )
    edges.append(
        {
            "from": nid_file,
            "to": nid_macro,
            "type": "defines_macro",
            "props": {
                "source_strategy": STRATEGY,
                "confidence": "definite",
                "file": rel,
            },
        }
    )
    return True


def _emit_expansion(
    cursor: Any,
    nodes: dict[str, dict],
    edges: list[dict],
) -> bool:
    """Emit a ``macro --expands_to--> symbol`` edge when resolvable.

    libclang exposes the macro's spelling at the instantiation site.
    Without a definite expansion target (``cursor.referenced``) we skip
    the edge -- guessing here would flood the graph with low-signal
    noise.
    """
    name = cursor.spelling or ""
    if not name:
        return False
    referenced = getattr(cursor, "referenced", None)
    if referenced is None:
        return False
    target_name = getattr(referenced, "spelling", "") or ""
    if not target_name or target_name == name:
        return False
    nid_macro = macro_id(name)
    nid_symbol = f"symbol:{target_name}"
    # The macro node may not exist yet (definition in a different TU);
    # mint a placeholder so the edge is well-formed.
    nodes.setdefault(
        nid_macro,
        {
            "type": "macro",
            "label": name,
            "props": {
                "name": name,
                "source_strategy": STRATEGY,
                "authority": "derived",
                "confidence": "inferred",
                "roles": ["implementation"],
            },
        },
    )
    edges.append(
        {
            "from": nid_macro,
            "to": nid_symbol,
            "type": "expands_to",
            "props": {
                "source_strategy": STRATEGY,
                "confidence": "definite",
            },
        }
    )
    return True


__all__ = [
    "STRATEGY",
    "macro_id",
    "walk_translation_unit",
]
