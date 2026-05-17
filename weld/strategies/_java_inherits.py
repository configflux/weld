"""Java inheritance / implementation edge emission (ADR 0064 criterion 2).

Three responsibilities, mirroring :mod:`weld.strategies._csharp_inheritance`:

1. :func:`extract_class_inheritance` -- regex scan returning
   ``(declaring_class, base_short_name, edge_type)`` triples for every
   ``extends`` / ``implements`` clause in ``class`` / ``interface`` /
   ``record`` declarations. ``edge_type`` is ``inherits`` for an
   ``extends`` clause and ``implements`` for an ``implements`` clause.

2. :func:`record_inheritance` -- stage records into an accumulator
   shared across all files in the discover run so :func:`finalise` can
   resolve same-package bases against the project-wide class index
   (Java's implicit-same-package model means ``class Foo extends Base``
   in package ``com.example`` resolves to ``com.example.Base`` declared
   in a sibling file, without an explicit import).

3. :func:`emit_inheritance_edges` -- consume the accumulator after the
   tree-sitter file loop completes, resolve each ``(derived, base)``
   pair against the project class index, and emit one edge per pair
   originating at the derived-class symbol node (per criterion 2's
   "symbol-origin, not file-origin" contract).

Generic-parameter tails (``Comparable<Point>``) and qualified prefixes
(``java.util.List``) are stripped to their final short name for the
project-index lookup. Unresolved bases land on the shared
``symbol:unresolved:<short>`` sentinel, mirroring the call-edge
unresolved path so the graph stays referentially closed.

Generic bounds inside the type-parameter list (``class Foo<T extends Base>``)
are *not* captured as class-level ``extends`` clauses: the regex
consumes the ``<...>`` block before the ``extends`` lookahead so the
inner ``extends`` keyword never matches.
"""

from __future__ import annotations

import re

from weld.strategies._ts_call_graph import ts_module_from_path

#: Match a class / interface / record / enum declaration with optional
#: ``extends`` and ``implements`` clauses. Captures:
#:   (1) the declaring kind keyword (``class`` / ``interface`` / etc.)
#:   (2) the declaring identifier
#:   (3) the ``extends`` clause body (comma-separated)
#:   (4) the ``implements`` clause body (comma-separated)
#:
#: Type-parameter lists (``<T>``, ``<T extends Number>``) are consumed
#: before the ``extends`` lookahead so generic bounds do not leak in
#: as class-level extends. Record parameter lists (``record Point(int x,
#: int y)``) are similarly consumed.
_DECL_RE = re.compile(
    # leading modifiers
    r"(?:(?:public|private|protected|static|final|abstract|sealed|non-sealed|default|strictfp)\s+)*"
    # kind keyword
    r"(class|interface|record|enum)\s+"
    # declaring name
    r"([A-Za-z_][A-Za-z0-9_]*)"
    # optional type parameters: balanced one-level deep is enough for
    # the bundled fixture and the corpora pinned in
    # docs/bench/tier1-corpora.yaml.
    r"(?:\s*<[^<>]*(?:<[^<>]*>[^<>]*)*>)?"
    # optional record parameters (record Point(int x, int y))
    r"(?:\s*\([^)]*\))?"
    # optional extends clause: list of dotted/generic identifiers
    r"(?:\s+extends\s+([^{<;]+?(?:<[^<>]*>)?(?:\s*,\s*[^{<;]+?(?:<[^<>]*>)?)*))?"
    # optional implements clause: list of dotted/generic identifiers
    r"(?:\s+implements\s+([^{<;]+?(?:<[^<>]*>)?(?:\s*,\s*[^{<;]+?(?:<[^<>]*>)?)*))?"
    # opening brace or semicolon (interface forward declarations end with ;)
    r"\s*[{;]",
    re.MULTILINE | re.DOTALL,
)

#: Match a single base entry. Strips generic-arg tail; captures the bare
#: dotted form so the project-index lookup uses the short name. Mirrors
#: the C# extractor's _BASE_ENTRY_RE.
_BASE_ENTRY_RE = re.compile(
    r"([A-Za-z_][A-Za-z_0-9.]*)"
    r"(?:\s*<[^>]*>)?",
)

#: Match a line-comment tail (``// ...``) and a block-comment span
#: (``/* ... */``). Replaced with whitespace so character offsets used
#: by other parsers stay stable -- mirrors the C# stripper.
_COMMENT_RE = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)


def _strip_comments(source_text: str) -> str:
    """Return *source_text* with line and block comments blanked out."""
    def _blank(match: re.Match) -> str:
        return "".join("\n" if ch == "\n" else " " for ch in match.group(0))
    return _COMMENT_RE.sub(_blank, source_text)


def _short_name(base_entry: str) -> str:
    """Return the final identifier of a qualified base entry.

    ``java.util.List`` -> ``List``. ``Comparable`` -> ``Comparable``.
    The project-index lookup runs on the short name; the canonical
    symbol id uses the declaring file's module path, not the import
    chain on the *use* side.
    """
    return base_entry.rsplit(".", 1)[-1]


