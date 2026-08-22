"""Unit contract for the unresolved-symbol-sentinel purge (bd oao53).

``python_callgraph``, ``_go_inherits``, ``_rust_inherits``,
``_typescript_inherits``, ``_java_inherits``, ``_cpp_inherits``, and
``_ts_call_graph`` each mint a ``symbol:unresolved:<name>`` node lazily
(``nodes.setdefault``) as the edge target for a call/inherits/implements
reference that does not resolve to a known symbol. The node carries no
``props.file``, so ``weld.discovery_state.purge_stale_nodes``'s ordinary
``props.file`` match never purges it directly -- the same blind spot bd
pkz2s found for ``graph_closure``'s external package placeholders
(``weld/tests/discovery_state_external_package_purge_test.py``), on a
third, disjoint placeholder shape.

These tests exercise
:func:`weld._discover_unresolved_symbol_purge.emptied_unresolved_symbol_node_ids`,
the widened
:func:`weld._discover_external_package_purge.emptied_placeholder_node_ids`,
and :func:`weld.discovery_state.purge_stale_nodes` directly against small
synthetic graphs -- the same level the sibling purge-unit-test files already
test at -- so the negative space (a resolved cross-glob stub with no
``props.file`` either, a non-``symbol`` node under the prefix) and the
composition with the other two placeholder rules are each pinned
independently of any one language's real extraction code. The real
``discover()`` end-to-end proof (multiple languages, byte-identical
incremental==full) lives in
``weld/tests/incremental_unresolved_symbol_purge_equivalence_test.py``.
"""

from __future__ import annotations

import unittest

from weld._discover_external_package_purge import emptied_placeholder_node_ids
from weld._discover_orphan_edges import orphaned_producer_files
from weld._discover_unresolved_symbol_purge import (
    emptied_unresolved_symbol_node_ids,
)
from weld.discovery_state import purge_stale_nodes


def _unresolved_symbol(name: str = "Base", *, language: str = "go") -> dict:
    return {
        "type": "symbol",
        "label": name,
        "props": {
            "language": language,
            "source_strategy": f"{language}_inherits",
            "authority": "derived",
            "confidence": "speculative",
            "kind": "unresolved",
            "origin": "unresolved",
            "qualname": name,
        },
    }


def _resolved_cross_glob_stub(module: str = "some.mod", qual: str = "func") -> dict:
    """``make_resolved_target_node``-shaped: no ``props.file``, not under
    the ``symbol:unresolved:`` prefix -- oao53's tracked non-goal.
    ``authority`` is always ``"derived"`` in real output -- exact now that
    bd n4nvt keys a 4th rule on this props shape."""
    return {
        "type": "symbol",
        "label": qual,
        "props": {
            "module": module,
            "qualname": qual,
            "language": "python",
            "source_strategy": "python_callgraph",
            "authority": "derived",
            "confidence": "speculative",
            "roles": ["implementation"],
            "origin": "external",
        },
    }


def _file_node(rel_path: str) -> dict:
    return {"type": "file", "label": rel_path, "props": {"file": rel_path}}


def _calls(frm: str, to: str, *, provenance_file: str | None = None) -> dict:
    props = {"source_strategy": "python_callgraph", "confidence": "speculative"}
    if provenance_file is not None:
        props["provenance"] = {"file": provenance_file}
    return {"from": frm, "to": to, "type": "calls", "props": props}


def _inherits(frm: str, to: str, *, provenance_file: str | None = None) -> dict:
    props = {"source_strategy": "go_inherits", "confidence": "speculative"}
    if provenance_file is not None:
        props["provenance"] = {"file": provenance_file}
    return {"from": frm, "to": to, "type": "inherits", "props": props}


