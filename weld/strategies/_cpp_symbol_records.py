"""Structured per-symbol records for C++ files (ADR 0057 Wave 2).

Tree-sitter's YAML query layer returns *names only* (one capture per
``@name`` in ``cpp.yaml``). To distinguish a forward declaration from a
definition, and to capture template-parameter lists at the definition
site, we re-parse the source text with targeted regex anchored at each
export name. The same regex strategy is already used by
:mod:`weld.strategies._cpp_tree_sitter` for entrypoint detection.

The shape of each record is intentionally narrow:

  * ``name``: the exported identifier as captured by the ``exports``
    query (e.g. ``Foo``, ``Foo::bar``, ``free_add``).
  * ``kind``: ``"definition"`` when the source text shows a body
    (function with ``{...}`` or class/struct with ``{...};``);
    ``"declaration"`` when the source shows a terminating ``;`` with
    no body.
  * ``template``: ``True`` when a ``template <...>`` clause precedes
    the symbol.
  * ``template_signature``: the raw parameter list between the angle
    brackets when ``template`` is true (e.g. ``"typename T"``,
    ``"typename T, int N"``); ``None`` otherwise.

The classifier is best-effort:

  * Multiple definitions with the same unqualified name collapse onto
    the first match found (overload sets are name-only at this layer
    per ``cpp.yaml`` § "Deliberate limitations").
  * Macros, conditional compilation, and template specialisations are
    not preprocessed (Wave 3 covers those with libclang).
  * Function-style ``using`` aliases and ``typedef``s do not appear in
    the ``exports`` list so they are not classified here.

The module is pure: no filesystem access, no logging, no globals.
"""

from __future__ import annotations

import re

# A function/method definition has parentheses followed (possibly across
# newlines and qualifiers like ``const``, ``noexcept``, ``override``,
# ``final``, ``= default``, ``= delete``) by an opening brace before any
# semicolon. We anchor on the bare unqualified name so qualified
# definitions like ``void Foo::bar()`` still match when the export is
# ``Foo::bar`` (we strip the qualifier separately).
#
# Tail group ``(?:\([^;{]*\))*`` permits attribute parentheses like
# ``[[noreturn]]`` between the name and the parameter list; modern code
# rarely uses it but the cost of allowing it is zero.
_FUNCTION_DEF_BODY_TAIL = (
    r"\s*\([^;{}]*\)"          # primary parameter list
    r"(?:\s*[A-Za-z_][A-Za-z0-9_]*)*"  # cv-qualifiers / specifiers
    r"(?:\s*=\s*(?:default|delete|0))?"  # = default / = delete / pure virtual
    r"\s*\{"                   # opening brace begins the body
)
_FUNCTION_DECL_TAIL = (
    r"\s*\([^;{}]*\)"          # parameter list
    r"(?:\s*[A-Za-z_][A-Za-z0-9_]*)*"  # cv-qualifiers / specifiers
    r"(?:\s*=\s*(?:default|delete|0))?"
    r"\s*;"                    # semicolon ends the declaration
)

# A class/struct definition has the keyword followed by the type name
# and (after optional base specifiers) an opening brace.
_TYPE_DEF_TAIL = r"(?:\s*:[^;{]*)?\s*\{"
_TYPE_DECL_TAIL = r"\s*;"

# Template clause: ``template`` followed by an angle-bracket parameter
# list. We capture the contents between the outermost ``<`` and ``>``.
# The clause may span multiple lines but does not nest deeper than one
# level for our purposes -- a ``template <template <typename> class>``
# parameter is treated as the literal text inside the outer brackets,
# which is the desired behaviour for the ``template_signature`` prop.
#
# The clause may be followed by a return type / inheritance list /
# qualifiers between the closing ``>`` and the symbol name itself, so
# we do NOT anchor to ``$`` at the end of the head slice. Instead the
# caller bounds the search window to the slice between the previous
# statement terminator (``;``, ``}``, or beginning-of-file) and the
# definition match.
_TEMPLATE_CLAUSE_RE = re.compile(
    r"template\s*<\s*((?:[^<>]|<[^<>]*>)*)>",
    re.DOTALL,
)
_STATEMENT_TERMINATORS_RE = re.compile(r"[;{}]")


def _unqualified(name: str) -> str:
    """Return the tail segment of a ``::``-qualified C++ name."""
    if "::" in name:
        return name.rsplit("::", 1)[1]
    return name


def _looks_like_type_name(source_text: str, name: str) -> bool:
    """Return True when *name* appears as a class/struct in *source_text*."""
    pattern = re.compile(
        r"\b(?:class|struct)\s+" + re.escape(name) + r"\b",
    )
    return bool(pattern.search(source_text))


