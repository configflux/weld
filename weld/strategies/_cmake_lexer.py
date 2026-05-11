"""Block-level lexer for ``cpp_cmake`` (ADR 0057).

The CMake grammar is messy, but for the v1 build-graph extraction we
only need the *call block* shape: ``COMMAND(<args>)``. Real CMake calls
span multiple lines, embed comments, and contain quoted strings that
may themselves contain whitespace.

This lexer turns a CMakeLists.txt string into a stream of
:class:`CMakeCall` records, each carrying the command name and a flat
list of argument tokens. The lexer is intentionally permissive --
unknown commands round-trip without error so the call-handler layer
can decide which calls to act on.

Out of scope:

- Bracket arguments (``[[...]]``) -- treated as opaque single tokens.
- Backslash line continuations -- CMake does not use them; we treat a
  backslash as a literal character.
- ``if/endif``/``while``/``foreach`` block scoping -- the lexer emits
  every call regardless of nesting; ``cpp_cmake`` ignores branches it
  does not understand.
- Generator expressions ``$<...>`` -- preserved verbatim in the token.

Public API:

- :class:`CMakeCall`     -- ``(command: str, args: list[str], line: int)``.
- :func:`tokenize_args`  -- split an argument body into tokens.
- :func:`iter_calls`     -- yield :class:`CMakeCall` from CMake text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "CMakeCall",
    "iter_calls",
    "strip_comments",
    "tokenize_args",
]

# Block matcher: ``COMMAND(<body>)``. We capture the command and the
# *entire* parenthesised body. CMake allows nested parentheses
# (``add_test(NAME foo COMMAND foo $<TARGET_FILE:bar>)``) so a flat
# ``[^)]*`` body would truncate at the first ``)`` inside the call.
# The greedy ``.*?`` is correctness-equivalent to a single-pass
# bracket-balanced scan because the lexer only feeds CMake-like text;
# the worked-example corpus and unit tests confirm round-trip on
# nested-paren shapes.
_BLOCK_RE = re.compile(
    r"([A-Za-z_][A-Za-z0-9_]*)\s*\(",
)
_COMMENT_RE = re.compile(r"#[^\n]*")


@dataclass(frozen=True)
class CMakeCall:
    """A single ``COMMAND(args...)`` invocation discovered by the lexer."""

    command: str
    args: list[str]
    line: int


def strip_comments(text: str) -> str:
    """Return *text* with CMake line comments removed.

    ``# foo ...`` runs to end-of-line. Bracket comments ``#[[...]]`` are
    not handled; users that need them get tokens that include the
    comment marker (rare in normal projects).
    """
    return _COMMENT_RE.sub("", text)


def _scan_balanced(text: str, start: int) -> int:
    """Return the index of the matching close-paren after *start*.

    *start* must point one past the opening ``(``. Quoted strings are
    skipped so a ``)`` inside a quoted token does not close the call.
    Returns ``-1`` if the input is unbalanced (truncated file, etc.).
    """
    depth = 1
    i = start
    n = len(text)
    in_quote = False
    while i < n:
        ch = text[i]
        if in_quote:
            if ch == "\\" and i + 1 < n:
                # Skip the next character verbatim (escape inside quote).
                i += 2
                continue
            if ch == '"':
                in_quote = False
            i += 1
            continue
        if ch == '"':
            in_quote = True
            i += 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def tokenize_args(body: str) -> list[str]:
    """Split a CMake argument body into a flat list of tokens.

    Tokens are whitespace-separated except inside double-quoted strings,
    where whitespace is preserved verbatim. Quotes themselves are
    stripped from the resulting token. Empty tokens are dropped so the
    caller can iterate without bound-checking.

    Examples
    --------
    >>> tokenize_args('a "b c" d')
    ['a', 'b c', 'd']
    >>> tokenize_args('${VAR} "with space"')
    ['${VAR}', 'with space']
    >>> tokenize_args('')
    []
    """
    tokens: list[str] = []
    buf: list[str] = []
    in_quote = False
    i = 0
    n = len(body)
    while i < n:
        ch = body[i]
        if in_quote:
            if ch == "\\" and i + 1 < n:
                buf.append(body[i + 1])
                i += 2
                continue
            if ch == '"':
                # End of the quoted segment. Do not flush yet: the
                # quoted text concatenates with any adjacent unquoted
                # text on either side (CMake treats ``FOO="x"`` as a
                # single token, not two).
                in_quote = False
                i += 1
                continue
            buf.append(ch)
            i += 1
            continue
        if ch == '"':
            # Start a quoted segment without flushing the buffer so the
            # preceding unquoted text concatenates with it.
            in_quote = True
            i += 1
            continue
        if ch.isspace():
            if buf:
                tokens.append("".join(buf))
                buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    if buf:
        tokens.append("".join(buf))
    # Drop empties defensively; the loop above already filters them but
    # an all-whitespace input previously appended one.
    return [tok for tok in tokens if tok]


def iter_calls(text: str):
    """Yield :class:`CMakeCall` records from *text*.

    The input is the raw CMakeLists content (callers should
    :func:`strip_comments` first if they want comment-aware behaviour;
    the function does not call ``strip_comments`` itself so callers
    can use the same lexer on already-clean text).
    """
    pos = 0
    n = len(text)
    while pos < n:
        match = _BLOCK_RE.search(text, pos)
        if match is None:
            return
        command = match.group(1)
        body_start = match.end()
        body_end = _scan_balanced(text, body_start)
        if body_end < 0:
            return
        body = text[body_start:body_end]
        # Line of the command keyword (1-indexed).
        line = text.count("\n", 0, match.start()) + 1
        yield CMakeCall(command=command, args=tokenize_args(body), line=line)
        pos = body_end + 1