class EmptiedUnresolvedSymbolNodeIdsTest(unittest.TestCase):
    """The pure predicate, isolated from the purge call site."""

    def test_zero_inbound_edges_is_emptied(self) -> None:
        nodes = {"symbol:unresolved:Base": _unresolved_symbol()}
        self.assertEqual(
            emptied_unresolved_symbol_node_ids(nodes, []),
            {"symbol:unresolved:Base"},
        )

    def test_surviving_calls_inbound_is_not_emptied(self) -> None:
        nodes = {
            "symbol:unresolved:foo": _unresolved_symbol("foo", language="python"),
            "symbol:py:a:use_it": {"type": "symbol", "label": "use_it", "props": {}},
        }
        edges = [_calls("symbol:py:a:use_it", "symbol:unresolved:foo")]
        self.assertEqual(emptied_unresolved_symbol_node_ids(nodes, edges), set())

    def test_surviving_inherits_inbound_is_not_emptied(self) -> None:
        """A DIFFERENT edge type than the calls-edge test above -- proves the
        rule counts inbound edges of any type, not just ``calls``."""
        nodes = {
            "symbol:unresolved:Base": _unresolved_symbol(),
            "symbol:go:a:Widget": {"type": "symbol", "label": "Widget", "props": {}},
        }
        edges = [_inherits("symbol:go:a:Widget", "symbol:unresolved:Base")]
        self.assertEqual(emptied_unresolved_symbol_node_ids(nodes, edges), set())

    def test_two_edge_types_from_two_languages_both_count_toward_one_id(
        self,
    ) -> None:
        """The sentinel id is a SHARED namespace keyed on bare name only: a
        Python ``calls`` edge and an unrelated Go ``inherits`` edge can
        BOTH target the identical ``symbol:unresolved:Base`` id at once.
        The inbound-edge accumulator must not be confused by multiple edge
        types/sources landing on the same id -- it is a set, not a count
        keyed per type."""
        nodes = {
            "symbol:unresolved:Base": _unresolved_symbol(),
            "symbol:py:a:use_it": {"type": "symbol", "label": "use_it", "props": {}},
            "symbol:go:w:Widget": {"type": "symbol", "label": "Widget", "props": {}},
        }
        edges = [
            _calls("symbol:py:a:use_it", "symbol:unresolved:Base"),
            _inherits("symbol:go:w:Widget", "symbol:unresolved:Base"),
        ]
        self.assertEqual(emptied_unresolved_symbol_node_ids(nodes, edges), set())

    def test_resolved_cross_glob_stub_is_never_emptied(self) -> None:
        """No ``props.file`` either, but not ``symbol:unresolved:``-prefixed
        -- a different id shape and minting condition (bd oao53 non-goal),
        so this rule's id-prefix guard must not reach it."""
        nodes = {"symbol:py:some.mod:func": _resolved_cross_glob_stub()}
        self.assertEqual(emptied_unresolved_symbol_node_ids(nodes, []), set())

    def test_non_symbol_type_under_the_prefix_is_never_emptied(self) -> None:
        """Defensive: no minting strategy has ever emitted a non-``symbol``
        node under this prefix, but the type guard must hold structurally,
        not just by convention."""
        nodes = {
            "symbol:unresolved:Base": {
                "type": "concept", "label": "Base", "props": {},
            },
        }
        self.assertEqual(emptied_unresolved_symbol_node_ids(nodes, []), set())

    def test_missing_or_non_dict_props_does_not_raise(self) -> None:
        nodes = {"symbol:unresolved:Base": {"type": "symbol", "label": "Base"}}
        self.assertEqual(
            emptied_unresolved_symbol_node_ids(nodes, []), {"symbol:unresolved:Base"},
        )

    def test_non_string_node_id_is_skipped_defensively(self) -> None:
        # Malformed input (not producible by any real strategy) must not
        # raise -- mirrors the sibling purge modules' defensive posture.
        nodes = {"symbol:unresolved:Base": _unresolved_symbol()}
        edges = [{"from": "x", "to": 42, "type": "calls", "props": {}}]
        self.assertEqual(
            emptied_unresolved_symbol_node_ids(nodes, edges), {"symbol:unresolved:Base"},
        )


