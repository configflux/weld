"""Strategy: optional libclang-driven C++ semantic layer (ADR 0057 § Wave 3).

This strategy is **opt-in**: it stays dormant unless all three preconditions
are met (ADR 0057 § Wave 3):

1. The ``[cpp-libclang]`` extra is installed (``import clang.cindex``
   succeeds).
2. A ``compile_commands.json`` is found in the repo root or a
   conventional build directory.
3. ``WELD_CPP_LIBCLANG=1`` is set in the environment.

When dormant, ``extract()`` returns an empty :class:`StrategyResult`.
This is the safe default: discovery is always idempotent and never
crashes when libclang is missing.

When active, the strategy runs **after** the tree-sitter and CMake
strategies and produces:

- ``file --defines_macro--> macro``: preprocessor definitions.
- ``macro --expands_to--> symbol``: macro expansions to callables.
- ``template_definition --instantiated_by--> file``: template-call
  sites.
- Upgraded ``calls`` edges from tree-sitter's ``inferred`` heuristics
  to ``definite`` ground truth, with ``provenance: libclang`` stamped.

Precedence rule: when both tree-sitter and libclang produce edges for
the same ``(from, to, type)``, libclang wins on
``confidence``/``provenance``. We implement this by mutating the
existing edge in place rather than appending a duplicate.

The dispatch module is intentionally thin -- the bulk of the work
lives in three private siblings (``_cpp_libclang_macros``,
``_cpp_libclang_templates``, ``_cpp_libclang_calls``) plus the
compile-database parser (``_cpp_libclang_db``). Each sibling is well
under the 400-line cap; the dispatch module sits at ~150 lines.

Security posture: every libclang import is wrapped in a broad
``Exception`` guard because libclang is a native binding and a broken
install may raise ``OSError`` or ``RuntimeError`` rather than
``ImportError``. The compile-database parser bounds its read size and
entry count so a malformed or hostile database cannot starve discovery.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from weld.strategies._cpp_libclang_db import (
    CompileEntry,
    covered_files,
    find_compile_db,
    is_libclang_active,
    parse_entries,
)
from weld.strategies._helpers import StrategyResult

STRATEGY: str = "cpp_libclang"

#: Maximum number of translation units we will index per discovery run.
#: A real codebase has thousands; this bound stops a generated database
#: with millions of entries from running unbounded.
_MAX_TUS: int = 10_000

#: clang.TranslationUnit.PARSE_DETAILED_PROCESSING_RECORD is required to
#: see ``MACRO_DEFINITION`` and ``MACRO_INSTANTIATION`` cursors. The
#: value below mirrors the libclang constant so we never depend on the
#: enum being importable at module load.
_PARSE_DETAILED_PROCESSING_RECORD: int = 0x01


def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Run the libclang index over the repo's compile database.

    Returns an empty result when libclang is not active. Otherwise
    walks each TU and emits the macro/template/call edges described in
    the module docstring.
    """
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    active, _reason = is_libclang_active(root)
    if not active:
        return StrategyResult(nodes, edges, discovered_from)

    db_path = find_compile_db(root)
    if db_path is None:
        # Cannot happen given is_libclang_active above, but the guard
        # is cheap and the type checker prefers it.
        return StrategyResult(nodes, edges, discovered_from)

    entries = parse_entries(db_path, root=root)
    if not entries:
        return StrategyResult(nodes, edges, discovered_from)

    # The libclang import sits behind the active-check guard so the
    # native binding is only loaded when we have decided to use it.
    cindex_module = _import_cindex()
    if cindex_module is None:
        return StrategyResult(nodes, edges, discovered_from)

    # The dispatcher mutates the shared *edges* list directly. The
    # *context* edge list lives in tree-sitter's outputs which are
    # merged by the discover orchestrator before our strategy runs.
    upstream_edges = context.get("upstream_edges") if isinstance(context, dict) else None
    target_edges_for_upgrade = upstream_edges if isinstance(upstream_edges, list) else edges

    for entry in entries[:_MAX_TUS]:
        if not entry.file_rel:
            continue
        _process_one_tu(
            cindex_module=cindex_module,
            entry=entry,
            root=root,
            nodes=nodes,
            edges=edges,
            edges_to_upgrade=target_edges_for_upgrade,
        )
        discovered_from.append(entry.file_rel)

    # Add a ``provenance: libclang`` stamp to nodes we minted so the
    # capability matrix and the doctor's ``--cpp`` report can show the
    # libclang path was exercised.
    for node in nodes.values():
        props = node.setdefault("props", {})
        props.setdefault("provenance", "libclang")

    _stamp_coverage(nodes, db_path, entries, root)
    return StrategyResult(nodes, edges, discovered_from)


