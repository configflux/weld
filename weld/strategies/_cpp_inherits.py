"""C++ inheritance edge emission (ADR 0064 criterion 2 / bd bou8).

Three responsibilities, mirroring :mod:`weld.strategies._java_inherits`:

1. :func:`extract_class_inheritance` -- regex scan returning
   ``(declaring_class, base_short_name)`` pairs for every base entry in
   every ``class`` / ``struct`` declaration with a base-class clause.
   C++ uses the syntax ``class Derived : <access> [virtual] Base`` (or
   ``: <access> Base1, <access> Base2`` for multiple inheritance). C++
   has no separate ``implements`` distinction at the language level --
   every base is emitted as an ``inherits`` edge regardless of whether
   the base is an abstract pure-interface (``Drawable``) or a concrete
   class (``Shape``). The criterion-2 file-vs-symbol contract is what
   matters; the edge-type vocabulary distinction is a per-language
   modelling choice that C++ does not make.

2. :func:`record_inheritance` -- stage records into an accumulator
   shared across all files in the discover run so :func:`finalise` can
   resolve bases against the project-wide class index (C++ headers
   declare bases that the strategy stack mints as symbol nodes on the
   declaring header; the derived class's symbol id keys off the
   *declaring* file's module path, not the base's location).

3. :func:`emit_inheritance_edges` -- consume the accumulator after the
   tree-sitter file loop completes, resolve each ``(derived, base)``
   pair against the project class index, and emit one ``inherits``
   edge per pair originating at the derived-class symbol node (per
   criterion 2's "symbol-origin, not file-origin" contract).

C++ specifics handled here:

* Access specifiers (``public`` / ``protected`` / ``private``) are
  stripped before lookup; the criterion-2 contract is class-level
  inheritance, not access-level metadata. A future enrichment can
  stamp the access modifier as an edge prop without changing the
  topology.
* The ``virtual`` keyword on a base (``: virtual public Base``) is
  consumed and discarded -- virtual inheritance is the same
  topological edge as non-virtual inheritance; only the runtime
  layout differs.
* Generic-parameter tails (``Comparable<Circle>``) and qualified
  prefixes (``foo::Bar``) are stripped to the final short name for
  the project-index lookup.
* Template class declarations (``template<typename T> class Foo : public Bar``)
  have their ``template<...>`` prefix consumed before scanning so the
  derived-class name is captured cleanly. A template with no bases
  (e.g. ``template<typename T> class Container``) generates zero
  inheritance edges -- the C++ analogue of the Java
  ``class Container<T>`` boundary the java sibling pins (bd 3kej).

Unresolved bases land on the shared ``symbol:unresolved:<short>``
sentinel, mirroring the call-edge unresolved path so the graph stays
referentially closed.
"""

from __future__ import annotations

import re

from weld.strategies._ts_call_graph import ts_module_from_path

#: Match a class / struct declaration with a base-class clause.
#: Captures:
#:   (1) the declaring kind keyword (``class`` / ``struct``)
#:   (2) the declaring identifier
#:   (3) the base-list body (comma-separated)
#:
#: The leading ``template<...>`` clause (optionally present) is
#: consumed by an outer pass before this regex runs -- see
#: :func:`extract_class_inheritance`. Type-parameter lists on the
#: declared name itself (``class Foo<T> : public Bar``) are *not*
#: expected in C++ source (template parameters are declared via the
#: outer ``template<...>`` clause, not on the class name), so we do
#: not consume them here.
_DECL_RE = re.compile(
    # kind keyword
    r"(class|struct)\s+"
    # leading optional attribute/specifier tokens (e.g. ``alignas(8)``)
    # are not modelled here; modern code rarely places them between the
    # class keyword and the name. We anchor strictly on the identifier.
    r"([A-Za-z_][A-Za-z0-9_]*)"
    # base-list: a colon, then a comma-separated list of base entries
    # terminating at the opening ``{`` of the class body. The non-greedy
    # form keeps the regex from spanning multiple declarations in a
    # file with several class definitions back-to-back.
    r"\s*:\s*([^{;]+?)"
    # opening brace ends the base list. We don't capture the body.
    r"\s*\{",
    re.MULTILINE | re.DOTALL,
)

