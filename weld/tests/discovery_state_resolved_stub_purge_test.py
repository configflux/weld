"""Unit contract for the resolved-cross-glob-stub purge (bd n4nvt).

``weld.strategies._python_origin.make_resolved_target_node`` mints a
speculative stub at a REAL ``symbol:py:<module>:<qual>`` id for a
call/inherits/references/scope-call/decorates target that resolves via
import-table syntax to a project-shaped id the current batch did not itself
walk (a cross-glob target, or a module outside every configured glob). The
node carries no ``props.file``, so ``weld.discovery_state.purge_stale_nodes``'s
ordinary ``props.file`` match never purges it directly -- the same blind spot
bd pkz2s and bd oao53 found for two other placeholder shapes
(``weld/tests/discovery_state_external_package_purge_test.py``,
``weld/tests/discovery_state_unresolved_symbol_purge_test.py``), on a fourth,
disjoint shape.

Unlike oao53's ``symbol:unresolved:*`` sentinel, this id shape
(``symbol:py:<module>:<qual>``) is the SAME shape a genuinely-walked, real,
``definite`` symbol node uses -- these tests specifically pin the negative
space: a real symbol with zero inbound ``calls`` edges (an exported,
uncalled function -- extremely common and entirely legitimate) must never be
purged by this rule.

These tests exercise
:func:`weld._discover_resolved_stub_purge.emptied_resolved_stub_node_ids`
and the widened
:func:`weld._discover_external_package_purge.emptied_placeholder_node_ids`
directly against small synthetic graphs -- the same level the sibling
purge-unit-test files already test at. The integrated
``purge_stale_nodes`` call-site tests live in a sibling file, split out to
keep both under the 400-line cap (the same split ukt95 already took for
``discovery_state_external_package_purge_test.py`` /
``discovery_state_manifest_dependency_purge_test.py``):
``weld/tests/discovery_state_resolved_stub_integration_test.py``. The real
``discover()`` end-to-end proof (byte-identical incremental==full, plus the
stub -> real upgrade path) lives in
``weld/tests/incremental_resolved_stub_purge_equivalence_test.py``.
"""

from __future__ import annotations

import unittest

from weld._discover_external_package_purge import emptied_placeholder_node_ids
from weld._discover_resolved_stub_purge import emptied_resolved_stub_node_ids


def _resolved_stub(module: str = "pkg_b.callee", qual: str = "target_func") -> dict:
    """Exact shape ``make_resolved_target_node`` stamps -- ``authority``
    always ``"derived"`` (hardcoded), ``origin`` here left as ``"external"``
    (a plausible value for a module outside every configured glob), and no
    ``props.file`` at all."""
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
    """A genuinely-walked ``python_callgraph`` symbol -- ``definite``
    confidence, ``props.file`` set. The negative space this rule must never
    reach, however many inbound ``calls`` edges it has (including zero)."""
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
    """``make_sentinel_node``-shaped: the SAME props fingerprint (no file,
    speculative, derived, python_callgraph) but under oao53's
    ``symbol:unresolved:`` prefix -- the negative space this rule's
    id-prefix guard must never reach (disjoint from oao53's own rule)."""
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