class EmptiedPlaceholderNodeIdsUnionTest(unittest.TestCase):
    """The three-way union entry point :mod:`weld.discovery_state` calls."""

    def test_unions_all_three_rules(self) -> None:
        nodes = {
            "package:python:pkg": {
                "type": "package", "label": "pkg",
                "props": {
                    "source_strategy": "python_package", "confidence": "definite",
                    "dir": "pkg", "roles": ["package"],
                },
            },
            "package:go:strings": {
                "type": "package", "label": "strings",
                "props": {
                    "source_strategy": "graph_closure", "authority": "external",
                    "confidence": "inferred",
                },
            },
            "symbol:unresolved:Base": _unresolved_symbol(),
        }
        self.assertEqual(
            emptied_placeholder_node_ids(nodes, []),
            {"package:python:pkg", "package:go:strings", "symbol:unresolved:Base"},
        )

    def test_a_node_matching_no_rule_is_never_returned(self) -> None:
        """bd n4nvt's 4th rule now catches a zero-inbound
        ``_resolved_cross_glob_stub()``, so this switched to a ``concept``
        node -- a shape no placeholder rule has ever targeted."""
        nodes = {"c:x": {"type": "concept", "label": "x", "props": {}}}
        self.assertEqual(emptied_placeholder_node_ids(nodes, []), set())


