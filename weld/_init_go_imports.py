"""Go-aware import-line filter for ``detect_frameworks``.

Background
----------
The framework matcher in ``weld/init_detect.py`` looks for the canonical
quoted path ``"github.com/gin-gonic/gin"`` to detect Gin in real Go source.
Per-line substring containment alone is too loose -- it produces false
positives when the path appears inside (a) a ``/* ... */`` block-comment
body, (b) a backtick raw-string literal, or (c) a plain double-quoted
string-literal assignment outside an ``import`` block.

This helper pre-filters a Go source text to only the lines that are
plausibly import context: either a single-line ``import "..."`` or a line
inside a parenthesised ``import (\n\t"..."\n)`` block. Block-comment
bodies and raw-string spans are stripped before classification.

The implementation is intentionally small -- it is not a full Go lexer.
It tracks just enough state to satisfy the three negative cases above
while preserving every positive case the existing tests pin (single
``import "..."`` and grouped ``import ( ... )``).

Caller: ``weld.init_detect.detect_frameworks`` wraps every ``.go`` file's
text with :func:`iter_go_import_lines` before feeding lines to
``_line_has_import``. The downstream substring match in that helper is a
defense-in-depth check; the import-context filtering happens here.
"""

from __future__ import annotations

from typing import Iterator


def _strip_block_comments_and_raw_strings(text: str) -> str:
    """Replace ``/* ... */`` and `` `...` `` spans with same-length blanks.

    Newlines inside these spans are preserved so line numbering is stable.
    Other characters become spaces. Double-quoted string literals are NOT
    stripped here -- they may legitimately carry the import path on
    ``import "..."`` lines and are filtered by ``iter_go_import_lines``
    based on import-block context instead.
    """
    out: list[str] = []
    i = 0
    n = len(text)
    in_block = False
    in_raw = False
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if in_block:
            if ch == "*" and nxt == "/":
                out.append("  ")
                i += 2
                in_block = False
                continue
            out.append("\n" if ch == "\n" else " ")
            i += 1
            continue
        if in_raw:
            if ch == "`":
                out.append(" ")
                i += 1
                in_raw = False
                continue
            out.append("\n" if ch == "\n" else " ")
            i += 1
            continue
        if ch == "/" and nxt == "*":
            out.append("  ")
            i += 2
            in_block = True
            continue
        if ch == "`":
            out.append(" ")
            i += 1
            in_raw = True
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def iter_go_import_lines(text: str) -> Iterator[str]:
    """Yield the lines of ``text`` that are within Go import context.

    A line is import context when it is either:

    * A top-level ``import "..."`` single-line declaration, or
    * Any line inside a parenthesised ``import ( ... )`` block.

    Block-comment bodies and backtick raw-string spans are blanked first so
    a path-like substring inside them is not mistaken for an import. Plain
    string-literal assignments outside any ``import`` block are filtered
    out by the import-context check itself: they begin with ``var``,
    ``const``, an identifier, ``return``, etc. -- never with ``import`` or
    a leading-quote import-block continuation.
    """
    sanitized = _strip_block_comments_and_raw_strings(text)
    in_block = False
    for line in sanitized.splitlines():
        stripped = line.strip()
        if not in_block:
            # Single-line ``import "..."`` (with optional alias).
            if stripped.startswith("import ") and not stripped.endswith("("):
                yield line
                continue
            # Opening of a grouped ``import (`` block.
            if stripped.startswith("import (") or stripped == "import(":
                in_block = True
                # Anything on the same line after the ``(`` is rare but
                # legal; the line itself does not carry an import path
                # at the start, so skip yielding it.
                continue
            # Outside import context -- drop the line.
            continue
        # Inside a grouped import block: emit until the closing ``)``.
        if stripped.startswith(")"):
            in_block = False
            continue
        yield line