class EmptiedResolvedStubNodeIdsTest(unittest.TestCase):
    """The pure predicate, isolated from the purge call site."""

    def test_zero_inbound_edges_is_emptied(self) -> None:
        nodes = {"symbol:py:pkg_b.callee:target_func": _resolved_stub()}
        self.assertEqual(
            emptied_resolved_stub_node_ids(nodes, []),
            {"symbol:py:pkg_b.callee:target_func"},
        )

    def test_surviving_calls_inbound_is_not_emptied(self) -> None:
        nodes = {
            "symbol:py:pkg_b.callee:target_func": _resolved_stub(),
            "symbol:py:pkg_a.caller:do_call": {
                "type": "symbol", "label": "do_call", "props": {},
            },
        }
        edges = [
            _calls(
                "symbol:py:pkg_a.caller:do_call",
                "symbol:py:pkg_b.callee:target_func",
            ),
        ]
        self.assertEqual(emptied_resolved_stub_node_ids(nodes, edges), set())

    def test_surviving_inherits_inbound_is_not_emptied(self) -> None:
        """A DIFFERENT edge type than the calls-edge test above -- proves the
        rule counts inbound edges of any type, not just ``calls``: the same
        stub id can be an inherits base too (``_python_inherits`` shares
        ``make_resolved_target_node``)."""
        nodes = {
            "symbol:py:pkg_b.base:Base": _resolved_stub("pkg_b.base", "Base"),
            "symbol:py:pkg_a.sub:Sub": {
                "type": "symbol", "label": "Sub", "props": {},
            },
        }
        edges = [_inherits("symbol:py:pkg_a.sub:Sub", "symbol:py:pkg_b.base:Base")]
        self.assertEqual(emptied_resolved_stub_node_ids(nodes, edges), set())

    def test_real_symbol_with_zero_inbound_calls_is_never_emptied(self) -> None:
        """THE critical negative-space case: a real, definite, file-anchored
        symbol with zero inbound ``calls`` edges (an exported function
        nobody happens to call, a constructor, dead code) must never be
        purged -- confidence/authority/props.file distinguish it from the
        stub even though nothing currently references it either."""
        nodes = {
            "symbol:py:pkg_b.callee:target_func": _real_symbol(
                "pkg_b/callee.py", "target_func",
            ),
        }
        self.assertEqual(emptied_resolved_stub_node_ids(nodes, []), set())

    def test_unresolved_sentinel_is_never_emptied_by_this_rule(self) -> None:
        """Same props fingerprint, different id prefix -- disjoint from
        oao53's own rule, which owns this shape."""
        nodes = {"symbol:unresolved:Base": _unresolved_sentinel()}
        self.assertEqual(emptied_resolved_stub_node_ids(nodes, []), set())

    def test_non_symbol_type_is_never_emptied(self) -> None:
        """Defensive: no minting strategy has ever emitted a non-``symbol``
        node with this props shape, but the type guard must hold
        structurally, not just by convention."""
        nodes = {
            "symbol:py:pkg_b.callee:target_func": {
                "type": "concept", "label": "target_func",
                "props": _resolved_stub()["props"],
            },
        }
        self.assertEqual(emptied_resolved_stub_node_ids(nodes, []), set())

    def test_wrong_source_strategy_is_never_emptied(self) -> None:
        """Defensive: the fingerprint requires source_strategy ==
        "python_callgraph" (what make_resolved_target_node always stamps),
        not merely "no file + speculative + derived" -- guards against a
        project-local strategy coincidentally sharing the rest of the
        shape for an unrelated reason."""
        node = _resolved_stub()
        node["props"]["source_strategy"] = "some_other_strategy"
        nodes = {"symbol:py:pkg_b.callee:target_func": node}
        self.assertEqual(emptied_resolved_stub_node_ids(nodes, []), set())

    def test_missing_or_non_dict_props_does_not_raise(self) -> None:
        nodes = {
            "symbol:py:pkg_b.callee:target_func": {
                "type": "symbol", "label": "target_func",
            },
        }
        self.assertEqual(emptied_resolved_stub_node_ids(nodes, []), set())

    def test_non_string_node_id_is_skipped_defensively(self) -> None:
        nodes = {"symbol:py:pkg_b.callee:target_func": _resolved_stub()}
        edges = [{"from": "x", "to": 42, "type": "calls", "props": {}}]
        self.assertEqual(
            emptied_resolved_stub_node_ids(nodes, edges),
            {"symbol:py:pkg_b.callee:target_func"},
        )


class EmptiedPlaceholderNodeIdsUnionTest(unittest.TestCase):
    """The four-way union entry point :mod:`weld.discovery_state` calls."""

    def test_unions_all_four_rules(self) -> None:
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
            "symbol:unresolved:Base": _unresolved_sentinel(),
            "symbol:py:pkg_b.callee:target_func": _resolved_stub(),
        }
        self.assertEqual(
            emptied_placeholder_node_ids(nodes, []),
            {
                "package:python:pkg",
                "package:go:strings",
                "symbol:unresolved:Base",
                "symbol:py:pkg_b.callee:target_func",
            },
        )

    def test_a_real_symbol_with_zero_inbound_edges_is_never_returned(self) -> None:
        """Sanity check for the union as a whole: an ordinary, real,
        file-anchored symbol with nothing calling it yet matches NONE of
        the four rules -- the union must not accidentally widen past what
        each rule individually allows."""
        nodes = {
            "symbol:py:pkg_b.callee:target_func": _real_symbol(
                "pkg_b/callee.py", "target_func",
            ),
        }
        self.assertEqual(emptied_placeholder_node_ids(nodes, []), set())


if __name__ == "__main__":
    unittest.main()
