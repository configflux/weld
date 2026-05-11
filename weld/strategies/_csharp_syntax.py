"""Shared C# lexical helpers for the ADR 0056 Wave 2 strategies.

The Wave 2 strategies (``csharp_aspnet_routes``, ``csharp_efcore``,
``csharp_test_framework``) all need the same kind of *lightweight*
parsing: locate namespaces, classes, and attribute windows in C# source
without depending on tree-sitter or Roslyn. Keeping that logic in one
place avoids three nearly-identical copies and the divergence risk that
brings.

Scope:

- ``namespace`` declarations (block or file-scoped).
- ``class`` declarations including optional modifiers, generic
  parameters, and base list.
- Brace-balanced extraction of the class body span.
- Attribute-window walking: the contiguous block of ``[...]`` and
  whitespace lines immediately preceding a class or method declaration.

The helpers intentionally do not try to parse expressions or method
bodies in full. Each strategy still walks its body with its own
purpose-specific regex (``DbSet<T>``, ``[HttpGet(...)]``, etc.).
"""

from __future__ import annotations

import re

#: Block- or file-scoped namespace declaration. Capture: dotted name.
NAMESPACE_RE = re.compile(
    r"^[\t ]*namespace[\t ]+([A-Za-z_][A-Za-z0-9_.]*)[\t ]*[;{]?[\t ]*$",
    re.MULTILINE,
)

#: Class declaration with optional modifiers, generic parameters, and
#: base list. Captures: (1) class name, (2) base list (or None).
CLASS_RE = re.compile(
    r"(?:(?:public|internal|protected|private|static|partial|sealed|abstract)[\t ]+)*"
    r"class[\t ]+([A-Za-z_][A-Za-z0-9_]*)\b(?:\s*<[^>]*>)?\s*"
    r"(?::\s*([^{]+))?",
)


def namespace_spans(source_text: str) -> list[tuple[int, str]]:
    """Return ``(offset_after_decl, namespace)`` pairs in source order.

    Used together with :func:`namespace_at` to assign a namespace to an
    arbitrary offset within the source text. Nested namespaces are not
    interval-tracked; the most-recent declaration wins. This is
    sufficient for the canonical "one namespace per file" or
    file-scoped (``namespace Foo;``) layouts the Wave 2 strategies
    target.
    """
    return [
        (match.end(), match.group(1))
        for match in NAMESPACE_RE.finditer(source_text)
    ]


def namespace_at(offset: int, spans: list[tuple[int, str]]) -> str:
    """Return the most recent namespace declared before *offset*.

    Returns the empty string when *offset* sits before any namespace
    declaration in the file (e.g. a top-level class declared outside
    any block).
    """
    current = ""
    for span_offset, name in spans:
        if span_offset <= offset:
            current = name
        else:
            break
    return current


def class_body_range(
    source_text: str, after_class: int,
) -> tuple[int | None, int | None]:
    """Return ``(body_start, body_end)`` for the first ``{...}`` block.

    Brace-balanced: increments on ``{``, decrements on ``}``, returns
    when the depth zeroes out. Skips inheritance lists like
    ``class Foo : IBar, IBaz {`` by searching from the position
    *after_class* rather than from the class header itself.

    Returns ``(None, None)`` for malformed input -- no closing brace
    found, or the file ended mid-body. The caller is expected to skip
    the class in that case rather than guess.
    """
    body_start = source_text.find("{", after_class)
    if body_start < 0:
        return None, None
    depth = 0
    i = body_start
    n = len(source_text)
    while i < n:
        ch = source_text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return body_start + 1, i
        i += 1
    return None, None


def attribute_window_start(source_text: str, decl_offset: int) -> int:
    """Return the offset where the attribute block before *decl* begins.

    Walks backwards over the lines immediately preceding *decl_offset*,
    accepting any line that is whitespace-only or starts with ``[``.
    The first non-attribute line terminates the window. This handles
    multi-line attribute stacks (``[Fact]\\n[Trait(...)]\\nvoid M()``)
    without needing a full parser; multiple attributes inside a single
    block (``[Fact, Trait(...)]``) are caught by the per-strategy
    attribute regex over the returned window.
    """
    line_start = source_text.rfind("\n", 0, decl_offset)
    line_start = 0 if line_start < 0 else line_start + 1
    cursor = line_start
    while cursor > 0:
        prev_line_end = cursor - 1
        prev_line_start = source_text.rfind("\n", 0, prev_line_end)
        prev_line_start = (
            0 if prev_line_start < 0 else prev_line_start + 1
        )
        prev_line = source_text[prev_line_start:prev_line_end]
        stripped = prev_line.strip()
        if not stripped:
            cursor = prev_line_start
            continue
        if stripped.startswith("["):
            cursor = prev_line_start
            continue
        break
    return cursor


__all__ = [
    "CLASS_RE",
    "NAMESPACE_RE",
    "attribute_window_start",
    "class_body_range",
    "namespace_at",
    "namespace_spans",
]
