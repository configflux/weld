"""File-level leading doc-comment extraction, feeding test_peer's
``props.summary`` reader for Go/Rust/TypeScript/Java test files.

bd cw4f (ADR 0125 follow-up): a test file's
own summary needs the file's OWN leading comment block -- the same "first
statement in the file" shape ``ast.get_docstring`` reads for Python, never a
specific symbol's doc comment. That is a structurally SIMPLER question than
:mod:`weld.strategies._ts_doc_comments`'s symbol-association walk (no
``@name`` capture, no per-declaration-kind wrapper depth, no modifier/
attribute skip needed to reach a *named* symbol) -- which is what lets this
module ship Go/Rust/TypeScript/Java today while ADR 0124 keeps those last two
languages' SYMBOL-level readers deferred. TypeScript's and Java's ADR 0124
deferral reasons were specifically about wrapper depth / attribute position
relative to a named declaration, both sidestepped when there is no
declaration to attach to. C# does NOT ship here either way -- see the bd
comment on 7ui6 for why its deferral reason (the comment's own content being
structured XML) is untouched by the symbol-vs-file distinction.

Two shapes, verified by direct tree-sitter probe (ADR 0124's own
methodology) rather than assumed -- and the split between them is NOT
"Go/Rust vs TypeScript/Java" the way symbol-level summaries split; it is
Go alone vs. everyone else, because only Go's tooling (``go doc``) enforces
a real "zero blank lines" adjacency rule for a file's own doc comment:

* **Go** -- the file's doc comment is an OUTER comment: it must immediately
  precede (no blank source line) the ``package`` clause, exactly the shape
  :func:`weld.strategies._ts_doc_comments._leading_doc_comments`'s backward
  walk already solves correctly (it finds the closest contiguous comment run
  to a node and stops at the first gap, so a detached license header
  separated by a blank line is excluded rather than glued to the real doc
  comment). Reused verbatim -- the only new work is finding the anchor (the
  first non-comment child of the parse tree's root node), since there is no
  ``@name`` capture to hop from here.
* **TypeScript / Java / Rust** -- none of these three have Go's "must touch
  the next declaration" convention; a file-header comment followed by a
  blank line and then imports is completely idiomatic TS/JS/Java style (and
  Rust's ``//!``/``/*!`` INNER doc markers, grammar-tagged via
  ``inner_doc_comment_marker``, document the ENCLOSING scope rather than
  attaching to a following item at all). Verified the hard way: an early
  version of this module used the Go backward walk for all three and it
  returned "" against the pinned TypeScript fixture
  (``weld/tests/fixtures/tier1/typescript/sample_typescript/src/
  geometry.test.ts``), which opens with a header comment, a blank line, then
  its first ``import`` -- the backward walk's zero-gap requirement rejected
  the header outright. All three instead get a forward walk
  (:func:`_leading_file_comment_prefix`): collect the maximal row-adjacent
  run starting at the very first node in the file, with no "attaches to what
  follows" requirement -- the same purely-positional contract
  ``ast.get_docstring`` already uses for Python (first statement in the
  file, full stop; nothing about what comes after it matters).

Both shapes delegate the paragraph/collapse/bound reduction to the same
:func:`weld.strategies._doc_summary.collapse_summary` every other
``props.summary`` writer uses, so a detached license header inside a run (or
a blank ``//!`` paragraph marker) is trimmed the same way a Python
docstring's second paragraph already is.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Sequence

from weld.strategies._doc_summary import collapse_summary
from weld.strategies._ts_doc_comments import (
    _go_is_doc_comment,
    _go_is_skippable,
    _go_join_text,
    _immediately_precedes,
    _leading_doc_comments,
    _rust_join_text,
)
from weld.strategies._ts_parse import load_ts_language

# ---------------------------------------------------------------------------
# Generic pieces -- no language-specific logic below this line.
# ---------------------------------------------------------------------------


def _parse(file_path: Path, language: str):
    """Parse *file_path* with *language*'s grammar.

    Returns ``(tree, source_bytes)``, or ``(None, b"")`` on any failure --
    missing grammar, unreadable file, or a parse-time exception. This reader
    must never interrupt ``wd discover``: the test-peer file node is built
    from its path alone regardless (see :mod:`weld.strategies.test_peer`'s
    module docstring), so a failure here just means this one channel stays
    ``""`` for this file, the same "always present, empty when absent"
    contract every other ``props.summary`` writer already honors.
    """
    try:
        import tree_sitter  # noqa: F811

        ts_lang = load_ts_language(language)
        lang_obj = tree_sitter.Language(ts_lang)
        parser = tree_sitter.Parser(lang_obj)
        source_bytes = file_path.read_bytes()
        tree = parser.parse(source_bytes)
        return tree, source_bytes
    except Exception:
        return None, b""


def _first_noncomment_child(root_node, is_doc: Callable[[object], bool]):
    """Return the first child of *root_node* that is not comment-shaped.

    ``None`` when every child qualifies as a comment (a file with no real
    declaration at all) or the file is empty -- both degrade to "no anchor
    to walk backward from", handled by the caller as an empty summary.
    """
    for child in root_node.children:
        if not is_doc(child):
            return child
    return None


def _leading_outer_file_comment(
    root_node,
    is_doc: Callable[[object], bool],
    is_skippable: Callable[[object], bool],
    join_text: Callable[[Sequence, bytes], str],
    source_bytes: bytes,
) -> str:
    """Return the collapsed leading OUTER comment for Go/TypeScript/Java.

    Finds the first non-comment top-level child (the file's own
    "definition node" stand-in) and reuses
    :func:`weld.strategies._ts_doc_comments._leading_doc_comments` on it
    unchanged -- the exact backward walk that already gets the
    detached-license-header case right for symbol-level doc comments.
    """
    anchor = _first_noncomment_child(root_node, is_doc)
    if anchor is None:
        return ""
    doc_nodes = _leading_doc_comments(anchor, is_doc, is_skippable)
    if not doc_nodes:
        return ""
    return collapse_summary(join_text(doc_nodes, source_bytes))


def _leading_file_comment_prefix(root_node, is_doc: Callable[[object], bool]) -> list:
    """Return the leading run of doc-eligible nodes, in source order.

    Walks forward from the very first child of *root_node* -- unlike a
    symbol's (or Go's file-level) doc comment, these conventions have no
    declaration the comment must be adjacent to, so this collects the
    maximal row-adjacent run starting at position 0 and stops at the first
    non-qualifying or non-adjacent node. A genuinely separate second comment
    block after a blank source line is excluded rather than glued to the
    first one -- the same class of misattribution
    :func:`weld.strategies._ts_doc_comments._leading_doc_comments` already
    guards against, just walked in the opposite direction since there is no
    following item to be adjacent to here.
    """
    nodes: list = []
    for child in root_node.children:
        if not is_doc(child):
            break
        if nodes and not _immediately_precedes(nodes[-1], child):
            break
        nodes.append(child)
    return nodes


# ---------------------------------------------------------------------------
# Go convention -- reuses _ts_doc_comments' Go helpers verbatim: any comment
# counts, nothing is skippable, same marker stripping.
# ---------------------------------------------------------------------------


def go_file_summary(file_path: Path) -> str:
    """Return the collapsed leading comment before *file_path*'s ``package``."""
    tree, source_bytes = _parse(file_path, "go")
    if tree is None:
        return ""
    return _leading_outer_file_comment(
        tree.root_node, _go_is_doc_comment, _go_is_skippable, _go_join_text,
        source_bytes,
    )


