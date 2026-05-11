"""Template-instantiation edges for the libclang semantic layer (ADR 0057 § Wave 3).

Tree-sitter captures the *definition* of a template
(``template <typename T> class Foo { ... };``) but cannot follow an
instantiation (``Foo<int> f;``) to the definition. libclang's
``ClassTemplateDecl`` / ``FunctionTemplateDecl`` cursors expose the
definition site, and ``TemplateRef`` / ``TypeRef`` cursors with a
``referenced`` template let us trace each instantiation back to it.

Edge shape:

    template:<name>  --instantiated_by-->  file:<callsite>

with ``confidence: definite`` and an ``instantiation_args`` prop
recording the substituted parameter list when libclang exposes it.

The module is imported only after the dispatch guard in
:mod:`weld.strategies.cpp_libclang` confirms libclang is active, so the
``clang.cindex`` reference below is safe behind that gate.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from weld._node_ids import file_id

STRATEGY: str = "cpp_libclang"

#: Cap on the number of distinct template definitions we mint. Real
#: codebases have hundreds; the cap bounds pathological generated
#: headers.
_MAX_TEMPLATES: int = 20_000

#: Cap on the number of ``instantiated_by`` edges we emit. Highly
#: template-heavy codebases (Boost, Eigen) can produce tens of
#: thousands; the cap stops the graph from blowing up on a single TU.
_MAX_INSTANTIATIONS: int = 200_000


def template_id(name: str) -> str:
    """Return the canonical ``template:`` node ID for a template definition.

    We keep the C++ qualified name verbatim (case-sensitive). Qualified
    names like ``ns::Container`` are stable per the C++ rule that two
    templates with the same qualified name occupy the same definition.
    """
    return f"template:{name}"


def walk_translation_unit(
    cindex_module: Any,
    tu: Any,
    *,
    root: Path,
    nodes: dict[str, dict],
    edges: list[dict],
) -> int:
    """Walk *tu* and emit ``instantiated_by`` edges. Returns edges appended."""
    CursorKind = cindex_module.CursorKind
    templates_seen = 0
    instantiations = 0
    appended = 0

    # Pass 1: capture every template definition we see in this TU.
    for cursor in tu.cursor.walk_preorder():
        kind = cursor.kind
        if kind in (
            CursorKind.CLASS_TEMPLATE,
            CursorKind.FUNCTION_TEMPLATE,
            CursorKind.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION,
        ):
            if templates_seen >= _MAX_TEMPLATES:
                continue
            if _record_definition(cursor, root, nodes):
                templates_seen += 1

    # Pass 2: every instantiation reference rooted at a template.
    for cursor in tu.cursor.walk_preorder():
        kind = cursor.kind
        if kind not in (CursorKind.TYPE_REF, CursorKind.TEMPLATE_REF):
            continue
        if instantiations >= _MAX_INSTANTIATIONS:
            continue
        if _emit_instantiation(cursor, root, nodes, edges):
            appended += 1
            instantiations += 1
    return appended


def _record_definition(
    cursor: Any,
    root: Path,
    nodes: dict[str, dict],
) -> bool:
    """Mint a ``template:<name>`` node for a template definition.

    Returns True on insert (idempotent: setdefault skips duplicates).
    """
    name = _qualified_name(cursor)
    if not name:
        return False
    location = cursor.location
    rel = _rel_from_location(location, root)
    if rel is None:
        return False
    nid = template_id(name)
    if nid in nodes:
        return False
    nodes[nid] = {
        "type": "template_definition",
        "label": name,
        "props": {
            "name": name,
            "file": rel,
            "source_strategy": STRATEGY,
            "authority": "canonical",
            "confidence": "definite",
            "roles": ["implementation"],
        },
    }
    return True


def _emit_instantiation(
    cursor: Any,
    root: Path,
    nodes: dict[str, dict],
    edges: list[dict],
) -> bool:
    """Emit a ``template --instantiated_by--> file`` edge when resolvable."""
    referenced = getattr(cursor, "referenced", None)
    if referenced is None:
        return False
    name = _qualified_name(referenced)
    if not name:
        return False
    location = cursor.location
    rel = _rel_from_location(location, root)
    if rel is None:
        return False
    nid_template = template_id(name)
    nid_file = file_id(rel)
    # Mint a placeholder for the template node if we never saw its
    # definition in this TU (e.g. it lives in a header we did not
    # include in pass 1). The placeholder is ``inferred`` because we
    # cannot point to its definition file.
    nodes.setdefault(
        nid_template,
        {
            "type": "template_definition",
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
    props: dict = {
        "source_strategy": STRATEGY,
        "confidence": "definite",
        "file": rel,
    }
    instantiation_args = _instantiation_args(cursor)
    if instantiation_args:
        props["instantiation_args"] = instantiation_args
    edges.append(
        {
            "from": nid_template,
            "to": nid_file,
            "type": "instantiated_by",
            "props": props,
        }
    )
    return True


def _qualified_name(cursor: Any) -> str:
    """Return the cursor's qualified name (``ns::Foo``) when reachable."""
    spelling = getattr(cursor, "spelling", "") or ""
    if not spelling:
        return ""
    parts: list[str] = [spelling]
    parent = getattr(cursor, "semantic_parent", None)
    # Walk semantic parents collecting namespace + class names; the
    # libclang API exposes ``TRANSLATION_UNIT`` as the top so we stop
    # there.
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


def _instantiation_args(cursor: Any) -> str:
    """Return a stringified instantiation argument list when libclang exposes it.

    libclang's Python binding does not expose template arguments
    uniformly across cursor kinds; we fall back to the cursor's
    extent's spelling when available, otherwise empty. The string is
    never security-sensitive: it is a label, not code to execute.
    """
    type_obj = getattr(cursor, "type", None)
    if type_obj is None:
        return ""
    spelling = getattr(type_obj, "spelling", "") or ""
    return spelling


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
    "template_id",
    "walk_translation_unit",
]
