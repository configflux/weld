"""bd 5038-009x (ADR 0118 follow-up): the leading-doc-comment backward walk.

ADR 0118 gave Python ``symbol:`` nodes ``props.summary`` from their own
docstring; the read path was already generic. This module is the write-side
extension for Go and Rust (the two tree-sitter languages verified against
this environment's installed grammars -- see the mini-spec comment on the
tracking issue for the full per-language enumeration and why
TypeScript/Java/C#/C++ are deferred). See also
:mod:`weld.tests.weld_ts_doc_comments_integration_test` for the public entry
point, the real strategy wiring, and the read-path reachability proof; split
into two files to keep each under the line-count cap.

:class:`LeadingDocCommentsWalkTest` pins the highest-risk logic directly
against real parse trees (comment-to-symbol association): adjacency,
blank-line rejection, the trailing-same-line misattribution guard ("``func
A() {} // note`` above ``func B(){}`` must not give B a doc comment"),
multi-line chains, and (Rust) attribute-skipping plus the outer/inner/plain
doc-comment-marker distinction. Red-first during development: every case
here failed before :mod:`weld.strategies._ts_doc_comments` existed.
"""

from __future__ import annotations

import unittest

import tree_sitter
import tree_sitter_go
import tree_sitter_rust

from weld.strategies._ts_doc_comments import (
    _go_is_doc_comment,
    _go_is_skippable,
    _leading_doc_comments,
    _rust_is_doc_comment,
    _rust_is_skippable,
)


def _parse(language_module, src: str):
    lang = tree_sitter.Language(language_module.language())
    parser = tree_sitter.Parser(lang)
    return parser.parse(src.encode("utf-8")), src.encode("utf-8")


def _find_all(node, pred):
    if pred(node):
        yield node
    for child in node.children:
        yield from _find_all(child, pred)


def _identifier(tree_root, node_type: str, text: str):
    for n in _find_all(tree_root, lambda n: n.type == node_type and n.text == text.encode()):
        return n
    raise AssertionError(f"no {node_type} node with text {text!r} found")


class LeadingDocCommentsWalkTest(unittest.TestCase):
    """The generic backward walk, pinned directly against real parse trees."""

    def test_go_adjacent_single_line_comment_attaches(self) -> None:
        tree, src = _parse(tree_sitter_go, "package p\n\n// Doc for X\nfunc X() {}\n")
        name = _identifier(tree.root_node, "identifier", "X")
        chain = _leading_doc_comments(name.parent, _go_is_doc_comment, _go_is_skippable)
        self.assertEqual([src[n.start_byte:n.end_byte] for n in chain], [b"// Doc for X"])

    def test_go_multiline_comment_chain_joins_in_source_order(self) -> None:
        tree, src = _parse(
            tree_sitter_go,
            "package p\n\n// Line one.\n// Line two.\nfunc X() {}\n",
        )
        name = _identifier(tree.root_node, "identifier", "X")
        chain = _leading_doc_comments(name.parent, _go_is_doc_comment, _go_is_skippable)
        texts = [src[n.start_byte:n.end_byte] for n in chain]
        self.assertEqual(texts, [b"// Line one.", b"// Line two."])

    def test_go_blank_line_before_declaration_rejects_attachment(self) -> None:
        tree, _src = _parse(
            tree_sitter_go, "package p\n\n// Stray.\n\nfunc X() {}\n",
        )
        name = _identifier(tree.root_node, "identifier", "X")
        chain = _leading_doc_comments(name.parent, _go_is_doc_comment, _go_is_skippable)
        self.assertEqual(chain, [])

    def test_go_trailing_same_line_comment_does_not_attach_to_next_decl(self) -> None:
        """``func A() {} // note`` above ``func B(){}`` must not give B a
        doc comment -- the comment is row-adjacent to B but starts on A's
        line, not its own. The historical misattribution risk this module
        exists to avoid getting wrong."""
        tree, _src = _parse(
            tree_sitter_go, "package p\n\nfunc A() {} // trailing\nfunc B() {}\n",
        )
        name = _identifier(tree.root_node, "identifier", "B")
        chain = _leading_doc_comments(name.parent, _go_is_doc_comment, _go_is_skippable)
        self.assertEqual(chain, [])

    def test_rust_outer_doc_comment_attaches(self) -> None:
        tree, src = _parse(tree_sitter_rust, "/// Doc for x.\npub fn x() -> i32 { 1 }\n")
        name = _identifier(tree.root_node, "identifier", "x")
        chain = _leading_doc_comments(name.parent, _rust_is_doc_comment, _rust_is_skippable)
        self.assertEqual(len(chain), 1)
        self.assertIn(b"Doc for x", src[chain[0].start_byte:chain[0].end_byte])

    def test_rust_inner_doc_marker_is_excluded(self) -> None:
        """``//!`` documents the ENCLOSING scope, not the following item."""
        tree, _src = _parse(tree_sitter_rust, "//! Module doc.\npub fn x() -> i32 { 1 }\n")
        name = _identifier(tree.root_node, "identifier", "x")
        chain = _leading_doc_comments(name.parent, _rust_is_doc_comment, _rust_is_skippable)
        self.assertEqual(chain, [])

    def test_rust_plain_comment_is_excluded(self) -> None:
        """Plain ``//`` is not rustdoc-eligible, unlike Go's convention."""
        tree, _src = _parse(tree_sitter_rust, "// not doc\npub fn x() -> i32 { 1 }\n")
        name = _identifier(tree.root_node, "identifier", "x")
        chain = _leading_doc_comments(name.parent, _rust_is_doc_comment, _rust_is_skippable)
        self.assertEqual(chain, [])

    def test_rust_attribute_between_doc_comment_and_item_is_skipped(self) -> None:
        """Verified against the pinned rust_project fixture's own shape:
        ``/// doc`` then ``#[derive(...)]`` then the item."""
        tree, src = _parse(
            tree_sitter_rust,
            "/// Doc for Rect.\n#[derive(Debug)]\npub struct Rect { pub w: f64 }\n",
        )
        name = _identifier(tree.root_node, "type_identifier", "Rect")
        chain = _leading_doc_comments(name.parent, _rust_is_doc_comment, _rust_is_skippable)
        self.assertEqual(len(chain), 1)
        self.assertIn(b"Doc for Rect", src[chain[0].start_byte:chain[0].end_byte])

    def test_rust_attribute_with_no_doc_comment_behind_it_stays_empty(self) -> None:
        tree, _src = _parse(
            tree_sitter_rust, "#[derive(Debug)]\npub struct Rect { pub w: f64 }\n",
        )
        name = _identifier(tree.root_node, "type_identifier", "Rect")
        chain = _leading_doc_comments(name.parent, _rust_is_doc_comment, _rust_is_skippable)
        self.assertEqual(chain, [])


if __name__ == "__main__":
    unittest.main()
