"""Literal (``|``) and folded (``>``) block-scalar handling for ``_yaml``.

Extracted from ``weld/_yaml.py`` so the minimal parser can expand multi-line
block scalars without pushing that module over the line-count cap. Covers the
YAML block-scalar features the repo's config and GitHub Actions workflow files
actually use:

* literal style ``|`` -- interior newlines and relative indentation preserved;
* folded style ``>`` -- runs of non-empty lines joined by a single space, blank
  lines kept as newlines;
* chomping indicators -- clip (default, one trailing newline), strip (``-``, no
  trailing newline), keep (``+``, all trailing blank lines retained);
* an optional explicit indentation indicator digit (e.g. ``|2``).

It does NOT implement anchors, tags, or block scalars that appear as a bare
sequence entry (``- |``); those are out of scope for the minimal parser.
"""

from __future__ import annotations

_INDICATORS = ("|", ">")


def is_block_scalar_header(val: str) -> bool:
    """Return True when *val* (a ``key:``'s inline value) opens a block scalar.

    Recognises ``|`` / ``>`` optionally followed by a chomping indicator
    (``-``/``+``) and/or a single explicit-indentation digit, in either order
    per the YAML spec (``|2-`` and ``|-2`` are both valid). A trailing inline
    comment after the header (``| # note``) is tolerated.
    """
    return _parse_header(val) is not None


def _parse_header(val: str) -> tuple[str, str, int | None] | None:
    """Parse a block-scalar header into ``(style, chomp, explicit_indent)``.

    ``style`` is ``"|"`` or ``">"``; ``chomp`` is ``""`` (clip), ``"-"``
    (strip), or ``"+"`` (keep); ``explicit_indent`` is the indentation digit if
    present, else ``None``. Returns ``None`` when *val* is not a block-scalar
    header. An inline ``#`` comment after the indicators is ignored.
    """
    if not val:
        return None
    style = val[0]
    if style not in _INDICATORS:
        return None
    rest = val[1:]
    comment = rest.find("#")
    if comment != -1:
        rest = rest[:comment]
    rest = rest.strip()
    chomp = ""
    explicit: int | None = None
    for ch in rest:
        if ch in ("-", "+") and not chomp:
            chomp = ch
        elif ch.isdigit() and explicit is None:
            explicit = int(ch)
        else:
            # Any other trailing token means this is not a clean block header
            # (e.g. ``|foo``), so treat the value as a plain scalar.
            return None
    return style, chomp, explicit


def consume_block_scalar(
    val: str, lines: list[str], start: int, parent_indent: int
) -> tuple[str, int]:
    """Expand a block scalar and return ``(text, next_line_index)``.

    *val* is the header (``|``, ``>-``, ...). *start* is the index of the first
    body line (the line after the header). *parent_indent* is the indentation
    of the ``key:`` line that introduced the scalar; body lines must be more
    indented than that to belong to the block.

    The caller is responsible for only invoking this when
    :func:`is_block_scalar_header` is true for *val*.
    """
    header = _parse_header(val)
    assert header is not None, f"not a block-scalar header: {val!r}"
    style, chomp, explicit = header

    body, next_index = _collect_body(lines, start, parent_indent)
    block_indent = _resolve_indent(body, parent_indent, explicit)
    content_lines = [_dedent(line, block_indent) for line in body]

    if style == "|":
        text = "\n".join(content_lines)
    else:
        text = _fold(content_lines)
    return _apply_chomp(text, chomp), next_index


def _collect_body(
    lines: list[str], start: int, parent_indent: int
) -> tuple[list[str], int]:
    """Gather raw body lines that belong to the block, plus the stop index.

    A line belongs to the block when it is blank or indented strictly more than
    *parent_indent*. The first line that is non-blank and indented at or below
    *parent_indent* terminates the block. Trailing blank lines are kept here;
    chomping decides their fate later.
    """
    body: list[str] = []
    i = start
    while i < len(lines):
        line = lines[i].rstrip("\r")
        if line.strip() == "":
            body.append(line)
            i += 1
            continue
        indent = len(line) - len(line.lstrip())
        if indent <= parent_indent:
            break
        body.append(line)
        i += 1
    return body, i


def _resolve_indent(
    body: list[str], parent_indent: int, explicit: int | None
) -> int:
    """Determine the block's content indentation column.

    With an explicit indicator the column is ``parent_indent + explicit``.
    Otherwise it is the indentation of the first non-blank body line (YAML's
    auto-detection); an all-blank body resolves to ``parent_indent`` so every
    line dedents to empty.
    """
    if explicit is not None:
        return parent_indent + explicit
    for line in body:
        if line.strip() != "":
            return len(line) - len(line.lstrip())
    return parent_indent


def _dedent(line: str, block_indent: int) -> str:
    """Strip up to *block_indent* leading spaces; blank lines become empty.

    Lines shorter than the block indent (whitespace-only) collapse to ``""``;
    indentation beyond the block column is preserved verbatim.
    """
    if line.strip() == "":
        return ""
    return line[block_indent:]


def _fold(content_lines: list[str]) -> str:
    """Apply folded-style line folding to dedented content lines.

    Consecutive non-empty lines are joined by a single space; a blank line
    introduces a literal newline and is not itself folded. More-indented lines
    are kept literal per the spec, but the minimal parser folds uniformly --
    none of the repo's folded scalars use the indented-keep sub-feature.
    """
    out: list[str] = []
    prev_blank = True  # so the first line never gets a leading space
    for line in content_lines:
        if line == "":
            out.append("\n")
            prev_blank = True
            continue
        if prev_blank:
            out.append(line)
        else:
            out.append(" " + line)
        prev_blank = False
    return "".join(out)


def _apply_chomp(text: str, chomp: str) -> str:
    """Apply the chomping indicator to *text* (which has no trailing newline).

    Clip (``""``) -> exactly one trailing newline; strip (``"-"``) -> none;
    keep (``"+"``) -> preserve every trailing line break verbatim. For folded
    text the interior already encodes blank lines as newlines, so we normalise
    the tail first, then re-add per the indicator.

    An entirely empty scalar (no content lines belong to the block) is the empty
    string under every indicator: clip's "single trailing newline" applies only
    when there is content, matching ``yaml.safe_load`` (``x: |`` with no body
    -> ``''``).
    """
    stripped = text.rstrip("\n")
    if not stripped:
        # Empty body: keep retains whatever trailing newlines were present (the
        # block was nothing but blank lines); clip/strip collapse to "".
        return text if chomp == "+" else ""
    if chomp == "-":
        return stripped
    if chomp == "+":
        return stripped + text[len(stripped):]
    return stripped + "\n"
