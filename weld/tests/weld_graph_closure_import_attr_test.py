"""The submodule reading of a deferred attribute call, and what it refuses.

``from PARENT import CHILD`` + ``CHILD.attr()`` is several readings of one
syntax, and ``python_callgraph`` can only decide it for a module its own glob
owns. The rest arrives here as ``props.import_attr`` and is decided against the
merged graph -- see :mod:`weld._graph_closure_import_attr`.

The refusals are the load-bearing half. This rule assembles a node id out of
strings taken from a stored prop, so every path that does *not* end in a
retarget has to end on the sentinel the strategy already emitted: a base that
names a value rather than a module, a stdlib value import, and any hint whose
stored fields cannot be vouched for. A miss is a visible "weld does not know";
a fabricated ``symbol:py:<anything>:<attr>`` is a confident wrong answer that a
reader acts on, which is the whole class of bug this shape has produced before.

This file also owns ``RuleTableTest``, the seam every reading plugs into, since
that pins the table as a whole rather than any one rule. The other two halves
have their own files: the class-base reading in
``weld_graph_closure_class_base_test``, and the undo -- restoring an endpoint
moved in an earlier round before re-deriving -- in
``weld_graph_closure_import_attr_undo_test``.
"""

from __future__ import annotations

import unittest

from weld._graph_closure_import_attr import (
    IMPORT_ATTR_RULES,
    ImportAttrTarget,
    resolve_class_base,
    resolve_submodule,
)
from weld.strategies._python_import_attr import (
    IMPORT_ATTR_PROP,
    ImportAttrHint,
    read_import_attr_hint,
)
from weld.tests._graph_closure_import_attr_fixture import (
    CALLER,
    CLASS,
    METHOD,
    RESOLVED,
    SENTINEL,
    class_base_graph,
    close,
    cross_glob_graph,
    deferred_edge,
    one,
    symbol_node,
    targets,
)


class SubmoduleReadingTest(unittest.TestCase):
    """The module another glob owns is reachable once the graph is merged."""

    def setUp(self) -> None:
        self.nodes, self.edges = close(*cross_glob_graph())

    def test_the_call_lands_on_the_real_submodule_symbol(self) -> None:
        self.assertEqual(targets(self.edges, CALLER), [RESOLVED])

    def test_the_edge_reads_as_resolved(self) -> None:
        """A moved endpoint that still claimed ``speculative`` would misreport."""
        props = one(self.edges, CALLER)["props"]
        self.assertTrue(props["resolved"])
        self.assertEqual(props["confidence"], "definite")
        self.assertEqual(props["resolution"], "import")

    def test_the_hint_survives_the_retarget(self) -> None:
        """It is what lets the next round undo and re-derive this move."""
        self.assertIn(IMPORT_ATTR_PROP, one(self.edges, CALLER)["props"])

    def test_the_orphaned_sentinel_is_dropped(self) -> None:
        self.assertNotIn(SENTINEL, self.nodes)

    def test_the_bare_parent_is_never_minted(self) -> None:
        """``symbol:py:lib:work`` is the fabricated id this shape used to give."""
        self.assertNotIn("symbol:py:lib:work", self.nodes)


class MissingMemberTest(unittest.TestCase):
    """A proven module whose member was never walked still gets a target.

    Mirrors what the strategy does for any cross-module call target it did not
    walk: the module is proven, the member is the caller's own claim, so the
    node is minted speculatively rather than withheld.
    """

    def setUp(self) -> None:
        nodes, edges = cross_glob_graph()
        del nodes[RESOLVED]
        self.nodes, self.edges = close(nodes, edges)

    def test_the_target_is_minted_speculatively(self) -> None:
        self.assertEqual(targets(self.edges, CALLER), [RESOLVED])
        self.assertEqual(self.nodes[RESOLVED]["props"]["confidence"], "speculative")

    def test_the_minted_target_is_first_party(self) -> None:
        """The module was proven by a ``file:`` node, so ``origin`` follows."""
        self.assertEqual(self.nodes[RESOLVED]["props"]["origin"], "project")


