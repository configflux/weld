"""Integrated call-site tests for the resolved-cross-glob-stub purge (bd n4nvt).

Split out from ``discovery_state_resolved_stub_purge_test.py`` to keep both
files under the 400-line cap -- the same split ukt95 already took for
``discovery_state_external_package_purge_test.py`` /
``discovery_state_manifest_dependency_purge_test.py``. See that sibling
file's module docstring for the full mechanism writeup; this file exercises
:func:`weld.discovery_state.purge_stale_nodes` directly (the real call site,
not just the isolated predicate), including composition with the other
three placeholder rules and the no-dangling-edge guarantee.
"""

from __future__ import annotations

import unittest

from weld._discover_orphan_edges import orphaned_producer_files
from weld.discovery_state import purge_stale_nodes


def _resolved_stub(module: str = "pkg_b.callee", qual: str = "target_func") -> dict:
    """Exact shape ``make_resolved_target_node`` stamps."""
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


def _real_symbol(rel_path: str, qual: str, *, module: str = "pkg_b.callee") -> dict:
    """A genuinely-walked ``python_callgraph`` symbol -- the negative space
    this rule must never reach, even at zero inbound edges."""
    return {
        "type": "symbol",
        "label": qual,
        "props": {
            "file": rel_path,
            "module": module,
            "qualname": qual,
            "line": 1,
            "kind": "function",
            "language": "python",
            "source_strategy": "python_callgraph",
            "authority": "derived",
            "confidence": "definite",
            "roles": ["implementation"],
            "origin": "project",
        },
    }


def _unresolved_sentinel(name: str = "Base") -> dict:
    """oao53's sentinel shape -- used only to prove all four placeholder
    rules compose correctly in one purge call."""
    return {
        "type": "symbol",
        "label": name,
        "props": {
            "module": "",
            "qualname": name,
            "language": "python",
            "resolved": False,
            "resolution": "unresolved",
            "source_strategy": "python_callgraph",
            "authority": "derived",
            "confidence": "speculative",
            "roles": ["implementation"],
            "origin": "unresolved",
        },
    }


def _file_node(rel_path: str) -> dict:
    return {"type": "file", "label": rel_path, "props": {"file": rel_path}}


def _calls(frm: str, to: str, *, provenance_file: str | None = None) -> dict:
    props = {"source_strategy": "python_callgraph", "confidence": "definite"}
    if provenance_file is not None:
        props["provenance"] = {"file": provenance_file}
    return {"from": frm, "to": to, "type": "calls", "props": props}


def _inherits(frm: str, to: str, *, provenance_file: str | None = None) -> dict:
    props = {"source_strategy": "python_callgraph", "confidence": "definite"}
    if provenance_file is not None:
        props["provenance"] = {"file": provenance_file}
    return {"from": frm, "to": to, "type": "inherits", "props": props}