#: Match a single base entry. Strips access specifiers, optional
#: ``virtual`` keyword, generic-arg tail, and qualified prefix. Captures
#: the bare dotted form so the project-index lookup uses the short
#: name. The access specifier is consumed before the type expression
#: (per C++ grammar -- access goes BEFORE the type name in a base
#: specifier).
_BASE_ENTRY_RE = re.compile(
    # optional access specifier; ``virtual`` may appear before or after
    # the access specifier so we accept either order.
    r"(?:(?:virtual|public|protected|private)\s+)*"
    # the qualified name itself
    r"((?:[A-Za-z_][A-Za-z_0-9]*::)*[A-Za-z_][A-Za-z_0-9]*)"
    # optional template argument tail
    r"(?:\s*<[^>]*>)?",
)

#: Match the leading ``template<...>`` clause so we can strip it
#: before scanning for ``class Foo : public Bar``. The clause may
#: contain one level of nested ``<...>`` (``template<template<typename> class>``);
#: deeper nesting is unsupported but rare in real code and not present
#: in the bundled fixture.
_TEMPLATE_PREFIX_RE = re.compile(
    r"template\s*<(?:[^<>]|<[^<>]*>)*>",
    re.DOTALL,
)

#: Match line-comment tails (``// ...``) and block-comment spans
#: (``/* ... */``). Replaced with whitespace so character offsets used
#: by other parsers stay stable -- mirrors the C# / java strippers.
_COMMENT_RE = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)


def _strip_comments(source_text: str) -> str:
    """Return *source_text* with line and block comments blanked out."""
    def _blank(match: re.Match) -> str:
        return "".join("\n" if ch == "\n" else " " for ch in match.group(0))
    return _COMMENT_RE.sub(_blank, source_text)


def _strip_template_prefixes(source_text: str) -> str:
    """Blank out leading ``template<...>`` clauses.

    Required because :data:`_DECL_RE` anchors on ``class``/``struct``
    keywords; a template-parameter list that contains an unrelated
    ``class`` keyword (``template<class T>``) would otherwise match
    spuriously, and a parameter-list that contains ``: public`` token
    sequences -- though uncommon -- could derail the base-list
    capture. Blanking the prefix preserves character offsets so other
    parsers reading the same text are unaffected.
    """
    def _blank(match: re.Match) -> str:
        return "".join("\n" if ch == "\n" else " " for ch in match.group(0))
    return _TEMPLATE_PREFIX_RE.sub(_blank, source_text)


def _short_name(base_entry: str) -> str:
    """Return the final identifier of a qualified base entry.

    ``foo::Bar`` -> ``Bar``. ``Comparable`` -> ``Comparable``. The
    project-index lookup runs on the short name; the canonical symbol
    id uses the declaring file's module path, not the include chain on
    the *use* side.
    """
    return base_entry.rsplit("::", 1)[-1]


def _split_base_entries(clause: str) -> list[str]:
    """Split a C++ base-list into bare base names.

    Splits on top-level commas (commas inside ``<...>`` belong to a
    template argument list and do not separate base entries). Each
    entry is matched against :data:`_BASE_ENTRY_RE` to strip access
    specifiers, the optional ``virtual`` keyword, and the generic-arg
    tail so the lookup uses ``Bar`` not ``public virtual Bar<int>``.
    """
    out: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in clause:
        if ch == "<":
            depth += 1
            current.append(ch)
        elif ch == ">":
            depth = max(depth - 1, 0)
            current.append(ch)
        elif ch == "," and depth == 0:
            entry = "".join(current).strip()
            if entry:
                out.append(entry)
            current = []
        else:
            current.append(ch)
    tail = "".join(current).strip()
    if tail:
        out.append(tail)
    bases: list[str] = []
    for entry in out:
        m = _BASE_ENTRY_RE.match(entry)
        if m:
            bases.append(m.group(1))
    return bases


def extract_class_inheritance(
    source_text: str,
) -> list[tuple[str, str]]:
    """Return ``[(declaring, base), ...]`` for every base in *source_text*.

    Every entry is captured as an ``inherits`` edge -- C++ does not
    distinguish ``implements`` at the language level, so a single
    return type keeps the API surface small. The list preserves
    source order so deterministic edge emission falls out naturally.
    """
    text = _strip_template_prefixes(_strip_comments(source_text))
    records: list[tuple[str, str]] = []
    for match in _DECL_RE.finditer(text):
        declaring = match.group(2)
        base_clause = match.group(3) or ""
        for base in _split_base_entries(base_clause):
            records.append((declaring, base))
    return records