class PurgeStaleNodesUnresolvedSymbolTest(unittest.TestCase):
    """The integrated call site: purge_stale_nodes folds the new check in."""

    def test_sole_referencer_deletion_purges_the_sentinel_too(self) -> None:
        nodes = {
            "file:a": _file_node("a.py"),
            "symbol:py:a:use_it": {
                "type": "symbol", "label": "use_it",
                "props": {"file": "a.py"},
            },
            "symbol:unresolved:foo": _unresolved_symbol("foo", language="python"),
        }
        edges = [
            _calls(
                "symbol:py:a:use_it", "symbol:unresolved:foo",
                provenance_file="a.py",
            ),
        ]
        surviving_nodes, surviving_edges = purge_stale_nodes(
            dict(nodes), list(edges), {"a.py"},
        )
        self.assertEqual(surviving_nodes, {})
        self.assertEqual(surviving_edges, [])

    def test_non_sole_referencer_keeps_the_sentinel_and_its_surviving_edge(
        self,
    ) -> None:
        """No over-purge: two files reference the same unresolved name;
        deleting ONE must leave the sentinel alive with exactly the
        surviving file's edge."""
        nodes = {
            "file:a": _file_node("a.py"),
            "symbol:py:a:use_it": {
                "type": "symbol", "label": "use_it", "props": {"file": "a.py"},
            },
            "file:b": _file_node("b.py"),
            "symbol:py:b:also_use_it": {
                "type": "symbol", "label": "also_use_it", "props": {"file": "b.py"},
            },
            "symbol:unresolved:foo": _unresolved_symbol("foo", language="python"),
        }
        edges = [
            _calls(
                "symbol:py:a:use_it", "symbol:unresolved:foo",
                provenance_file="a.py",
            ),
            _calls(
                "symbol:py:b:also_use_it", "symbol:unresolved:foo",
                provenance_file="b.py",
            ),
        ]
        surviving_nodes, surviving_edges = purge_stale_nodes(
            dict(nodes), list(edges), {"a.py"},
        )
        self.assertEqual(
            set(surviving_nodes),
            {"file:b", "symbol:py:b:also_use_it", "symbol:unresolved:foo"},
        )
        self.assertEqual(
            surviving_edges,
            [
                _calls(
                    "symbol:py:b:also_use_it", "symbol:unresolved:foo",
                    provenance_file="b.py",
                ),
            ],
        )

    def test_referenced_resolved_cross_glob_stub_survives_unrelated_deletion(
        self,
    ) -> None:
        """A resolved stub must not be collaterally purged by an unrelated
        stale file -- survives here via a live inbound edge, not because no
        rule reaches it (bd n4nvt added one for the zero-inbound case; see
        discovery_state_resolved_stub_integration_test.py)."""
        nodes = {
            "file:app": _file_node("app.py"),
            "symbol:py:a:use_it": {
                "type": "symbol", "label": "use_it", "props": {"file": "a.py"},
            },
            "symbol:py:some.mod:func": _resolved_cross_glob_stub(),
        }
        edges = [_calls("symbol:py:a:use_it", "symbol:py:some.mod:func",
                         provenance_file="a.py")]
        surviving_nodes, _ = purge_stale_nodes(dict(nodes), edges, {"app.py"})
        self.assertIn("symbol:py:some.mod:func", surviving_nodes)

    def test_all_three_placeholder_rules_fire_together_in_one_purge_call(
        self,
    ) -> None:
        nodes = {
            "file:pkg/__init__": _file_node("pkg/__init__.py"),
            "package:python:pkg": {
                "type": "package", "label": "pkg",
                "props": {
                    "source_strategy": "python_package", "confidence": "definite",
                    "dir": "pkg", "roles": ["package"],
                },
            },
            "file:a": _file_node("a.go"),
            "package:go:strings": {
                "type": "package", "label": "strings",
                "props": {
                    "source_strategy": "graph_closure", "authority": "external",
                    "confidence": "inferred",
                },
            },
            "file:c": _file_node("c.py"),
            "symbol:py:c:use_it": {
                "type": "symbol", "label": "use_it", "props": {"file": "c.py"},
            },
            "symbol:unresolved:foo": _unresolved_symbol("foo", language="python"),
        }
        edges = [
            {"from": "package:python:pkg", "to": "file:pkg/__init__",
             "type": "contains", "props": {"source_strategy": "python_package"}},
            {
                "from": "file:a", "to": "package:go:strings", "type": "depends_on",
                "props": {"source_strategy": "graph_closure"},
            },
            _calls(
                "symbol:py:c:use_it", "symbol:unresolved:foo",
                provenance_file="c.py",
            ),
        ]
        surviving_nodes, surviving_edges = purge_stale_nodes(
            dict(nodes), list(edges), {"pkg/__init__.py", "a.go", "c.py"},
        )
        self.assertEqual(surviving_nodes, {})
        self.assertEqual(surviving_edges, [])

    def test_this_rule_can_never_leave_a_dangling_inbound_edge(self) -> None:
        """Unlike pkz2s's ``depends_on``-only signal (which can strand a
        DIFFERENT edge type pointing at a node it purges -- the dangling
        shape :mod:`weld._discover_orphan_edges` exists to catch), this
        rule's signal IS total inbound edge count across every type -- so a
        node it purges can never have a surviving edge of ANY kind pointing
        at it: "purge this node" and "some edge still targets it" are
        mutually exclusive by construction. Proven here with two DIFFERENT
        edge types (``calls`` from Python, ``inherits`` from Go) targeting
        the SAME shared sentinel id from two different files: deleting only
        the ``calls``-edge's file must leave both the node and the
        ``inherits`` edge fully intact, with nothing orphaned."""
        nodes = {
            "file:a": _file_node("a.py"),
            "symbol:py:a:use_it": {
                "type": "symbol", "label": "use_it", "props": {"file": "a.py"},
            },
            "symbol:unresolved:Base": _unresolved_symbol(),
            "file:w": _file_node("w.go"),
            "symbol:go:w:Widget": {
                "type": "symbol", "label": "Widget", "props": {"file": "w.go"},
            },
        }
        edges = [
            _calls(
                "symbol:py:a:use_it", "symbol:unresolved:Base",
                provenance_file="a.py",
            ),
            _inherits(
                "symbol:go:w:Widget", "symbol:unresolved:Base",
                provenance_file="w.go",
            ),
        ]
        surviving_nodes, surviving_edges = purge_stale_nodes(
            dict(nodes), list(edges), {"a.py"},
        )
        self.assertIn("symbol:unresolved:Base", surviving_nodes)
        self.assertEqual(
            surviving_edges,
            [
                _inherits(
                    "symbol:go:w:Widget", "symbol:unresolved:Base",
                    provenance_file="w.go",
                ),
            ],
        )
        orphaned = orphaned_producer_files(surviving_nodes, surviving_edges)
        self.assertEqual(orphaned, set())

    def test_unaffected_run_is_a_no_op(self) -> None:
        nodes = {"symbol:unresolved:Base": _unresolved_symbol()}
        surviving_nodes, surviving_edges = purge_stale_nodes(dict(nodes), [], set())
        self.assertEqual(surviving_nodes, nodes)
        self.assertEqual(surviving_edges, [])


if __name__ == "__main__":
    unittest.main()
