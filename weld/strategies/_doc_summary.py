"""Shared paragraph/collapse/bound reduction for ``props.summary``.

ADR 0114 gave ``file:`` nodes ``props.summary`` from a Python module's own
opening docstring paragraph; ADR 0118 extended the same reduction one level
down to ``symbol:`` nodes from their own ``FunctionDef`` / ``AsyncFunctionDef``
/ ``ClassDef`` docstring. Both lived in :mod:`weld.strategies._python_anchor`
as ``module_summary`` / ``symbol_summary``, sharing a private
``_summary_from_docstring(doc: str)`` so the paragraph/collapse/bound rule
was defined exactly once.

That reduction was never actually Python-specific -- it takes an
already-extracted string and never touches ``ast``. bd 5038-009x (ADR 0118
follow-up) reuses it verbatim for non-Python doc comments (Go ``//``, Rust
``///``) rather than duplicating the contract a second time, so this module
is the shared home both callers import from. :mod:`weld.strategies._python_anchor`
re-exports :data:`MAX_SUMMARY_LEN` and delegates ``module_summary`` /
``symbol_summary`` to :func:`collapse_summary` unchanged; the new
:mod:`weld.strategies._ts_doc_comments` calls it directly on joined
doc-comment text.
"""

from __future__ import annotations

import re

#: Upper bound on a recorded module or symbol summary, in characters.
#:
#: Not a formatting rule -- a bound. ``props.summary`` is indexed, and
#: ``query_index.candidate_nodes`` substring-scans every indexed token once per
#: query token, so an unbounded string from a generated or pathological
#: docstring/doc-comment would land directly on the query hot path. The same
#: reasoning caps ``props.constants`` in :mod:`weld._file_index_extractors`.
#: Measured over this repo's 675 Python modules the longest opening paragraph
#: is 150 characters, so the cap does not fire here for modules; it exists for
#: the repos weld is pointed at next, and (bd p6ke) for the much larger symbol
#: population, where a docstring's opening paragraph is shorter on average but
#: far more numerous.
MAX_SUMMARY_LEN = 320

#: A paragraph break: a newline, an optionally whitespace-only line, a newline.
#: A Python ``ast.get_docstring`` dedents but does not strip trailing spaces
#: from a line, so a separator carrying the file's own indentation is
#: invisible to a reader and to a literal ``"\n\n"`` split alike. A joined
#: Go/Rust doc-comment run reproduces the same pattern by construction: an
#: empty comment line between two non-empty ones joins to an empty string
#: between two ``"\n"`` separators, i.e. exactly this pattern.
_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n")


def collapse_summary(doc: str) -> str:
    """Return the opening paragraph of *doc*, collapsed and bounded.

    Shared by every ``props.summary`` writer regardless of source language:
    :mod:`weld.strategies._python_anchor` (``ast.get_docstring`` text) and
    :mod:`weld.strategies._ts_doc_comments` (a joined run of leading ``//``
    / ``///`` doc-comment lines). Takes already-extracted text, never a
    parser node, so the reduction itself carries no language-specific logic:
    split on the first blank-line paragraph break, collapse internal
    whitespace (including the joining newlines between doc-comment lines)
    onto a single line, and bound the result at :data:`MAX_SUMMARY_LEN`,
    breaking on a word boundary where possible.

    Empty string in, empty string out -- callers pass ``""`` for "no doc
    comment found" and get back the same "always present, empty when absent"
    value ADR 0114/0118 already document.
    """
    if not doc:
        return ""
    collapsed = " ".join(_PARAGRAPH_BREAK.split(doc, maxsplit=1)[0].split())
    if len(collapsed) <= MAX_SUMMARY_LEN:
        return collapsed
    head = collapsed[:MAX_SUMMARY_LEN]
    boundary = head.rfind(" ")
    return (head[:boundary] if boundary > 0 else head).rstrip()