class RefusalTest(unittest.TestCase):
    """Everything the rule will not do, and what it leaves behind instead."""

    def _closed(self, **edge_kwargs) -> tuple[dict, list]:
        nodes, _edges = cross_glob_graph()
        return close(nodes, [deferred_edge(**edge_kwargs)])

    def test_a_value_base_keeps_the_sentinel(self) -> None:
        """``from lib import TABLE`` + ``TABLE.get()``: ``lib.TABLE`` is no module."""
        nodes, edges = self._closed(base="TABLE", attr="get")
        self.assertEqual(targets(edges, CALLER), ["symbol:unresolved:get"])
        self.assertNotIn("symbol:py:lib.TABLE:get", nodes)

    def test_a_stdlib_value_import_keeps_the_sentinel(self) -> None:
        """``from pathlib import Path`` + ``Path.cwd()`` -- no first-party file node."""
        nodes, edges = self._closed(module="pathlib", base="Path", attr="cwd")
        self.assertEqual(targets(edges, CALLER), ["symbol:unresolved:cwd"])
        self.assertNotIn("symbol:py:pathlib.Path:cwd", nodes)

    def test_a_module_with_no_file_node_is_not_proven(self) -> None:
        """A speculative symbol under a module name is not evidence of a module.

        The index this rule reads is Python ``file:`` nodes only, for the same
        reason the N4 import inference accepts nothing else: a stub keyed by
        its declared module would let one fabricated id justify the next.
        """
        nodes, edges = cross_glob_graph(definer=False)
        nodes["symbol:py:lib.inner:other"] = symbol_node(
            "lib.inner", "other", "lib/inner.py"
        )
        del nodes["symbol:py:lib.inner:other"]["props"]["file"]
        nodes, edges = close(nodes, edges)
        self.assertEqual(targets(edges, CALLER), [SENTINEL])

    def test_a_refused_edge_keeps_its_sentinel_node(self) -> None:
        """The drop is reference-counted, not unconditional."""
        nodes, _edges = self._closed(module="pathlib", base="Path", attr="cwd")
        self.assertIn("symbol:unresolved:cwd", nodes)

    def test_a_refused_edge_keeps_its_hint(self) -> None:
        """A second reading gets its turn on the next round, not one round only."""
        _nodes, edges = self._closed(base="TABLE", attr="get")
        self.assertIn(IMPORT_ATTR_PROP, one(edges, CALLER)["props"])


class MalformedHintTest(unittest.TestCase):
    """A hint read back off disk decides a node id, so it is vouched for first."""

    def _hint(self, payload) -> ImportAttrHint | None:
        return read_import_attr_hint({IMPORT_ATTR_PROP: payload})

    def test_a_missing_field_is_refused(self) -> None:
        self.assertIsNone(self._hint({"module": "lib", "base": "inner"}))

    def test_an_empty_field_is_refused(self) -> None:
        self.assertIsNone(
            self._hint({"module": "", "base": "inner", "attr": "w", "side": "to"})
        )

    def test_a_non_string_field_is_refused(self) -> None:
        self.assertIsNone(
            self._hint({"module": 1, "base": "inner", "attr": "w", "side": "to"})
        )

    def test_a_dotted_base_is_refused(self) -> None:
        """The import table binds one identifier; a dotted one is not from it."""
        self.assertIsNone(
            self._hint(
                {"module": "lib", "base": "a.b", "attr": "w", "side": "to"}
            )
        )

    def test_a_non_identifier_attr_is_refused(self) -> None:
        """``attr`` is concatenated into a node id, so it may not carry an id's
        own punctuation -- ``work:extra`` would mint a shape the strategy could
        not have minted.
        """
        self.assertIsNone(
            self._hint(
                {"module": "lib", "base": "inner", "attr": "w:x", "side": "to"}
            )
        )

    def test_a_non_identifier_module_segment_is_refused(self) -> None:
        self.assertIsNone(
            self._hint(
                {"module": "lib.a-b", "base": "inner", "attr": "w", "side": "to"}
            )
        )

    def test_a_well_formed_hint_is_accepted(self) -> None:
        """The refusals above must not be refusing everything."""
        self.assertEqual(
            self._hint(
                {"module": "lib.pkg", "base": "inner", "attr": "work", "side": "to"}
            ),
            ImportAttrHint("lib.pkg", "inner", "work", "to"),
        )

    def test_an_unknown_side_is_refused(self) -> None:
        """``side`` decides which end of an edge gets rewritten."""
        self.assertIsNone(
            self._hint(
                {"module": "lib", "base": "inner", "attr": "w", "side": "up"}
            )
        )

    def test_a_non_dict_payload_is_refused(self) -> None:
        self.assertIsNone(self._hint("lib.inner.work"))

    def test_a_malformed_hint_leaves_the_edge_alone(self) -> None:
        """End to end: refusal degrades to "no rule", never to a rewrite."""
        nodes, edges = cross_glob_graph()
        edges[0]["props"][IMPORT_ATTR_PROP] = {"module": "lib", "base": "inner"}
        nodes, edges = close(nodes, edges)
        self.assertEqual(targets(edges, CALLER), [SENTINEL])