def _split_base_entries(clause: str) -> list[str]:
    """Split an ``extends``/``implements`` clause into bare base names.

    Splits on top-level commas (commas inside ``<...>`` belong to a
    generic argument list and do not separate base entries). Each entry
    is then matched against :data:`_BASE_ENTRY_RE` to strip the
    generic-arg tail so the lookup uses ``IList`` not ``IList<int>``.
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
) -> list[tuple[str, str, str]]:
    """Return ``[(declaring, base, edge_type), ...]`` for *source_text*.

    ``edge_type`` is ``"inherits"`` for an ``extends`` base and
    ``"implements"`` for an ``implements`` base. The list preserves
    source order so deterministic edge emission falls out naturally.
    """
    text = _strip_comments(source_text)
    records: list[tuple[str, str, str]] = []
    for match in _DECL_RE.finditer(text):
        declaring = match.group(2)
        extends_clause = match.group(3) or ""
        implements_clause = match.group(4) or ""
        if extends_clause:
            for base in _split_base_entries(extends_clause):
                records.append((declaring, base, "inherits"))
        if implements_clause:
            for base in _split_base_entries(implements_clause):
                records.append((declaring, base, "implements"))
    return records


def record_inheritance(
    inheritance_records: list,
    *,
    rel_path: str,
    source_text: str,
) -> None:
    """Append every (derived, base, edge_type) record from *source_text*.

    Records the declaring file's relative path so :func:`finalise` can
    compute the canonical ``symbol:java:<module>:<derived>`` id without
    re-parsing the source. Records are filtered to skip self-references
    (``class Foo extends Foo`` is never legal Java but the regex would
    not know that).
    """
    module_path = ts_module_from_path(rel_path)
    for declaring, base, edge_type in extract_class_inheritance(source_text):
        short = _short_name(base)
        if declaring == short:
            # Defensive: self-reference is invalid Java; skip without
            # surfacing an error so a malformed corpus does not break
            # the discover run.
            continue
        inheritance_records.append({
            "rel_path": rel_path,
            "module_path": module_path,
            "derived": declaring,
            "base": base,
            "base_short": short,
            "edge_type": edge_type,
        })


def build_project_class_index(
    nodes: dict[str, dict],
) -> dict[str, str]:
    """Return ``{class_short_name: symbol_id}`` for project Java classes.

    Walks every ``type='symbol'`` node with ``language='java'`` and
    indexes the label (which equals the declared class short name)
    to the symbol id. Subsequent same-named declarations are ignored
    so resolution is deterministic -- if the project tree has two
    classes with the same short name in different packages, the first
    one discovered wins and the second base reference falls through to
    the unresolved sentinel. That matches Java's import-disambiguation
    contract: ambiguous short names require an explicit import on the
    use side, which we do not resolve in this MVP.
    """
    index: dict[str, str] = {}
    for nid, node in nodes.items():
        if not isinstance(node, dict):
            continue
        if node.get("type") != "symbol":
            continue
        props = node.get("props") or {}
        if props.get("language") != "java":
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
    """Walk *inheritance_records* and emit one ``inherits``/``implements`` edge per record.

    Resolution:

    * Same-named project class -- resolves to ``symbol:java:<module>:<base_short>``
      via the project class index. ``confidence: definite``,
      ``resolved=True``.
    * Otherwise -- ``symbol:unresolved:<base_short>`` sentinel, minted
      lazily so the edge target is referentially closed.
      ``confidence: speculative``, ``resolved=False``.

    Edges originate at ``symbol:java:<module>:<derived>`` per the
    ADR 0064 criterion 2 "symbol-origin, not file-origin" contract.
    A missing derived-class symbol (the derived class is declared but
    its symbol node was never minted) skips the record -- there is no
    symbol to anchor the edge to. The shape mirrors the python
    ``_python_inherits.emit_inherits_edges`` resolver / edge contract
    so downstream consumers can filter both edge types uniformly.
    """
    project_index = build_project_class_index(nodes)
    seen: set[tuple[str, str, str]] = set()
    for record in inheritance_records:
        from_id = f"symbol:java:{record['module_path']}:{record['derived']}"
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
        edge_type = record["edge_type"]
        key = (from_id, target_id, edge_type)
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
                        "language": "java",
                        "source_strategy": source_strategy,
                        "authority": "derived",
                        "confidence": "speculative",
                        "kind": "unresolved",
                        "origin": "unresolved",
                        "qualname": base_short,
                    },
                },
            )
        edges.append({
            "from": from_id,
            "to": target_id,
            "type": edge_type,
            "props": {
                "source_strategy": source_strategy,
                "confidence": "definite" if resolved else "speculative",
                "resolved": resolved,
                "base_name": record["base"],
                "derived_class": record["derived"],
            },
        })


__all__ = [
    "build_project_class_index",
    "emit_inheritance_edges",
    "extract_class_inheritance",
    "record_inheritance",
]