# ---------------------------------------------------------------------------
# TypeScript / Java convention -- both grammars expose top-of-file comments
# as direct root children (verified by probe); TypeScript tags every comment
# uniformly as ``comment``, Java splits ``line_comment``/``block_comment``
# (including Javadoc ``/** */``, which the grammar does not distinguish from
# a plain ``/* */`` block). Neither carries a Rust-style marker child, so
# "is a comment" and "is doc-eligible" are the same predicate for both, and
# both share one stripping rule: a ``//`` line drops its marker (+ one
# optional space), a ``/* */``/``/** */`` block drops its wrapper and any
# per-line leading ``*`` (the common JSDoc/Javadoc continuation style). Both
# use the forward walk (see module docstring): neither language requires
# the comment to touch the first real declaration.
# ---------------------------------------------------------------------------


def _strip_c_style_comment_node(text: str) -> str:
    if text.startswith("//"):
        rest = text[2:]
        return rest[1:] if rest.startswith(" ") else rest
    if text.startswith("/*") and text.endswith("*/"):
        lines = []
        for line in text[2:-2].split("\n"):
            stripped = line.strip()
            if stripped.startswith("*"):
                stripped = stripped[1:]
                if stripped.startswith(" "):
                    stripped = stripped[1:]
            lines.append(stripped)
        return "\n".join(lines)
    return text.strip()


def _c_style_join_text(nodes: Sequence, source_bytes: bytes) -> str:
    lines = [
        _strip_c_style_comment_node(
            source_bytes[n.start_byte:n.end_byte].decode("utf-8", errors="replace")
        )
        for n in nodes
    ]
    return "\n".join(lines)


def _ts_is_doc_comment(node) -> bool:
    return node.type == "comment"


def typescript_file_summary(file_path: Path) -> str:
    """Return the collapsed leading comment run at *file_path*'s top."""
    tree, source_bytes = _parse(file_path, "typescript")
    if tree is None:
        return ""
    nodes = _leading_file_comment_prefix(tree.root_node, _ts_is_doc_comment)
    if not nodes:
        return ""
    return collapse_summary(_c_style_join_text(nodes, source_bytes))


def _java_is_doc_comment(node) -> bool:
    return node.type in ("line_comment", "block_comment")


def java_file_summary(file_path: Path) -> str:
    """Return the collapsed leading comment run at *file_path*'s top."""
    tree, source_bytes = _parse(file_path, "java")
    if tree is None:
        return ""
    nodes = _leading_file_comment_prefix(tree.root_node, _java_is_doc_comment)
    if not nodes:
        return ""
    return collapse_summary(_c_style_join_text(nodes, source_bytes))


# ---------------------------------------------------------------------------
# Rust convention -- INNER doc markers (`//!`/`/*!`) document the enclosing
# module, so like TypeScript/Java above (but for a different reason) they
# use the forward walk rather than Go's backward-from-anchor one.
# ---------------------------------------------------------------------------


def _rust_is_inner_doc_comment(node) -> bool:
    if node.type not in ("line_comment", "block_comment"):
        return False
    return any(child.type == "inner_doc_comment_marker" for child in node.children)


def rust_file_summary(file_path: Path) -> str:
    """Return the collapsed leading ``//!``/``/*!`` run at *file_path*'s top."""
    tree, source_bytes = _parse(file_path, "rust")
    if tree is None:
        return ""
    nodes = _leading_file_comment_prefix(tree.root_node, _rust_is_inner_doc_comment)
    if not nodes:
        return ""
    return collapse_summary(_rust_join_text(nodes, source_bytes))