def record_inheritance(
    inheritance_records: list,
    *,
    rel_path: str,
    source_text: str,
) -> None:
    """Append every (derived, base) record from *source_text*.

    Records the declaring file's relative path so :func:`finalise` can
    compute the canonical ``symbol:cpp:<module>:<derived>`` id without
    re-parsing the source. Records are filtered to skip self-references
    (``class Foo : public Foo`` is never legal C++ but the regex would
    not know that).
    """
    module_path = ts_module_from_path(rel_path)
    for declaring, base in extract_class_inheritance(source_text):
        short = _short_name(base)
        if declaring == short:
            # Defensive: self-reference is invalid C++; skip without
            # surfacing an error so a malformed corpus does not break
            # the discover run.
            continue
        inheritance_records.append({
            "rel_path": rel_path,
            "module_path": module_path,
            "derived": declaring,
            "base": base,
            "base_short": short,
        })


def build_project_class_index(
    nodes: dict[str, dict],
) -> dict[str, str]:
    """Return ``{class_short_name: symbol_id}`` for project C++ classes.

    Walks every ``type='symbol'`` node with ``language='cpp'`` and
    indexes the label (which equals the declared class short name) to
    the symbol id. Subsequent same-named declarations are ignored so
    resolution is deterministic -- if the project tree has two classes
    with the same short name in different headers, the first one
    discovered wins and the second base reference falls through to the
    unresolved sentinel. That matches the same project-index policy as
    the java sibling: ambiguous short names without an explicit
    qualified reference cannot be disambiguated by this MVP.
    """
    index: dict[str, str] = {}
    for nid, node in nodes.items():
        if not isinstance(node, dict):
            continue
        if node.get("type") != "symbol":
            continue
        props = node.get("props") or {}
        if props.get("language") != "cpp":
            continue
        label = node.get("label", "")
        if not label:
            continue
        index.setdefault(label, nid)
    return index


def emit_inheritance_edges(
    nodes: dict[str, dict],
    edges: list[dict],
    inheritance_records: list,
    source_strategy: str,
) -> None:
    """Walk *inheritance_records* and emit one ``inherits`` edge per record.

    Resolution:

    * Same-named project class -- resolves to ``symbol:cpp:<module>:<base_short>``
      via the project class index. ``confidence: definite``,
      ``resolved=True``.
    * Otherwise -- ``symbol:unresolved:<base_short>`` sentinel, minted
      lazily so the edge target is referentially closed.
      ``confidence: speculative``, ``resolved=False``.

    Edges originate at ``symbol:cpp:<module>:<derived>`` per the
    ADR 0064 criterion 2 "symbol-origin, not file-origin" contract.
    A missing derived-class symbol (the derived class is declared but
    its symbol node was never minted) skips the record -- there is no
    symbol to anchor the edge to. The shape mirrors the python and
    java inherits resolvers so downstream consumers can filter both
    edge types uniformly.
    """
    project_index = build_project_class_index(nodes)
    seen: set[tuple[str, str, str]] = set()
    for record in inheritance_records:
        from_id = f"symbol:cpp:{record['module_path']}:{record['derived']}"
        if from_id not in nodes:
            # The derived class was declared but its symbol node was
            # never minted (e.g. the file slipped past the symbol
            # extractor). Skip rather than emit a dangling edge.
            continue
        base_short = record["base_short"]
        if base_short in project_index:
            target_id = project_index[base_short]
            resolved = True
        else:
            target_id = f"symbol:unresolved:{base_short}"
            resolved = False
        key = (from_id, target_id, "inherits")
        if key in seen:
            continue
        seen.add(key)
        if not resolved:
            nodes.setdefault(
                target_id,
                {
                    "type": "symbol",
                    "label": base_short,
                    "props": {
                        "language": "cpp",
                        "source_strategy": source_strategy,
                        "authority": "derived",
                        "confidence": "speculative",
                        "kind": "unresolved",
                        "origin": "unresolved",
                        "qualname": base_short,
                    },
                },
            )
        props: dict = {
            "source_strategy": source_strategy,
            "confidence": "definite" if resolved else "speculative",
            "resolved": resolved,
            "base_name": record["base"],
            "derived_class": record["derived"],
        }
        rel_path = record.get("rel_path", "")
        if rel_path:
            # ADR 0074: attribute the edge to the file whose base-class
            # clause produced it -- record_inheritance already captures
            # rel_path per record, one file per declaration (bd rifzk).
            props["provenance"] = {"file": rel_path}
        edges.append({
            "from": from_id, "to": target_id, "type": "inherits", "props": props,
        })


__all__ = [
    "build_project_class_index",
    "emit_inheritance_edges",
    "extract_class_inheritance",
    "record_inheritance",
]
