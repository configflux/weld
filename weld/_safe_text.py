"""Terminal-safety escaping for every byte ``wd`` writes to a tty.

Weld node ids are *derived from the scanned tree* -- file paths and symbol
names -- so their content is chosen by whoever wrote the repository, not by
Weld. A repository containing a file named ``foo\x1b[2J.py`` yields a node id
carrying a real ANSI control sequence. Every ``wd`` command that renders ids
back as human text would then replay those bytes into the operator's terminal:
clearing the screen, moving the cursor, recolouring, or overwriting a line
with text that misrepresents what the command actually found.

This module is the single sanitization contract for that boundary. Three
rules make it safe to apply broadly:

**Escape, never strip.** ``foo\x1b[2Jbar`` renders as ``foo\\x1b[2Jbar``, not
``foo[2Jbar``. A stripped id is a *lie* -- it looks like a legitimate id the
operator could copy, paste, and act on. An escaped one is visibly hostile and
still round-trips to the true bytes by inspection.

**Apply at the write boundary, never inside a formatter.** The formatters in
:mod:`weld._cli_render` and friends stay pure functions of their payload, and
each surface has exactly one place where a rendered string becomes bytes.
:mod:`tools.lint_terminal_safety` is the standing guard that a new write site
cannot skip either boundary.

**Never change the value, only its encoding.** The text helpers escape into
text that a human reads; the JSON helper escapes into ``\\uXXXX``, which every
conformant JSON parser decodes back to the original code point. A machine
consumer of ``--json`` therefore sees byte-identical values before and after,
which is what lets the CLI and MCP surfaces move together without breaking
their byte-identity parity contract.

What is escaped
---------------
Human text (:func:`sanitize_terminal_text`, :func:`sanitize_terminal_line`):

* **C0** (``\\x00``-``\\x1f``) -- ESC above all, but also CR, which overwrites
  the current line, and BEL/backspace.
* **DEL** (``\\x7f``).
* **C1** (``\\x80``-``\\x9f``) -- a UTF-8 terminal decodes these as controls
  in their own right; ``\\x9b`` *is* CSI, so it opens a control sequence with
  no ESC involved.
* **Bidi overrides and isolates** (``U+202A``-``U+202E``,
  ``U+2066``-``U+2069``) -- the "Trojan Source" set. These reorder glyphs, so
  a node id can *render* as a different id than the one it is. They are
  explicit formatting controls with no legitimate role in a path or symbol
  name. The directional *marks* (``U+200E`` LRM, ``U+200F`` RLM, ``U+061C``
  ALM) are deliberately left alone: they occur in genuine right-to-left
  content, and they nudge ordering rather than override it.

Serialized JSON (:func:`sanitize_json_text`, :func:`dumps_safe_json`):

* **The same set, minus C0**, which ``json.dumps`` already escapes by
  construction. What it passes through verbatim under ``ensure_ascii=False``
  is exactly DEL, C1 and the bidi controls -- so those are re-spelled as
  ``\\uXXXX``.

The two boundaries deliberately cover the same characters. It is tempting to
argue that JSON is machine-facing and needs only the *executable* classes,
but this CLI disproves it: ``wd list`` and ``wd dump`` print JSON to a
terminal with no flag at all, and ``--json`` is routinely piped through a
pager. An operator reads these by eye, so a reordered filename deceives here
exactly as it would in a rendered block. The escape costs nothing to say it:
``\\u202e`` parses back to the same character, so a machine consumer's value
is unchanged either way.

Printable non-ASCII (accents, CJK, emoji) is legitimate content and is left
alone on both boundaries -- which is why ``ensure_ascii=True`` is the wrong
tool for the JSON half. The C0 allowlist mirrors the existing repo idiom in
:mod:`weld._telemetry_redact`.

Scope: this is the *output* contract. On-disk artifacts (``graph.json``,
telemetry, state sidecars) keep their bytes: they are not terminal renders,
and ``graph.json`` in particular has an ADR 0012 determinism contract plus
enrichment fingerprints that a byte change would invalidate.

Known residual (deliberate, not an oversight)
---------------------------------------------
:func:`sanitize_terminal_text` must preserve ``\\n``/``\\t`` because it runs
over already-rendered multi-line blocks where those *are* the layout. An id
containing a newline can therefore still inject a plausible-looking extra
line into a rendered block. That is output spoofing, not terminal control:
strictly weaker, and unfixable at this boundary -- it needs per-field escaping
inside each renderer. The one-line stderr contract does not share the problem;
it uses :func:`sanitize_terminal_line`, which escapes newlines too. This
limit is accepted, not outstanding.

Packaging: kept stdlib-only and wired as the leaf ``//weld:safe_text``
micro-library. :mod:`weld._notice` sits directly above it and is itself a
leaf target so ``//weld/cross_repo`` and ``//weld:contract`` can emit
notices without a cycle through ``//weld:runtime`` -- this module must not
drag ``:runtime`` in behind it and break that.
"""

from __future__ import annotations

import json
import re
from typing import Any, Final