class RuleTableTest(unittest.TestCase):
    """The seam both readings plug into, pinned as a contract.

    The seam is ``weld._graph_closure_import_attr.IMPORT_ATTR_RULES``.
    Each rule answers the same hint a different question against the same
    merged node set, and neither reshaped the pass to arrive: same hint in,
    same target triple out, tried in declared order.
    """

    def test_the_table_is_ordered_name_rule_pairs(self) -> None:
        self.assertIsInstance(IMPORT_ATTR_RULES, tuple)
        for name, rule in IMPORT_ATTR_RULES:
            self.assertIsInstance(name, str)
            self.assertTrue(callable(rule))

    def test_both_readings_are_registered(self) -> None:
        self.assertEqual(
            IMPORT_ATTR_RULES,
            (("submodule", resolve_submodule), ("class_base", resolve_class_base)),
        )

    def test_a_rule_answers_a_target_triple(self) -> None:
        hint = ImportAttrHint("lib", "inner", "work", "to")
        target = resolve_submodule(hint, {}, {"lib.inner": "file:lib/inner"})
        self.assertEqual(
            target, ImportAttrTarget(RESOLVED, "import", "project")
        )

    def test_a_rule_declines_by_answering_none(self) -> None:
        hint = ImportAttrHint("lib", "inner", "work", "to")
        self.assertIsNone(resolve_submodule(hint, {}, {}))

    def test_the_class_base_rule_reads_nodes_not_the_module_index(self) -> None:
        """Its whole question is about the node set, so it is answered there.

        Passing an empty ``module_index`` proves the rule does not quietly
        depend on the submodule reading's evidence -- these two never overlap,
        because ``PARENT.CHILD`` is either a module or a symbol inside
        ``PARENT``, never both.
        """
        nodes, _edges = class_base_graph()
        hint = ImportAttrHint("lib.tables", "Corpus", "build", "to")
        self.assertEqual(
            resolve_class_base(hint, nodes, {}),
            ImportAttrTarget(METHOD, "import", "project"),
        )
        self.assertIsNone(resolve_submodule(hint, nodes, {}))

    def test_the_class_base_rule_declines_on_an_empty_graph(self) -> None:
        hint = ImportAttrHint("lib.tables", "Corpus", "build", "to")
        self.assertIsNone(resolve_class_base(hint, {}, {}))

    def test_the_class_base_rule_ignores_a_non_symbol_node(self) -> None:
        """A ``file:``-typed node parked at a symbol id is not a class."""
        nodes, _edges = class_base_graph()
        nodes[CLASS] = {"type": "file", "props": {"kind": "class"}}
        hint = ImportAttrHint("lib.tables", "Corpus", "build", "to")
        self.assertIsNone(resolve_class_base(hint, nodes, {}))

    def test_the_class_base_rule_survives_a_malformed_node(self) -> None:
        """Node props come off the same untrusted file the hint does.

        ``MalformedHintTest`` vouches for the hint; nothing vouches for the
        node the rule then looks up, so a graph whose ``props`` is not a dict
        (or whose node is not one) has to read as "no proof" rather than raise
        out of the middle of ``close_graph``.
        """
        hint = ImportAttrHint("lib.tables", "Corpus", "build", "to")
        for broken in ({"type": "symbol", "props": None}, "not-a-node", None):
            with self.subTest(node=broken):
                nodes, _edges = class_base_graph()
                nodes[CLASS] = broken
                self.assertIsNone(resolve_class_base(hint, nodes, {}))


if __name__ == "__main__":
    unittest.main()
