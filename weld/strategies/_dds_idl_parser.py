"""Text parser for OMG IDL (``.idl``) DDS contract files.

Private helper for :mod:`weld.strategies.dds_idl`. Parses the subset of
OMG IDL that CycloneDDS / FastDDS data-definition files use in practice
and returns a structured :class:`IdlFile`. Per ADR 0086's static-truth
policy the parse is text-only: no ``#include`` is followed (so a crafted
``.idl`` cannot traverse the filesystem), no code is evaluated, and every
scan is bounded by the input length.

Recognised constructs:

- ``module <name> { ... }`` -- nesting namespace; contributes to the
  dotted qualified name of the types it encloses.
- ``struct <name> { <field>; ... };`` -- a data type. ``sequence<T>``,
  ``string<N>``, arrays (``T name[N]``), and comma-declarators
  (``long a, b;``) are tolerated; unparseable field lines are skipped.
- ``enum <name> { A, B = 2, C };`` -- emits the member identifiers.
- ``@topic`` / ``@nested`` annotations and ``#pragma keylist <Struct>``
  drive topic classification (see :class:`IdlStruct`).

Everything else (``union``, ``typedef``, ``const``, ``interface``,
``bitmask``, ``bitset``, forward declarations) is skipped tolerantly:
its body -- if any -- is consumed via balanced-brace matching so the
walk stays aligned.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# A single top-level lexical token: an identifier (optionally
# ``@``-prefixed for an annotation), a scope separator, or one of the
# structural punctuation characters the walk branches on.
_TOKEN_RE = re.compile(r"@?[A-Za-z_][A-Za-z0-9_]*|::|[{};,=]")
_KEYLIST_RE = re.compile(r"#\s*pragma\s+keylist\s+([A-Za-z_][A-Za-z0-9_]*)")
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_ARRAY_RE = re.compile(r"\[[^\]]*\]")
_SKIP_KEYWORDS = frozenset(
    {"union", "typedef", "const", "interface", "bitmask", "bitset",
     "exception", "valuetype", "native"}
)


@dataclass(frozen=True)
class IdlStruct:
    """One ``struct`` declaration.

    *qualified_name* is the dotted module path plus the struct name
    (original source case). *is_topic* is False only when the struct is
    explicitly ``@nested``; otherwise every top-level struct is a
    DDS topic-type candidate (the DDS code generators emit TypeSupport
    for each). *topic_definite* is True when an explicit ``@topic``
    annotation or a ``#pragma keylist`` names the struct.
    """

    qualified_name: str
    fields: tuple[dict, ...]
    is_topic: bool
    topic_definite: bool


@dataclass(frozen=True)
class IdlEnum:
    """One ``enum`` declaration with its member identifiers."""

    qualified_name: str
    members: tuple[str, ...]


@dataclass(frozen=True)
class IdlFile:
    """Parsed contents of a single ``.idl`` file."""

    structs: tuple[IdlStruct, ...]
    enums: tuple[IdlEnum, ...]


def _strip_comments(text: str) -> str:
    """Strip comments and neutralise string/char literals.

    ``//`` line comments and ``/* ... */`` block comments are removed.
    String and character literals are honoured during comment scanning
    (so a ``//`` inside a literal is not mistaken for a comment) and then
    blanked to equal-length whitespace, so an identifier that happens to
    sit inside an annotation string argument (``@note("... struct ...")``)
    can never be tokenised as a declaration. Span lengths are preserved
    throughout so later ``str.find`` offsets stay valid; newlines outside
    comments are preserved so line-oriented pragmas still scan.
    """
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in "\"'":
            j = i + 1
            while j < n and text[j] != ch:
                j += 2 if text[j] == "\\" else 1
            consumed = j + 1 if j < n else n
            out.append(" " * (consumed - i))  # blank literal, keep length
            i = consumed
        elif ch == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif ch == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _collect_keylist(text: str) -> set[str]:
    """Return struct names named by ``#pragma keylist <Struct> ...``."""
    return {m.group(1) for m in _KEYLIST_RE.finditer(text)}


def _strip_preprocessor(text: str) -> str:
    """Blank out ``#``-prefixed preprocessor lines (keeping newlines).

    ``#include`` is intentionally *not* followed: the strategy never
    reads a referenced path, so a hostile include cannot traverse the
    filesystem. All ``#`` directives are simply removed from the token
    stream.
    """
    lines = text.split("\n")
    return "\n".join("" if ln.lstrip().startswith("#") else ln for ln in lines)


def _match_brace(text: str, open_idx: int) -> int:
    """Return the index of the ``}`` matching the ``{`` at *open_idx*.

    String/char literals are skipped so a brace inside a literal does
    not unbalance the count. Returns ``len(text)`` when unmatched
    (tolerant: an unterminated block consumes to end of input).
    """
    depth = 0
    i, n = open_idx, len(text)
    while i < n:
        ch = text[i]
        if ch in "\"'":
            j = i + 1
            while j < n and text[j] != ch:
                if text[j] == "\\":
                    j += 1
                j += 1
            i = j + 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return n


def _split_top_level(body: str, sep: str) -> list[str]:
    """Split *body* on *sep*, ignoring separators nested in ``<>``/``[]``/``()``.

    Used for field declarations (``;``) and enum members (``,``) so a
    ``sequence<map<K, V>>`` or array bound never splits mid-template.
    """
    parts: list[str] = []
    depth = 0
    start = 0
    for i, ch in enumerate(body):
        if ch in "<[(":
            depth += 1
        elif ch in ">])":
            depth = max(0, depth - 1)
        elif ch == sep and depth == 0:
            parts.append(body[start:i])
            start = i + 1
    parts.append(body[start:])
    return parts


def _parse_fields(body: str) -> list[dict]:
    """Parse a struct body into ``{"type", "name"}`` field records.

    Best-effort and tolerant: annotations and array bounds are stripped,
    ``long a, b;`` yields two records sharing the type, and any
    declaration that does not resolve to at least ``<type> <name>`` is
    skipped rather than guessed.
    """
    fields: list[dict] = []
    for raw in _split_top_level(body, ";"):
        decl = _ARRAY_RE.sub("", raw).strip()
        if not decl:
            continue
        # Drop leading member annotations (``@key``, ``@id(3)`` ...).
        while decl.startswith("@"):
            decl = decl[1:]
            m = _IDENT_RE.match(decl)
            if not m:
                break
            decl = decl[m.end():].lstrip()
            if decl.startswith("("):
                close = decl.find(")")
                decl = decl[close + 1:].lstrip() if close >= 0 else ""
        declarators = _split_top_level(decl, ",")
        head = declarators[0].split("=", 1)[0].split()
        if len(head) < 2:
            continue
        ftype = " ".join(head[:-1])
        names = [head[-1]]
        for extra in declarators[1:]:
            extra = extra.split("=", 1)[0].strip()
            if _IDENT_RE.fullmatch(extra):
                names.append(extra)
        for name in names:
            if _IDENT_RE.fullmatch(name):
                fields.append({"type": ftype, "name": name})
    return fields


def _parse_enum_members(body: str) -> list[str]:
    """Return the leading identifier of each comma-separated enum member."""
    members: list[str] = []
    for raw in _split_top_level(body, ","):
        m = _IDENT_RE.match(raw.strip().lstrip("@"))
        if m:
            members.append(m.group(0))
    return members


def _read_name(tokens: list, idx: int) -> tuple[str, int]:
    """Return the next identifier token at/after *idx* and its new index."""
    while idx < len(tokens):
        tok = tokens[idx][0]
        idx += 1
        if _IDENT_RE.fullmatch(tok):
            return tok, idx
    return "", idx


def _advance_past(tokens: list, idx: int, offset: int) -> int:
    """Return the first token index whose start is at or beyond *offset*.

    Used to resynchronise the token cursor after a body span (or a
    statement terminator) has been consumed on the raw text.
    """
    if offset < 0:
        return len(tokens)
    j = idx
    while j < len(tokens) and tokens[j][1] <= offset:
        j += 1
    return j


def _emit_type(
    tok: str, name: str, inner: str, scope: list[str], pending: set[str],
    keylist: set[str], structs: list[IdlStruct], enums: list[IdlEnum],
) -> None:
    """Append a struct or enum record for a parsed declaration."""
    qualified = ".".join([*scope, name])
    if tok == "struct":
        structs.append(IdlStruct(
            qualified_name=qualified,
            fields=tuple(_parse_fields(inner)),
            is_topic="nested" not in pending,
            topic_definite="topic" in pending or name in keylist,
        ))
    else:
        enums.append(IdlEnum(
            qualified_name=qualified,
            members=tuple(_parse_enum_members(inner)),
        ))


def parse_idl_text(text: str) -> IdlFile:
    """Parse *text* (one ``.idl`` file) into an :class:`IdlFile`."""
    stripped = _strip_comments(text)
    # Collect keylist pragmas from comment-free text (so a commented-out
    # pragma cannot mark a struct) but before preprocessor lines are dropped.
    keylist = _collect_keylist(stripped)
    body = _strip_preprocessor(stripped)
    tokens = [(m.group(0), m.start()) for m in _TOKEN_RE.finditer(body)]

    structs: list[IdlStruct] = []
    enums: list[IdlEnum] = []
    scope: list[str] = []
    pending: set[str] = set()
    i = 0
    while i < len(tokens):
        tok, start = tokens[i]
        if tok.startswith("@"):
            pending.add(tok[1:])
            i += 1
            continue
        if tok == "}":
            if scope:
                scope.pop()
            pending.clear()
            i += 1
            continue
        if tok == "module":
            name, i = _read_name(tokens, i + 1)
            if name:
                scope.append(name)
            pending.clear()
            continue
        if tok in {"struct", "enum"}:
            name, i = _read_name(tokens, i + 1)
            end = start + len(tok)
            brace = body.find("{", end)
            semi = body.find(";", end)
            if brace < 0 or (0 <= semi < brace):
                # Forward declaration (``struct Foo;``) -- no body.
                i = _advance_past(tokens, i, semi)
                pending.clear()
                continue
            close = _match_brace(body, brace)
            if name:
                _emit_type(
                    tok, name, body[brace + 1:close], scope, pending,
                    keylist, structs, enums,
                )
            i = _advance_past(tokens, i, close)
            pending.clear()
            continue
        if tok in _SKIP_KEYWORDS:
            end = start + len(tok)
            brace = body.find("{", end)
            semi = body.find(";", end)
            if brace >= 0 and (semi < 0 or brace < semi):
                i = _advance_past(tokens, i, _match_brace(body, brace))
            else:
                i = _advance_past(tokens, i, semi)
            pending.clear()
            continue
        if tok == ";":
            pending.clear()
        i += 1
    return IdlFile(structs=tuple(structs), enums=tuple(enums))