class PurgeStaleNodesResolvedStubTest(unittest.TestCase):
    """The integrated call site: purge_stale_nodes folds the new check in."""

    def test_sole_referencer_deletion_purges_the_stub_too(self) -> None:
        nodes = {
            "file:pkg_a/caller": _file_node("pkg_a/caller.py"),
            "symbol:py:pkg_a.caller:do_call": {
                "type": "symbol", "label": "do_call",
                "props": {"file": "pkg_a/caller.py"},
            },
            "symbol:py:pkg_b.callee:target_func": _resolved_stub(),
        }
        edges = [
            _calls(
                "symbol:py:pkg_a.caller:do_call",
                "symbol:py:pkg_b.callee:target_func",
                provenance_file="pkg_a/caller.py",
            ),
        ]
        surviving_nodes, surviving_edges = purge_stale_nodes(
            dict(nodes), list(edges), {"pkg_a/caller.py"},
        )
        self.assertEqual(surviving_nodes, {})
        self.assertEqual(surviving_edges, [])

    def test_non_sole_referencer_keeps_the_stub_and_its_surviving_edge(self) -> None:
        """No over-purge: two files call the same cross-glob target;
        deleting ONE must leave the stub alive with exactly the surviving
        file's edge."""
        nodes = {
            "file:pkg_a/caller": _file_node("pkg_a/caller.py"),
            "symbol:py:pkg_a.caller:do_call": {
                "type": "symbol", "label": "do_call",
                "props": {"file": "pkg_a/caller.py"},
            },
            "file:pkg_a/other_caller": _file_node("pkg_a/other_caller.py"),
            "symbol:py:pkg_a.other_caller:also_call": {
                "type": "symbol", "label": "also_call",
                "props": {"file": "pkg_a/other_caller.py"},
            },
            "symbol:py:pkg_b.callee:target_func": _resolved_stub(),
        }
        edges = [
            _calls(
                "symbol:py:pkg_a.caller:do_call",
                "symbol:py:pkg_b.callee:target_func",
                provenance_file="pkg_a/caller.py",
            ),
            _calls(
                "symbol:py:pkg_a.other_caller:also_call",
                "symbol:py:pkg_b.callee:target_func",
                provenance_file="pkg_a/other_caller.py",
            ),
        ]
        surviving_nodes, surviving_edges = purge_stale_nodes(
            dict(nodes), list(edges), {"pkg_a/caller.py"},
        )
        self.assertEqual(
            set(surviving_nodes),
            {
                "file:pkg_a/other_caller",
                "symbol:py:pkg_a.other_caller:also_call",
                "symbol:py:pkg_b.callee:target_func",
            },
        )
        self.assertEqual(
            surviving_edges,
            [
                _calls(
                    "symbol:py:pkg_a.other_caller:also_call",
                    "symbol:py:pkg_b.callee:target_func",
                    provenance_file="pkg_a/other_caller.py",
                ),
            ],
        )

    def test_already_zero_inbound_stub_is_purged_by_an_unrelated_stale_file(
        self,
    ) -> None:
        """A stub with zero inbound edges FROM THE START (nothing in this
        synthetic graph ever called it -- unlike the sole-referencer-deleted
        tests above, no edge to it exists at all here) is purged the moment
        ANY purge pass runs, even one triggered by a totally unrelated
        file's staleness -- it does not need to be the deleted file's own
        stub to go. This is the exact shape that used to be asserted to
        SURVIVE before bd n4nvt (see
        discovery_state_unresolved_symbol_purge_test.py's history): a fresh
        full discover of a tree missing app.py never mints this stub either
        (nothing in this graph calls it), so purging it here keeps
        incremental equal to full."""
        nodes = {
            "file:app": _file_node("app.py"),
            "symbol:py:pkg_b.callee:target_func": _resolved_stub(),
        }
        surviving_nodes, _ = purge_stale_nodes(dict(nodes), [], {"app.py"})
        self.assertNotIn("symbol:py:pkg_b.callee:target_func", surviving_nodes)

    def test_real_symbol_survives_unrelated_deletion_even_at_zero_inbound(
        self,
    ) -> None:
        """THE over-purge proof at the integrated call site: a real symbol
        node with zero inbound ``calls`` edges must survive an unrelated
        stale file in the same run -- it is not this rule's business that
        nothing calls it, only that it was genuinely walked (props.file
        set, confidence definite)."""
        nodes = {
            "file:app": _file_node("app.py"),
            "symbol:py:pkg_b.callee:target_func": _real_symbol(
                "pkg_b/callee.py", "target_func",
            ),
        }
        surviving_nodes, _ = purge_stale_nodes(dict(nodes), [], {"app.py"})
        self.assertIn("symbol:py:pkg_b.callee:target_func", surviving_nodes)

    def test_all_four_placeholder_rules_fire_together_in_one_purge_call(self) -> None:
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
            "symbol:unresolved:foo": _unresolved_sentinel("foo"),
            "file:pkg_a/caller": _file_node("pkg_a/caller.py"),
            "symbol:py:pkg_a.caller:do_call": {
                "type": "symbol", "label": "do_call",
                "props": {"file": "pkg_a/caller.py"},
            },
            "symbol:py:pkg_b.callee:target_func": _resolved_stub(),
        }
        edges = [
            {"from": "package:python:pkg", "to": "file:pkg/__init__",
             "type": "contains", "props": {"source_strategy": "python_package"}},
            {
                "from": "file:a", "to": "package:go:strings", "type": "depends_on",
                "props": {"source_strategy": "graph_closure"},
            },
            _calls("symbol:py:c:use_it", "symbol:unresolved:foo",
                   provenance_file="c.py"),
            _calls(
                "symbol:py:pkg_a.caller:do_call",
                "symbol:py:pkg_b.callee:target_func",
                provenance_file="pkg_a/caller.py",
            ),
        ]
        surviving_nodes, surviving_edges = purge_stale_nodes(
            dict(nodes), list(edges),
            {"pkg/__init__.py", "a.go", "c.py", "pkg_a/caller.py"},
        )
        self.assertEqual(surviving_nodes, {})
        self.assertEqual(surviving_edges, [])

    def test_this_rule_can_never_leave_a_dangling_inbound_edge(self) -> None:
        """Unlike pkz2s's ``depends_on``-only signal, this rule's signal IS
        total inbound edge count across every type, so a node it purges can
        never have a surviving edge of ANY kind pointing at it. Proven with
        two DIFFERENT edge types (``calls``, ``inherits``) targeting the
        SAME stub id from two different files: deleting only the
        ``calls``-edge's file must leave both the node and the ``inherits``
        edge fully intact, with nothing orphaned."""
        nodes = {
            "file:pkg_a/caller": _file_node("pkg_a/caller.py"),
            "symbol:py:pkg_a.caller:do_call": {
                "type": "symbol", "label": "do_call",
                "props": {"file": "pkg_a/caller.py"},
            },
            "symbol:py:pkg_b.base:Base": _resolved_stub("pkg_b.base", "Base"),
            "file:pkg_a/sub": _file_node("pkg_a/sub.py"),
            "symbol:py:pkg_a.sub:Sub": {
                "type": "symbol", "label": "Sub", "props": {"file": "pkg_a/sub.py"},
            },
        }
        edges = [
            _calls(
                "symbol:py:pkg_a.caller:do_call", "symbol:py:pkg_b.base:Base",
                provenance_file="pkg_a/caller.py",
            ),
            _inherits(
                "symbol:py:pkg_a.sub:Sub", "symbol:py:pkg_b.base:Base",
                provenance_file="pkg_a/sub.py",
            ),
        ]
        surviving_nodes, surviving_edges = purge_stale_nodes(
            dict(nodes), list(edges), {"pkg_a/caller.py"},
        )
        self.assertIn("symbol:py:pkg_b.base:Base", surviving_nodes)
        self.assertEqual(
            surviving_edges,
            [
                _inherits(
                    "symbol:py:pkg_a.sub:Sub", "symbol:py:pkg_b.base:Base",
                    provenance_file="pkg_a/sub.py",
                ),
            ],
        )
        orphaned = orphaned_producer_files(surviving_nodes, surviving_edges)
        self.assertEqual(orphaned, set())

    def test_unaffected_run_is_a_no_op(self) -> None:
        nodes = {"symbol:py:pkg_b.callee:target_func": _resolved_stub()}
        surviving_nodes, surviving_edges = purge_stale_nodes(dict(nodes), [], set())
        self.assertEqual(surviving_nodes, nodes)
        self.assertEqual(surviving_edges, [])


if __name__ == "__main__":
    unittest.main()