#: Explicit bidirectional *override* and *isolate* controls -- the Trojan
#: Source set (CVE-2021-42574). Spelled once so the two text patterns below
#: cannot drift apart, and spelled as regex ``\uXXXX`` escapes rather than as
#: the characters themselves: a source file holding raw overrides would
#: reorder its *own* display, which is the very attack this closes.
#: Directional marks are excluded on purpose; see the module docstring.
_BIDI: Final[str] = "\\u202a-\\u202e\\u2066-\\u2069"

#: Characters that are never safe to hand to a terminal, minus the layout
#: whitespace each public helper decides to keep. ``\x09`` (TAB) and ``\x0a``
#: (LF) are carved out of the C0 span here and re-added by
#: :data:`_LINE_UNSAFE` when a caller needs a single line.
_BLOCK_UNSAFE: Final[re.Pattern[str]] = re.compile(
    rf"[\x00-\x08\x0b-\x1f\x7f-\x9f{_BIDI}]"
)

#: As :data:`_BLOCK_UNSAFE`, but LF is unsafe too: used where the output
#: contract is exactly one line, so a smuggled newline cannot forge a second
#: line that looks like another (real) diagnostic.
_LINE_UNSAFE: Final[re.Pattern[str]] = re.compile(
    rf"[\x00-\x08\x0a-\x1f\x7f-\x9f{_BIDI}]"
)

#: The classes that survive ``json.dumps``. C0 is absent because the encoder
#: escapes every one of those itself; what it passes through verbatim under
#: ``ensure_ascii=False`` is exactly DEL, C1, and the bidi set.
_JSON_UNSAFE: Final[re.Pattern[str]] = re.compile(rf"[\x7f-\x9f{_BIDI}]")


def _escape(match: re.Match[str]) -> str:
    """Render one unsafe character as a visible escape.

    ``\\xNN`` for a byte-range code point, ``\\uNNNN`` above it -- writing
    ``\\x202e`` for a bidi override would read as ``\\x20`` followed by a
    literal ``2e``, which is exactly the kind of ambiguity this module exists
    to remove.
    """
    code = ord(match.group())
    return f"\\x{code:02x}" if code < 0x100 else f"\\u{code:04x}"


def _json_escape(match: re.Match[str]) -> str:
    """Render one unsafe character as its JSON ``\\uXXXX`` escape.

    JSON has no ``\\xNN`` form, and this escape is the *encoding* of the same
    value: a parser decodes it back to the original code point.
    """
    return f"\\u{ord(match.group()):04x}"


def _sanitize(text: str, pattern: re.Pattern[str], escape) -> str:
    """Escape every *pattern* match in *text*, or return *text* untouched.

    The ``search`` guard is not premature optimization: the overwhelmingly
    common case is output with nothing to escape, and returning the original
    object keeps that path byte-identical and allocation-free.
    """
    if not pattern.search(text):
        return text
    return pattern.sub(escape, text)


def sanitize_terminal_text(text: str) -> str:
    """Make an already-rendered *block* of human text safe to print.

    Preserves ``\\n`` and ``\\t`` because they are the rendered layout. Use
    this at the write boundary of any multi-line human render path.
    """
    return _sanitize(text, _BLOCK_UNSAFE, _escape)


def sanitize_terminal_line(text: str) -> str:
    """Make a *single-line* human message safe to print.

    Escapes newlines in addition to :func:`sanitize_terminal_text`'s classes,
    so a value interpolated into a one-line diagnostic cannot break out of it
    and forge a second line. Tabs survive -- they cannot escape the line.
    """
    return _sanitize(text, _LINE_UNSAFE, _escape)


def sanitize_json_text(text: str) -> str:
    """Escape DEL/C1/bidi in *already-serialized* JSON text.

    Safe to run over the whole document rather than per string value: JSON's
    grammar outside a string literal is printable ASCII plus space/TAB/CR/LF,
    so every character this touches is necessarily *inside* a string literal,
    where ``\\uXXXX`` is its exact escape. The result parses to a value equal
    to the input's, character for character.

    Takes serialized text rather than an object so a caller that must not
    change its bytes on disk -- ``dumps_graph`` and ``dumps_communities``,
    whose output is also the persisted artifact -- can escape only the copy
    going to the terminal.
    """
    return _sanitize(text, _JSON_UNSAFE, _json_escape)


def dumps_safe_json(
    data: Any, *, ensure_ascii: bool = False, **kwargs: Any,
) -> str:
    """Serialize *data* to JSON that is safe to write to a terminal.

    The emit-side counterpart to :func:`sanitize_terminal_text`: every ``wd``
    ``--json`` writer and the MCP serializer go through here, so the two
    surfaces cannot diverge in the bytes they produce.

    ``ensure_ascii`` defaults to ``False``, the long-standing contract of the
    read surface -- accents, CJK and emoji stay readable, and turning it on
    would escape all of them to solve a control-character problem. A caller
    that *already* emits pure ASCII keeps its exact bytes by passing ``True``;
    there the escape pass is a no-op, because ``ensure_ascii=True`` neutralizes
    DEL/C1 on its own. Routing that case through here anyway is deliberate:
    the safety then holds unconditionally, rather than resting on a keyword a
    later edit could quietly drop. Remaining ``kwargs`` (``indent``,
    ``sort_keys``, ...) pass through to :func:`json.dumps` unchanged.
    """
    return sanitize_json_text(
        json.dumps(data, ensure_ascii=ensure_ascii, **kwargs)
    )
