"""Go source fact extraction for the inheritance edge emitter (ADR 0064 criterion 2).

The regex-based front half of :mod:`weld.strategies._go_inherits`: it scans
one Go source file and returns a :class:`FileFacts` describing the struct
embeddings, method receivers, and interface method sets the file declares.
Kept separate from the resolution/emission half so each module stays a
single cohesive responsibility (and within the line-count cap).

The regexes never read beyond a declaration body, so cost is linear in
source length and output is deterministic (source order). Comments are
blanked first so a commented-out declaration never contributes a phantom
edge -- the same comment-stripping contract the Rust/TypeScript extractors
use.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: ``type Name struct { ... }`` / ``type Name interface { ... }`` headers.
#: The body is captured up to the *first* ``}``; a nested anonymous struct
#: would truncate the body early -- conservatively fewer embeddings, never
#: a wrong one (a flat field list has no nested braces).
_STRUCT_RE = re.compile(
    r"\btype\s+(?P<name>[A-Za-z_]\w*)\s+struct\s*\{(?P<body>[^{}]*)\}",
    re.DOTALL,
)
_INTERFACE_RE = re.compile(
    r"\btype\s+(?P<name>[A-Za-z_]\w*)\s+interface\s*\{(?P<body>[^{}]*)\}",
    re.DOTALL,
)

#: Method header ``func (recv T) M(`` / ``func (recv *T) M(`` -- receiver
#: name optional (``func (T) M()`` is legal). A free function (``func F(``)
#: has no receiver clause and never matches.
_METHOD_RE = re.compile(
    r"\bfunc\s*\(\s*(?:[A-Za-z_]\w*\s+)?\*?(?P<recv>[A-Za-z_]\w*)\s*\)\s*"
    r"(?P<method>[A-Za-z_]\w*)\s*\(",
)

#: One struct-body line: an embedded field (bare type, opt. pointer /
#: qualified) vs. a named field (``Name Type``, two leading tokens).
_EMBED_LINE_RE = re.compile(r"^\s*(?P<ptr>\*?)\s*(?P<path>[A-Za-z_][\w.]*)\s*$")

#: Interface-body element: ``M(`` -> required method; bare ``Name`` ->
#: embedded-interface contributor.
_IFACE_METHOD_RE = re.compile(r"^\s*(?P<name>[A-Za-z_]\w*)\s*\(")
_IFACE_EMBED_RE = re.compile(r"^\s*(?P<name>[A-Za-z_][\w.]*)\s*$")

#: Line/block comment span, blanked to whitespace so a commented-out
#: declaration never stages a phantom edge. Mirrors the Rust/TS strippers.
_COMMENT_RE = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)


def _strip_comments(source_text: str) -> str:
    """Return *source_text* with line and block comments blanked out."""
    def _blank(match: re.Match) -> str:
        return "".join("\n" if ch == "\n" else " " for ch in match.group(0))
    return _COMMENT_RE.sub(_blank, source_text)


def short_name(path: str) -> str:
    """Return the final ``.``-separated segment (``shapes.Base`` -> ``Base``).

    The project symbol index is keyed by the declared short name, so the
    use-side package qualifier is dropped for lookup. A leading ``*`` is
    stripped by the caller's capture group and never reaches here.
    """
    return path.rsplit(".", 1)[-1]


@dataclass
class FileFacts:
    """Structural facts extracted from one Go source file.

    * ``embeddings`` -- ``(struct_short, base_short, base_full)`` per
      embedded struct field, in source order.
    * ``methods`` -- ``(receiver_short, method_name)`` per method
      declaration, in source order.
    * ``interfaces`` -- ``{interface_short: (method_set, embed_set)}``;
      ``method_set`` is the directly-declared method names and
      ``embed_set`` the embedded-interface short names folded in by
      :func:`weld.strategies._go_inherits.finalise`.
    """

    embeddings: list[tuple[str, str, str]] = field(default_factory=list)
    methods: list[tuple[str, str]] = field(default_factory=list)
    interfaces: dict[str, tuple[set[str], set[str]]] = field(default_factory=dict)


def _extract_struct_embeddings(text: str) -> list[tuple[str, str, str]]:
    """Return ``(struct_short, base_short, base_full)`` for embedded fields."""
    out: list[tuple[str, str, str]] = []
    for match in _STRUCT_RE.finditer(text):
        struct = match.group("name")
        for raw_line in match.group("body").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            embed = _EMBED_LINE_RE.match(line)
            if not embed:
                continue  # ``Name Type`` -> named field, not an embedding.
            base_full = f"{embed.group('ptr')}{embed.group('path')}"
            out.append((struct, short_name(embed.group("path")), base_full))
    return out


def _extract_interfaces(text: str) -> dict[str, tuple[set[str], set[str]]]:
    """Return ``{iface_short: (method_set, embedded_iface_set)}``."""
    out: dict[str, tuple[set[str], set[str]]] = {}
    for match in _INTERFACE_RE.finditer(text):
        methods: set[str] = set()
        embeds: set[str] = set()
        for raw_line in match.group("body").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            method = _IFACE_METHOD_RE.match(line)
            if method:
                methods.add(method.group("name"))
                continue
            embed = _IFACE_EMBED_RE.match(line)
            if embed:
                embeds.add(short_name(embed.group("name")))
        out[match.group("name")] = (methods, embeds)
    return out


def extract_file_facts(source_text: str) -> FileFacts:
    """Return the :class:`FileFacts` declared in *source_text*.

    Comments are blanked first so a commented-out ``type ... struct`` /
    ``interface`` / ``func`` never contributes a phantom edge.
    """
    text = _strip_comments(source_text)
    return FileFacts(
        embeddings=_extract_struct_embeddings(text),
        methods=[(m.group("recv"), m.group("method"))
                 for m in _METHOD_RE.finditer(text)],
        interfaces=_extract_interfaces(text),
    )


__all__ = ["FileFacts", "extract_file_facts", "short_name"]
