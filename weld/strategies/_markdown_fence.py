"""Fence-aware line scanning shared by weld's markdown heading extractors.

Every markdown scan in this package used to walk ``text.splitlines()`` with
no memory of fenced code blocks, so a ``## Added`` that exists only inside a
```` ``` ```` sample was indistinguishable from real document structure. In
this repository alone that misread 83 lines across the indexed globs:
``docs/release.md``'s changelog template contributed ``Added`` / ``Changed``
/ ``Fixed`` / ``vX.Y.Z - YYYY-MM-DD`` to that doc node's ``props.headings``,
so a query for "Added" could rank a release runbook on the strength of a
placeholder, and with ``extract_sections`` on the same lines mint section
nodes whose spans cover a code sample.

The scanner is deliberately narrower than CommonMark: it resolves fenced
blocks and nothing else. Indented code blocks, setext headings, and HTML
blocks stay unhandled, because the callers only ever asked "is this line
real document structure?" and fences are the answer that was missing.

Three rules here are not obvious and are each load-bearing:

* **Leading whitespace is allowed on both delimiters.** A column-anchored
  fence matches zero blocks in a document that nests its samples inside list
  items -- the failure that made the sibling scanner in
  ``tools/cycle_worker_scratch_contract_test.py`` match empty text until
  bd wm3z fixed it there.
* **A closer must be the same character, at least as long as its opener, and
  carry no info string.** Without the length rule a ```` ``` ```` *inside* a
  ```` ```` ```` block ends it early and the rest of the sample reads as
  document text again -- which is how a scanner that "handles fences" still
  emits phantom headings from a doc that shows fenced markdown.
* **A backtick run whose info string contains a backtick opens nothing.**
  That line is a paragraph carrying inline code (CommonMark 4.5); promoting
  it to an opener would swallow every heading below it until EOF, turning an
  over-reporting bug into a silent under-reporting one.

bd ve41
"""

from __future__ import annotations

from collections.abc import Iterator

#: Characters that can delimit a fenced code block (CommonMark 4.5).
_FENCE_CHARS = frozenset("`~")

#: Shortest run of a fence character that can open or close a block.
_MIN_FENCE_RUN = 3


def _fence_delimiter(line: str) -> tuple[str, int, str] | None:
    """Return ``(char, run, info)`` when *line* could delimit a fence.

    Purely syntactic: it reports the run of leading fence characters and the
    text trailing it, and leaves both info-string rules -- disqualifying a
    closer, and disqualifying a backtick opener -- to :func:`content_lines`,
    which is the only place that knows whether a block is already open.
    ``None`` means the line cannot delimit a fence at all.
    """

    stripped = line.strip()
    if not stripped or stripped[0] not in _FENCE_CHARS:
        return None
    char = stripped[0]
    run = len(stripped) - len(stripped.lstrip(char))
    if run < _MIN_FENCE_RUN:
        return None
    return char, run, stripped[run:].strip()


def content_lines(text: str) -> Iterator[tuple[int, str]]:
    """Yield ``(index, line)`` for each line of *text* outside a fenced block.

    ``index`` is the zero-based position in ``text.splitlines()``, so callers
    that report line numbers keep counting the whole document while reading
    only the parts of it that are prose. Delimiter lines are withheld along
    with the block body -- neither can be a heading. An unclosed fence runs
    to the end of the document, which is what CommonMark says it does.
    """

    open_char = ""
    open_run = 0
    for index, line in enumerate(text.splitlines()):
        delimiter = _fence_delimiter(line)
        if open_run:
            if delimiter is not None:
                char, run, info = delimiter
                if char == open_char and run >= open_run and not info:
                    open_char, open_run = "", 0
            continue
        if delimiter is not None:
            char, run, info = delimiter
            if char != "`" or "`" not in info:
                open_char, open_run = char, run
                continue
        yield index, line


def content_text(text: str) -> str:
    """Return *text* with every fenced code block removed.

    For scans that are not line-oriented. The inter-doc link regex in
    :mod:`weld.strategies.markdown` allows a link *label* to wrap across
    lines, so feeding it :func:`content_lines` one line at a time would fix
    fence-blindness by introducing a silent under-report -- the trade this
    module's docstring already names as the worse one. Joining the surviving
    prose keeps a wrapped label matching while a link that only ever renders
    as code stops minting an edge (bd w624).

    Line *numbers* are not preserved (the removed blocks are not padded), so
    this is for callers that report no positions. Callers that do want
    positions take :func:`content_lines`, which yields the original index.

    The one artifact: an unbalanced ``[`` in prose immediately above a fence
    can now pair with a ``](x.md)`` below it, because the block between them
    is gone. That is an over-report of one edge in a document that is already
    malformed, and it is strictly rarer than the wrapped label it protects.
    """

    return "\n".join(line for _index, line in content_lines(text))


def iter_headings(text: str) -> Iterator[tuple[int, int, str]]:
    """Yield ``(index, level, heading)`` per ATX heading outside a fence.

    ``level`` is the run of leading ``#`` and ``heading`` is the remainder,
    stripped. Callers filter: the doc strategy wants H2 for section nodes and
    H2/H3 for query tokens, the runbook strategy wants the first H1. Sharing
    the walk is the point -- three copies of "strip, count hashes, slice" is
    how only some of them learned about fences.

    A run must be followed by a space *and* something after it to count, so
    the empty ATX headings CommonMark allows (``##``, ``## ``) are not
    headings here -- the line is stripped before the run is measured, which
    is the same reason the ``startswith("## ")`` tests this replaced did not
    take them either. ``heading`` is therefore never empty.
    """

    for index, line in content_lines(text):
        stripped = line.strip()
        level = len(stripped) - len(stripped.lstrip("#"))
        if not 1 <= level <= 6:
            continue
        rest = stripped[level:]
        if not rest.startswith(" "):
            continue
        yield index, level, rest.strip()
