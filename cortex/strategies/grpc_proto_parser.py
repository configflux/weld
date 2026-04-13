"""Proto text parser for the grpc_proto strategy (project-xoq.5.1).

Parses ``.proto`` declarations into small in-memory dataclasses. Per
ADR 0018's static-truth policy, parsing is text-only: no ``protoc``
invocation, no cross-file resolution, no runtime inspection. When a
declaration's brace balance is malformed the affected block is dropped
rather than guessed at.

The parser is intentionally grammar-light. It understands proto3
surface forms (proto2 files parse too because we ignore
``required``/``optional`` modifiers), plus:

- ``package a.b.c;``
- ``service Name { rpc Method([stream] Req) returns ([stream] Resp); ... }``
- ``message Name { field_type name = N; ... }`` with nested
  ``message`` and ``enum`` blocks extracted under the parent's dotted
  name.
- ``enum Name { MEMBER = N; ... }``

Anything else (options, imports, reserved numbers, oneof declarations,
custom extensions) is ignored. The result is a ``ProtoFile`` that the
facade module converts into cortex nodes and edges.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Parsed structures
# ---------------------------------------------------------------------------

@dataclass
class ProtoRpc:
    service: str
    name: str
    request_type: str
    response_type: str
    client_stream: bool
    server_stream: bool

@dataclass
class ProtoService:
    name: str
    rpcs: list[ProtoRpc] = field(default_factory=list)

@dataclass
class ProtoMessage:
    """A message declaration, possibly nested under a parent message.

    ``qualified_name`` is the dotted path from the top-level message
    down to this one (``Outer.Inner``). ``fields`` holds the declared
    field names in source order.
    """

    qualified_name: str
    fields: list[str] = field(default_factory=list)

@dataclass
class ProtoEnum:
    name: str
    members: list[str] = field(default_factory=list)

@dataclass
class ProtoFile:
    package: str
    services: list[ProtoService] = field(default_factory=list)
    messages: list[ProtoMessage] = field(default_factory=list)
    enums: list[ProtoEnum] = field(default_factory=list)

# ---------------------------------------------------------------------------
# Comment stripping
# ---------------------------------------------------------------------------

_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT_RE = re.compile(r"//[^\n]*")

def _strip_comments(text: str) -> str:
    """Remove ``/* ... */`` block comments and ``// ...`` line comments.

    Proto has no string-embedded comments that matter for our
    extraction -- we only care about identifiers and braces, both of
    which live outside any string literal in well-formed proto -- so a
    naive regex strip is sufficient and keeps the parser honest with
    the static-truth policy.
    """
    no_block = _BLOCK_COMMENT_RE.sub(" ", text)
    return _LINE_COMMENT_RE.sub("", no_block)

# ---------------------------------------------------------------------------
# Regex tokens
# ---------------------------------------------------------------------------

_PACKAGE_RE = re.compile(r"\bpackage\s+([A-Za-z_][A-Za-z0-9_.]*)\s*;")

#: ``rpc Name ( [stream] ReqType ) returns ( [stream] RespType )``
_RPC_RE = re.compile(
    r"\brpc\s+([A-Za-z_][A-Za-z0-9_]*)\s*"
    r"\(\s*(stream\s+)?([A-Za-z_][A-Za-z0-9_.]*)\s*\)\s*"
    r"returns\s*\(\s*(stream\s+)?([A-Za-z_][A-Za-z0-9_.]*)\s*\)",
)

#: Field declaration inside a message body. We only need the *name*;
#: the type may be a primitive, a dotted type reference, a
#: ``map<K,V>``, or ``repeated <type>``. The trailing ``= N ;`` shape
#: distinguishes real fields from ``oneof``/``option`` boilerplate.
_FIELD_RE = re.compile(
    r"^\s*(?:repeated\s+|optional\s+|required\s+)?"
    r"(?:map\s*<[^>]+>|[A-Za-z_][A-Za-z0-9_.]*)\s+"
    r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\d+\s*[;\[]",
)

#: ``NAME = <int>;`` inside an enum body.
_ENUM_MEMBER_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*-?\d+\s*[;\[]",
)

_SERVICE_HEADER_RE = re.compile(r"\bservice\s+([A-Za-z_][A-Za-z0-9_]*)\b")
_MESSAGE_HEADER_RE = re.compile(r"\bmessage\s+([A-Za-z_][A-Za-z0-9_]*)\b")
_ENUM_HEADER_RE = re.compile(r"\benum\s+([A-Za-z_][A-Za-z0-9_]*)\b")

# ---------------------------------------------------------------------------
# Block walker
# ---------------------------------------------------------------------------

def _find_block(text: str, header_end: int) -> tuple[int, int] | None:
    """Locate the ``{ ... }`` block that starts at or after *header_end*.

    Returns a ``(body_start, body_end)`` slice (exclusive of the outer
    braces). Drops the block when the braces are unbalanced -- per the
    static-truth policy, omission is preferred over a guess at what
    the author meant.
    """
    brace_open = text.find("{", header_end)
    if brace_open == -1:
        return None
    depth = 0
    i = brace_open
    while i < len(text):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return (brace_open + 1, i)
        i += 1
    return None

# ---------------------------------------------------------------------------
# Block-specific parsers
# ---------------------------------------------------------------------------

def _parse_service(
    text: str, name: str, body_start: int, body_end: int
) -> ProtoService:
    service = ProtoService(name=name)
    body = text[body_start:body_end]
    for match in _RPC_RE.finditer(body):
        service.rpcs.append(
            ProtoRpc(
                service=name,
                name=match.group(1),
                request_type=match.group(3),
                response_type=match.group(5),
                client_stream=bool(match.group(2)),
                server_stream=bool(match.group(4)),
            )
        )
    return service

def _parse_enum(
    text: str, name: str, body_start: int, body_end: int
) -> ProtoEnum:
    enum = ProtoEnum(name=name)
    body = text[body_start:body_end]
    for line in body.splitlines():
        m = _ENUM_MEMBER_RE.match(line)
        if m:
            enum.members.append(m.group(1))
    return enum

def _collect_fields(chunk: str, out: list[str]) -> None:
    for line in chunk.splitlines():
        m = _FIELD_RE.match(line)
        if not m:
            continue
        name = m.group(1)
        if name not in out:
            out.append(name)

def _parse_message(
    text: str,
    qualified_name: str,
    body_start: int,
    body_end: int,
    msg_bucket: list[ProtoMessage],
    enum_bucket: list[ProtoEnum],
) -> None:
    """Parse a message body into *msg_bucket* and *enum_bucket*.

    Nested ``message`` blocks become additional ``ProtoMessage``
    entries with dotted ``Outer.Inner`` names. Nested ``enum`` blocks
    are added to *enum_bucket* with the same dotted prefix. Fields are
    collected only at the current message's own nesting level.
    """
    msg = ProtoMessage(qualified_name=qualified_name)
    cursor = body_start
    while cursor < body_end:
        slice_ = text[cursor:body_end]
        m_msg = _MESSAGE_HEADER_RE.search(slice_)
        m_enum = _ENUM_HEADER_RE.search(slice_)
        next_msg_pos = cursor + m_msg.start() if m_msg else -1
        next_enum_pos = cursor + m_enum.start() if m_enum else -1
        candidates = [p for p in (next_msg_pos, next_enum_pos) if p != -1]
        next_pos = min(candidates) if candidates else -1
        if next_pos == -1:
            _collect_fields(text[cursor:body_end], msg.fields)
            break
        _collect_fields(text[cursor:next_pos], msg.fields)
        if next_pos == next_msg_pos:
            assert m_msg is not None
            header_end = next_pos + len(m_msg.group(0))
            block = _find_block(text, header_end)
            if block is None:
                break
            inner_qn = f"{qualified_name}.{m_msg.group(1)}"
            _parse_message(
                text, inner_qn, block[0], block[1], msg_bucket, enum_bucket
            )
            cursor = block[1] + 1
        else:
            assert m_enum is not None
            header_end = next_pos + len(m_enum.group(0))
            block = _find_block(text, header_end)
            if block is None:
                break
            enum_qn = f"{qualified_name}.{m_enum.group(1)}"
            enum_bucket.append(
                _parse_enum(text, enum_qn, block[0], block[1])
            )
            cursor = block[1] + 1
    msg_bucket.append(msg)

# ---------------------------------------------------------------------------
# Top-level parse
# ---------------------------------------------------------------------------

def parse_proto_text(text: str) -> ProtoFile:
    """Parse raw proto text into a ``ProtoFile`` structure.

    Unknown or malformed constructs are skipped rather than guessed
    at, matching the static-truth policy in ADR 0018. A file with
    zero recognised declarations is still returned as an empty
    ``ProtoFile``.
    """
    cleaned = _strip_comments(text)
    pkg_match = _PACKAGE_RE.search(cleaned)
    package = pkg_match.group(1) if pkg_match else ""
    pf = ProtoFile(package=package)

    cursor = 0
    while cursor < len(cleaned):
        svc = _SERVICE_HEADER_RE.search(cleaned[cursor:])
        msg = _MESSAGE_HEADER_RE.search(cleaned[cursor:])
        enm = _ENUM_HEADER_RE.search(cleaned[cursor:])
        positions: list[tuple[int, str, re.Match[str]]] = []
        if svc:
            positions.append((cursor + svc.start(), "service", svc))
        if msg:
            positions.append((cursor + msg.start(), "message", msg))
        if enm:
            positions.append((cursor + enm.start(), "enum", enm))
        if not positions:
            break
        positions.sort(key=lambda p: p[0])
        pos, kind, match = positions[0]
        name = match.group(1)
        header_end = pos + len(match.group(0))
        block = _find_block(cleaned, header_end)
        if block is None:
            break
        body_start, body_end = block
        if kind == "service":
            pf.services.append(
                _parse_service(cleaned, name, body_start, body_end)
            )
        elif kind == "message":
            _parse_message(
                cleaned, name, body_start, body_end, pf.messages, pf.enums
            )
        else:
            pf.enums.append(_parse_enum(cleaned, name, body_start, body_end))
        cursor = body_end + 1

    return pf
