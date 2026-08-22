"""Leading doc-comment extraction for tree-sitter ``symbol:`` nodes.

ADR 0114/0118 gave Python ``file:``/``symbol:`` nodes ``props.summary`` from
their own docstring; both the read path (``weld.query_index``,
``weld._match_surface``) and the paragraph/collapse/bound reduction
(:mod:`weld.strategies._doc_summary`) are already generic. bd 5038-009x
(ADR 0118 follow-up) is the write-side extension for the languages parsed by
:mod:`weld.strategies.tree_sitter` -- Go and Rust, empirically verified (not
assumed) against this environment's installed grammars; TypeScript, Java, C#
and C++ are recorded as deferred in the mini-spec rather than shipped
half-verified (see the bd comment on the tracking issue and ADR 0118's
amendment for the per-language reasoning).

The mechanism is one generic backward walk over tree-sitter siblings, plus a
small per-language "convention" of four functions:

* ``definition_node`` -- given the ``@name`` capture from the language's
  existing ``exports`` query (the same query
  :func:`weld.strategies._ts_definitions.promote_definition_symbols` already
  keys its definition list from for every language but C#), return the node
  whose LEADING comment is the symbol's own doc comment. Usually the name
  node's direct parent; Go's grouped ``type ( A; B )`` form needs one extra
  hop (or not -- see :func:`_go_definition_node`).
  Reuses the exact query the exports list already runs; no new query
  is added and no shared ``.yaml`` query file changes.
* ``is_doc_comment`` -- which sibling comment types count as *this item's*
  documentation, per language convention (Go: any comment, since godoc has no
  distinct doc-comment syntax; Rust: only ``///``/``/** */``, not ``//!`` or
  plain ``//``, via the grammar's own marker child).
* ``is_skippable`` -- non-comment siblings that may sit between the doc
  comment and the item without breaking the association (Rust
  ``#[attribute]``; Go has none).
* ``join_text`` -- decode and strip each collected comment node's marker
  syntax, returning one joined string ready for
  :func:`weld.strategies._doc_summary.collapse_summary`.

Association correctness (the reviewable risk this module exists to get
right): a comment attaches to the FOLLOWING definition only when it is
immediately adjacent (no blank source line between them) AND starts on its
own source line (not trailing other code on the same line as e.g. ``func
A() {} // note`` -- which must NOT become the next declaration's doc
comment). Both checks are point-based, not a raw source-byte scan, and
account for the one real cross-grammar difference observed by direct probe:
Rust's ``line_comment``/``block_comment`` spans swallow their own trailing
newline (``end_point`` already sits at column 0 of the next row) while Go's
plain ``comment`` node does not -- :func:`_content_end_row` normalises that
away so the adjacency arithmetic is identical for both.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from weld.strategies._doc_summary import collapse_summary
from weld.strategies._ts_parse import ParseCache, ParseEntry, load_ts_language

# ---------------------------------------------------------------------------
# Generic backward walk -- no language-specific logic below this line.
# ---------------------------------------------------------------------------


def _content_end_row(node) -> int:
    """Return the row *node*'s own visible text ends on.

    Some grammars' comment nodes swallow their own trailing newline into the
    span (``end_point`` lands at column 0 of the *next* row); others do not.
    Verified by direct probe: Rust's ``line_comment``/``block_comment`` do,
    Go's ``comment`` does not. Correcting for the swallowed case here is what
    lets :func:`_immediately_precedes` use one row-arithmetic rule for every
    grammar instead of a per-language constant.
    """
    if node.end_point.column == 0 and node.end_point.row > node.start_point.row:
        return node.end_point.row - 1
    return node.end_point.row


def _immediately_precedes(earlier, later) -> bool:
    """True when *later* begins the very next source line after *earlier*.

    I.e. no blank line between them -- the adjacency half of "is this
    comment part of the following declaration's doc comment".
    """
    return later.start_point.row - _content_end_row(earlier) == 1


def _starts_own_line(node) -> bool:
    """True when *node* is not a trailing fragment of the line before it.

    Rejects the misattribution case ``func A() {} // trailing note`` above
    ``func B() {}``: the comment IS row-adjacent to ``B``, but it starts on
    ``A``'s own line rather than a fresh one, so it must not become B's doc
    comment. Verified against exactly this shape via direct probe.
    """
    prev = node.prev_sibling
    return prev is None or _content_end_row(prev) != node.start_point.row


def _leading_doc_comments(node, is_doc, is_skippable) -> list:
    """Return *node*'s leading doc-comment nodes, in source order.

    Walks backward over *node*'s siblings, first skipping any contiguous,
    adjacent, own-line ``is_skippable`` wrappers (Rust ``#[attribute]``
    sitting between the doc comment and the item it annotates), then
    collecting a contiguous, adjacent, own-line run of ``is_doc`` comments.
    Either walk stops at the first sibling that fails adjacency, own-line, or
    the relevant predicate -- a blank line, a non-doc comment, or real code
    all end the run without raising. Returns ``[]`` when nothing qualifies
    (no doc comment for this symbol), never ``None``, so callers can always
    iterate the result.
    """
    current = node
    while True:
        prev = current.prev_sibling
        if prev is None or not is_skippable(prev):
            break
        if not _immediately_precedes(prev, current) or not _starts_own_line(prev):
            break
        current = prev

    chain: list = []
    while True:
        prev = current.prev_sibling
        if prev is None or not is_doc(prev):
            break
        if not _immediately_precedes(prev, current) or not _starts_own_line(prev):
            break
        chain.append(prev)
        current = prev
    chain.reverse()
    return chain


# ---------------------------------------------------------------------------
# Go convention: godoc has no dedicated doc-comment syntax -- any comment
# immediately adjacent and on its own line counts, matching what `go doc`
# itself extracts. No attribute/annotation wrapper to skip.
# ---------------------------------------------------------------------------


def _go_definition_node(name_node):
    """Return the node whose leading comment is this Go symbol's doc comment.

    ``@name``'s parent is the definition directly (``function_declaration``,
    ``method_declaration``) except a ``type_spec``, whose *own* leading
    comment is the doc comment only inside a grouped ``type ( A; B )`` block
    (each spec keeps its own comment sibling there, verified by probe); a
    single-spec ``type X struct {...}`` instead anchors the doc comment on
    the outer ``type_declaration``, so only that shape hops up.
    """
    parent = name_node.parent
    if parent is None:
        return None
    if parent.type == "type_spec":
        grandparent = parent.parent
        if grandparent is not None and grandparent.type == "type_declaration":
            specs = [c for c in grandparent.children if c.type == "type_spec"]
            if len(specs) == 1:
                return grandparent
    return parent


def _go_is_doc_comment(node) -> bool:
    return node.type == "comment"


def _go_is_skippable(_node) -> bool:
    return False


def _strip_go_comment_line(text: str) -> str:
    if text.startswith("//"):
        rest = text[2:]
        return rest[1:] if rest.startswith(" ") else rest
    if text.startswith("/*") and text.endswith("*/"):
        return text[2:-2].strip()
    return text.strip()


def _go_join_text(nodes: Sequence, source_bytes: bytes) -> str:
    lines = [
        _strip_go_comment_line(
            source_bytes[n.start_byte:n.end_byte].decode("utf-8", errors="replace")
        )
        for n in nodes
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Rust convention: `///`/`/** */` are real doc comments (grammar-tagged via
# an `outer_doc_comment_marker` child); `//!`/`/*!` document the ENCLOSING
# scope, not the following item, and plain `//`/`/* */` are not
# rustdoc-eligible at all -- both excluded. `#[attribute]` wrappers between
# the doc comment and the item are skipped (verified against the pinned
# rust_project fixture itself, which has exactly this shape).
# ---------------------------------------------------------------------------


def _rust_is_doc_comment(node) -> bool:
    if node.type not in ("line_comment", "block_comment"):
        return False
    return any(child.type == "outer_doc_comment_marker" for child in node.children)


def _rust_is_skippable(node) -> bool:
    return node.type == "attribute_item"


def _rust_doc_child_text(node) -> str:
    for child in node.children:
        if child.type == "doc_comment":
            return child.text.decode("utf-8", errors="replace")
    return ""


def _strip_rust_line(text: str) -> str:
    text = text[:-1] if text.endswith("\n") else text
    return text[1:] if text.startswith(" ") else text


def _rust_join_text(nodes: Sequence, _source_bytes: bytes) -> str:
    lines: list[str] = []
    for node in nodes:
        raw = _rust_doc_child_text(node)
        if node.type == "block_comment":
            # Best-effort: a `/** ... */` block's interior lines commonly
            # lead with `*`/`* `. Untested against a real fixture (the
            # pinned rust_project uses `///` throughout) but must not raise.
            for part in raw.split("\n"):
                stripped = part.strip()
                if stripped.startswith("*"):
                    stripped = stripped[1:]
                    if stripped.startswith(" "):
                        stripped = stripped[1:]
                lines.append(stripped)
        else:
            lines.append(_strip_rust_line(raw))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Per-language dispatch + entry point.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _DocConvention:
    definition_node: Callable[[object], object | None]
    is_doc_comment: Callable[[object], bool]
    is_skippable: Callable[[object], bool]
    join_text: Callable[[Sequence, bytes], str]


_CONVENTIONS: dict[str, _DocConvention] = {
    "go": _DocConvention(
        definition_node=_go_definition_node,
        is_doc_comment=_go_is_doc_comment,
        is_skippable=_go_is_skippable,
        join_text=_go_join_text,
    ),
    "rust": _DocConvention(
        definition_node=lambda name_node: name_node.parent,
        is_doc_comment=_rust_is_doc_comment,
        is_skippable=_rust_is_skippable,
        join_text=_rust_join_text,
    ),
}


def supports_doc_summaries(language: str) -> bool:
    """True when *language* has a registered doc-comment convention."""
    return language in _CONVENTIONS


def extract_definition_summaries(
    file_path: Path,
    language: str,
    queries: dict[str, str],
    *,
    cache: ParseCache | None = None,
) -> dict[str, str] | None:
    """Map each ``exports``-captured symbol name to its leading doc comment.

    Returns ``None`` for a language with no registered convention (or a
    query set missing ``exports``) so :mod:`weld.strategies.tree_sitter` can
    leave ``props.summary`` entirely absent rather than stamp a fabricated
    ``""`` on a symbol this module never looked at -- ADR 0118's own
    per-language presence contract, one level out.

    Reuses the caller's :class:`ParseCache` when supplied: the ``exports``
    query and this file's parse are already primed by the
    ``parse_file_symbols`` call that ran moments earlier in the same
    discover loop iteration, so this issues no second parse and no second
    query compilation. Best-effort like every other tree-sitter query pass
    in this codebase -- a bad file or malformed query degrades to no
    summaries for it, never a crashed discover run.
    """
    convention = _CONVENTIONS.get(language)
    if convention is None:
        return None
    export_query_str = queries.get("exports", "")
    if not export_query_str:
        return None

    # Guarded like every other lazy tree-sitter import (ADR 0002): the
    # caller reaches this from extract() even when the parser was mocked
    # in, so an absent umbrella package must degrade to "no summaries",
    # never escape. An unguarded import here is what turned public CI red
    # at v0.23.0 (bd uaz2d).
    try:
        import tree_sitter  # noqa: F811
    except ImportError:
        return None

    try:
        if cache is not None:
            entry = cache.get_parse(file_path, language)
            if entry is None:
                ts_language_obj, parser = cache.get_or_load_language(
                    language, load_ts_language, tree_sitter,
                )
                source_bytes = file_path.read_bytes()
                tree = parser.parse(source_bytes)
                cache.store_parse(
                    file_path, language,
                    ParseEntry(
                        tree=tree, source_bytes=source_bytes,
                        language_obj=ts_language_obj, parser=parser,
                    ),
                )
            else:
                ts_language_obj = entry.language_obj
                tree = entry.tree
                source_bytes = entry.source_bytes
            query = cache.get_or_compile_query(
                language, "exports", export_query_str, ts_language_obj, tree_sitter,
            )
        else:
            ts_lang = load_ts_language(language)
            ts_language_obj = tree_sitter.Language(ts_lang)
            parser = tree_sitter.Parser(ts_language_obj)
            source_bytes = file_path.read_bytes()
            tree = parser.parse(source_bytes)
            query = tree_sitter.Query(ts_language_obj, export_query_str)

        cursor = tree_sitter.QueryCursor(query)
        summaries: dict[str, str] = {}
        for _pattern_idx, capture_dict in cursor.matches(tree.root_node):
            for name_node in capture_dict.get("name", []):
                name = name_node.text.decode("utf-8")
                if name in summaries:
                    continue  # first occurrence wins, matching _dedupe's rule
                def_node = convention.definition_node(name_node)
                if def_node is None:
                    summaries[name] = ""
                    continue
                doc_nodes = _leading_doc_comments(
                    def_node, convention.is_doc_comment, convention.is_skippable,
                )
                if not doc_nodes:
                    summaries[name] = ""
                    continue
                joined = convention.join_text(doc_nodes, source_bytes)
                summaries[name] = collapse_summary(joined)
        return summaries
    except Exception:
        return {}
