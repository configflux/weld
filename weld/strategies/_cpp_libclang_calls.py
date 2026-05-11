"""Cross-TU call resolution for the libclang C++ semantic layer (ADR 0057 § Wave 3).

Tree-sitter emits ``calls`` edges with ``confidence: inferred`` when it
cannot resolve the callee across translation units. libclang knows --
its index has compilation-context truth -- so when libclang can resolve
a callee, we *upgrade* the existing edge's confidence to ``definite``
and stamp a ``provenance: libclang`` marker so consumers can tell
which edges crossed the precedence boundary.

The precedence rule from ADR 0057:

    > Precedence rule: when both tree-sitter and libclang produce
    > edges for the same (from, to, type), libclang wins on
    > confidence/provenance (rationale: libclang has compilation-
    > context truth).

We do NOT *replace* the edge; we mutate it in place. Mutation preserves
edge ordering (the edge list is the persistence order of the strategy
that produced it) and keeps tree-sitter's stamping for unrelated props.

The dispatcher in :mod:`weld.strategies.cpp_libclang` calls
:func:`upgrade_unresolved_calls` once per translation unit after
:mod:`weld.strategies._cpp_libclang_macros` and
:mod:`weld.strategies._cpp_libclang_templates` have run. All three
modules share the same active-libclang precondition.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

STRATEGY: str = "cpp_libclang"

#: Sentinel destination for unresolved calls emitted by tree-sitter.
#: ``cpp_resolver`` rewrites the *successful* resolutions into
#: ``symbol:<callee>``; the leftovers carry the prefix below. libclang
#: is the second line of defence.
_UNRESOLVED_PREFIX: str = "symbol:unresolved:"


def upgrade_unresolved_calls(
    cindex_module: Any,
    tu: Any,
    *,
    root: Path,
    edges: list[dict],
) -> int:
    """Walk *tu* call expressions and upgrade matching tree-sitter edges.

    For every ``CALL_EXPR`` cursor whose ``referenced`` is a known
    function in the index, we look for a matching unresolved edge in
    *edges* and rewrite it:

    - ``to``: from ``symbol:unresolved:<name>`` to ``symbol:<qname>``.
    - ``props.confidence``: from ``inferred`` to ``definite``.
    - ``props.provenance``: stamped ``libclang``.
    - ``props.resolved``: set to True (matches cpp_resolver's
      convention so downstream consumers don't have to branch).

    Returns the number of edges mutated for telemetry / tests.
    """
    CursorKind = cindex_module.CursorKind
    # Build a quick lookup of unresolved edges by their dangling target's
    # bare-name suffix so we can match without scanning the whole list
    # per cursor.
    by_callee_name: dict[str, list[dict]] = {}
    for edge in edges:
        if edge.get("type") != "calls":
            continue
        props = edge.get("props") or {}
        if props.get("confidence") != "inferred":
            continue
        to_id = str(edge.get("to") or "")
        if not to_id.startswith(_UNRESOLVED_PREFIX):
            continue
        callee = to_id[len(_UNRESOLVED_PREFIX) :]
        if not callee:
            continue
        by_callee_name.setdefault(callee, []).append(edge)

    if not by_callee_name:
        return 0

    upgraded = 0
    seen_pairs: set[tuple[str, str]] = set()
    for cursor in tu.cursor.walk_preorder():
        if cursor.kind != CursorKind.CALL_EXPR:
            continue
        referenced = getattr(cursor, "referenced", None)
        if referenced is None:
            continue
        callee_name = getattr(referenced, "spelling", "") or ""
        if not callee_name or callee_name not in by_callee_name:
            continue
        qualified = _qualified_name(referenced)
        if not qualified:
            continue
        # Match on the (caller_file, callee_name) pair so we do not
        # upgrade unrelated edges that share the callee name.
        location = cursor.location
        rel = _rel_from_location(location, root)
        if rel is None:
            continue
        for edge in by_callee_name[callee_name]:
            edge_file = (edge.get("props") or {}).get("file")
            if edge_file and edge_file != rel:
                continue
            pair_key = (str(edge.get("from") or ""), qualified)
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            _upgrade_edge(edge, qualified)
            upgraded += 1
    return upgraded


def _upgrade_edge(edge: dict, qualified: str) -> None:
    """Mutate *edge* in place to reflect the libclang resolution."""
    edge["to"] = f"symbol:{qualified}"
    props = edge.setdefault("props", {})
    props["confidence"] = "definite"
    props["provenance"] = "libclang"
    props["resolved"] = True


def _qualified_name(cursor: Any) -> str:
    """Return a qualified ``ns::foo`` name for *cursor* or empty string."""
    spelling = getattr(cursor, "spelling", "") or ""
    if not spelling:
        return ""
    parts: list[str] = [spelling]
    parent = getattr(cursor, "semantic_parent", None)
    while parent is not None:
        parent_kind = getattr(parent, "kind", None)
        parent_spelling = getattr(parent, "spelling", "") or ""
        if not parent_spelling:
            break
        if parent_kind is not None and str(parent_kind).endswith(
            "TRANSLATION_UNIT"
        ):
            break
        parts.insert(0, parent_spelling)
        parent = getattr(parent, "semantic_parent", None)
    return "::".join(parts)


def _rel_from_location(location: Any, root: Path) -> str | None:
    """Return ``location``'s repo-relative POSIX path, or None."""
    if location is None:
        return None
    file_attr = getattr(location, "file", None)
    if file_attr is None:
        return None
    file_name = str(file_attr)
    if not file_name:
        return None
    try:
        return Path(file_name).resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return None


__all__ = [
    "STRATEGY",
    "upgrade_unresolved_calls",
]