def _process_one_tu(
    *,
    cindex_module: Any,
    entry: CompileEntry,
    root: Path,
    nodes: dict[str, dict],
    edges: list[dict],
    edges_to_upgrade: list[dict],
) -> None:
    """Parse one TU with libclang and dispatch to per-aspect helpers.

    Errors raised by libclang (malformed TU, missing header, etc.) are
    swallowed: one bad TU should not poison the whole pass. The
    swallow is intentional and narrow -- it covers any failure of the
    native binding for *this* TU only.
    """
    from weld.strategies._cpp_libclang_calls import upgrade_unresolved_calls
    from weld.strategies._cpp_libclang_macros import walk_translation_unit as walk_macros
    from weld.strategies._cpp_libclang_templates import walk_translation_unit as walk_templates

    try:
        index = cindex_module.Index.create()
        # Prefer the binding's own constant when exposed; fall back to
        # the libclang value 0x01 (CXTranslationUnit_DetailedPreprocessingRecord)
        # when the Python wrapper does not surface it. Either form is
        # required to see ``MACRO_DEFINITION`` / ``MACRO_INSTANTIATION``
        # cursors during the walk.
        options = getattr(
            getattr(cindex_module, "TranslationUnit", None),
            "PARSE_DETAILED_PROCESSING_RECORD",
            _PARSE_DETAILED_PROCESSING_RECORD,
        )
        tu = index.parse(
            entry.file_abs,
            args=list(entry.arguments[1:]),  # drop argv[0] (compiler name)
            options=options,
        )
    except Exception:  # noqa: BLE001 -- native binding may raise OSError, etc.
        return

    try:
        walk_macros(cindex_module, tu, root=root, nodes=nodes, edges=edges)
        walk_templates(cindex_module, tu, root=root, nodes=nodes, edges=edges)
        upgrade_unresolved_calls(
            cindex_module, tu, root=root, edges=edges_to_upgrade,
        )
    except Exception:  # noqa: BLE001 -- defensive against native crashes
        return


def _import_cindex() -> Any | None:
    """Import ``clang.cindex`` defensively. Returns the module or None."""
    try:
        import clang.cindex as cindex  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001 -- native binding errors are not ImportError
        return None
    return cindex


def _stamp_coverage(
    nodes: dict[str, dict],
    db_path: Path,
    entries: list[CompileEntry],
    root: Path,
) -> None:
    """Mint a ``compile-db`` summary node so doctor/coverage can find it.

    The node is informational; it does not appear in the validate
    contract today (treated as ``concept``). It carries:

    - ``file``: repo-relative path to the database.
    - ``entries``: count of well-formed entries.
    - ``covered``: deduped count of repo-relative covered files.

    Consumers (``wd doctor --cpp``) read this node when present and
    fall back to direct filesystem scanning when absent.
    """
    try:
        rel = db_path.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        rel = db_path.name
    nid = "concept:cpp:compile-db"
    nodes.setdefault(
        nid,
        {
            "type": "concept",
            "label": "C++ compile database",
            "props": {
                "name": "compile_commands.json",
                "file": rel,
                "entries": len(entries),
                "covered_files": len(covered_files(entries)),
                "source_strategy": STRATEGY,
                "authority": "canonical",
                "confidence": "definite",
                "roles": ["config"],
                "provenance": "libclang",
            },
        },
    )


__all__ = ["STRATEGY", "extract"]