def _find_kind_and_template_for_function(
    source_text: str, name: str,
) -> tuple[str | None, bool, str | None]:
    """Classify *name* as a function/method declaration vs definition.

    Returns ``(kind, template, template_signature)`` or ``(None, False,
    None)`` when no match is found.
    """
    unq = re.escape(_unqualified(name))
    # Definition pattern: the bare unqualified name followed by a body.
    # We try both the qualified form (Foo::bar) and the unqualified
    # tail (bar) so out-of-class definitions classify correctly when
    # the export captured the qualified name and when only the tail
    # appears in the source.
    qualified = re.escape(name) if "::" in name else None
    def_patterns: list[str] = []
    if qualified is not None:
        def_patterns.append(qualified + _FUNCTION_DEF_BODY_TAIL)
    def_patterns.append(r"\b" + unq + _FUNCTION_DEF_BODY_TAIL)
    decl_patterns: list[str] = []
    if qualified is not None:
        decl_patterns.append(qualified + _FUNCTION_DECL_TAIL)
    decl_patterns.append(r"\b" + unq + _FUNCTION_DECL_TAIL)

    for pattern in def_patterns:
        match = re.search(pattern, source_text, re.DOTALL)
        if match:
            template, signature = _preceding_template(
                source_text, match.start(),
            )
            return "definition", template, signature

    for pattern in decl_patterns:
        match = re.search(pattern, source_text, re.DOTALL)
        if match:
            template, signature = _preceding_template(
                source_text, match.start(),
            )
            return "declaration", template, signature

    return None, False, None


def _find_kind_and_template_for_type(
    source_text: str, name: str,
) -> tuple[str | None, bool, str | None]:
    """Classify *name* as a class/struct declaration vs definition."""
    escaped = re.escape(name)
    # ``class Foo { ... }`` / ``struct Foo : Base { ... }``.
    def_pattern = r"\b(?:class|struct)\s+" + escaped + _TYPE_DEF_TAIL
    decl_pattern = r"\b(?:class|struct)\s+" + escaped + _TYPE_DECL_TAIL

    match = re.search(def_pattern, source_text, re.DOTALL)
    if match:
        template, signature = _preceding_template(
            source_text, match.start(),
        )
        return "definition", template, signature

    match = re.search(decl_pattern, source_text, re.DOTALL)
    if match:
        template, signature = _preceding_template(
            source_text, match.start(),
        )
        return "declaration", template, signature

    return None, False, None


def _preceding_template(
    source_text: str, position: int,
) -> tuple[bool, str | None]:
    """Return ``(template_flag, signature)`` for the clause before *position*.

    Searches the slice between the previous statement terminator
    (``;``, ``{``, ``}``) and *position*. A ``template <...>`` clause
    that lives entirely within that slice is recognised and its
    parameter list is returned as the signature. Returns
    ``(False, None)`` when no clause is present.
    """
    head = source_text[:position]
    # Find the most recent statement terminator before the match.
    window_start = 0
    for term_match in _STATEMENT_TERMINATORS_RE.finditer(head):
        window_start = term_match.end()
    window = head[window_start:]
    match = _TEMPLATE_CLAUSE_RE.search(window)
    if not match:
        return False, None
    signature = match.group(1).strip()
    # Collapse internal whitespace to single spaces so the signature is
    # graph-stable regardless of formatting in the source.
    signature = re.sub(r"\s+", " ", signature)
    return True, signature


def extract_symbol_records(
    source_text: str,
    exports: list[str],
    classes: list[str] | None = None,
) -> list[dict]:
    """Return structured records for each export, in input order.

    Args:
        source_text: Raw C++ source.
        exports: The flat names list returned by ``cpp.yaml``'s
            ``exports`` query.
        classes: Optional names list from the ``classes`` query, used
            to disambiguate type names from free symbols.

    Returns:
        A list of dicts ``{name, kind, template, template_signature}``
        one per input export. Entries we cannot classify carry
        ``kind=None`` so callers can drop them or keep them as
        unknown-shape placeholders.
    """
    classes_set: set[str] = set(classes or [])
    records: list[dict] = []
    for name in exports:
        unq = _unqualified(name)
        # If the unqualified tail is in the class set OR the source
        # text contains ``class Foo`` / ``struct Foo``, classify as a
        # type. Otherwise fall back to the function classifier.
        is_type = unq in classes_set or _looks_like_type_name(
            source_text, unq,
        )
        kind: str | None
        template: bool
        template_signature: str | None
        if is_type and "::" not in name:
            kind, template, template_signature = (
                _find_kind_and_template_for_type(source_text, unq)
            )
        else:
            kind, template, template_signature = (
                _find_kind_and_template_for_function(source_text, name)
            )
        record: dict = {
            "name": name,
            "kind": kind,
            "template": template,
        }
        if template_signature is not None:
            record["template_signature"] = template_signature
        records.append(record)
    return records


__all__ = ["extract_symbol_records"]
