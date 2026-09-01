"""The class-base reading of a deferred attribute call, and what it refuses.

``from weld.bm25 import BM25Corpus`` + ``BM25Corpus.from_nodes()`` is the one
non-submodule shape of ``props.import_attr`` with a real answer:
``symbol:py:weld.bm25:BM25Corpus.from_nodes``, a node ``python_callgraph``
already emitted, since a nested ``def`` carries its dotted qualname. Before the
imported-value fix that call minted ``symbol:py:weld.bm25:from_nodes`` -- a
function that exists under no spelling -- and after it the call fell to the
sentinel, so the real method read "no callers" either way.

Sibling of ``weld_graph_closure_import_attr_test``, which owns the submodule
reading, the hint's own validation, and the shape of the rule table. Split for
the reason that file's docstring gives for its own split: a rule is an
inference plus its bounds, and these bounds are a different argument from the
submodule rule's. That one proves a *module* and then mints the member the
caller claims; this one proves a *class* and refuses to mint anything, because
the walk that proved the class enumerated its members in the same pass.

The refusals are the load-bearing half, and by a measured margin. On this repo
at fix time 78 edges carried the hint: 3 had a definite class base with a
definite method under it, and 32 had a base that was a module-level constant --
a dict, a compiled regex, a message template. A rule keyed on the base alone
would have fabricated a bigger population than the deferral was introduced to
remove, which is why both halves of the proof get a case here refusing alone.
"""

from __future__ import annotations

import unittest

from weld.strategies._python_import_attr import IMPORT_ATTR_PROP
from weld.tests._graph_closure_import_attr_fixture import (
    CALLER,
    CLASS_SENTINEL,
    METHOD,
    class_base_graph,
    close,
    cross_glob_graph,
    deferred_edge,
    one,
    targets,
)


def _called(edges: list[dict]) -> set[str]:
    """Every id a ``calls`` edge names as its target.

    Scoped to ``calls`` on purpose: ``close_graph`` gives the method its
    ``file: -contains->`` edge whether or not the retarget fired, so a refusal
    is "no call landed here", not "nothing mentions it".
    """
    return {str(e["to"]) for e in edges if e["type"] == "calls"}


class ClassBaseReadingTest(unittest.TestCase):
    """``from lib.tables import Corpus`` + ``Corpus.build()`` -> the method."""

    def setUp(self) -> None:
        self.nodes, self.edges = close(*class_base_graph())

    def test_the_call_lands_on_the_method_symbol(self) -> None:
        self.assertEqual(targets(self.edges, CALLER), [METHOD])

    def test_the_edge_reads_as_resolved(self) -> None:
        """A moved endpoint that still claimed ``speculative`` would misreport."""
        props = one(self.edges, CALLER)["props"]
        self.assertTrue(props["resolved"])
        self.assertEqual(props["confidence"], "definite")
        self.assertEqual(props["resolution"], "import")

    def test_the_walked_method_node_is_left_alone(self) -> None:
        """Naming an existing node must not overwrite it with a stub.

        The walked node carries ``kind``/``file``; the speculative payload this
        pass mints for an absent target carries neither. Landing on it has to
        keep the definition's own record, or the retarget would trade a missing
        edge for a downgraded node.
        """
        props = self.nodes[METHOD]["props"]
        self.assertEqual(props["confidence"], "definite")
        self.assertEqual(props["kind"], "method")
        self.assertEqual(props["file"], "lib/tables.py")

    def test_the_orphaned_sentinel_is_dropped(self) -> None:
        self.assertNotIn(CLASS_SENTINEL, self.nodes)

    def test_the_bare_sibling_is_never_minted(self) -> None:
        """``symbol:py:lib.tables:build`` is the id this shape used to give."""
        self.assertNotIn("symbol:py:lib.tables:build", self.nodes)

    def test_the_hint_survives_the_retarget(self) -> None:
        """It is what lets the next round undo and re-derive this move."""
        self.assertIn(IMPORT_ATTR_PROP, one(self.edges, CALLER)["props"])


class ClassBaseDecoratesSideTest(unittest.TestCase):
    """``@Corpus.register`` moves the ``from`` endpoint, not the ``to`` one.

    A ``decorates`` edge runs decorator -> decorated (ADR 0122), so the
    deferred target sits on ``from``. Which endpoint to move is the pass's
    business and the hint records it, but a rule that only ever ran against
    ``calls`` in a test would not show that the two compose.
    """

    def setUp(self) -> None:
        nodes, _edges = class_base_graph()
        edges = [
            deferred_edge(
                src=CLASS_SENTINEL, dst=CALLER, side="from",
                edge_type="decorates", module="lib.tables", base="Corpus",
                attr="build",
            )
        ]
        self.nodes, self.edges = close(nodes, edges)

    def test_the_decorator_endpoint_lands_on_the_method(self) -> None:
        moved = [e for e in self.edges if e["type"] == "decorates"]
        self.assertEqual([e["from"] for e in moved], [METHOD])
        self.assertEqual([e["to"] for e in moved], [CALLER])

    def test_the_orphaned_sentinel_is_dropped(self) -> None:
        self.assertNotIn(CLASS_SENTINEL, self.nodes)


class ClassBaseRefusalTest(unittest.TestCase):
    """Each half of the proof, removed on its own, sends the call back."""

    def _closed(self, **kwargs) -> tuple[dict, list]:
        return close(*class_base_graph(**kwargs))

    def _refused(self, **kwargs) -> tuple[dict, list]:
        """Close the cast and assert the sentinel stood; return both halves."""
        nodes, edges = self._closed(**kwargs)
        self.assertEqual(targets(edges, CALLER), [CLASS_SENTINEL])
        self.assertNotIn(METHOD, _called(edges))
        return nodes, edges

    def test_a_constant_base_keeps_the_sentinel(self) -> None:
        """The 32-edge population: a base node with no walked ``kind``.

        ``from weld._contract_types import PROTOCOL_TRANSPORT_COMPATIBILITY``
        + ``.get()`` on this repo. The stub carries a module and a qualname and
        nothing that says a definition was read.
        """
        self._refused(base_kind=None)

    def test_a_function_base_keeps_the_sentinel(self) -> None:
        """``from lib.tables import helper`` + ``helper.build()`` is a closure."""
        self._refused(base_kind="function")

    def test_a_class_base_with_no_such_method_keeps_the_sentinel(self) -> None:
        """The walk saw the class and every member; an absent one is absent.

        This is the inherited-method case too -- ``Sub.method`` defined on
        ``Base`` emits no ``Sub.method`` symbol -- which the rule declines
        rather than resolve through an MRO it does not compute.
        """
        nodes, _edges = self._refused(method=False)
        self.assertNotIn(METHOD, nodes)

    def test_a_speculative_class_node_is_not_proof(self) -> None:
        """A stub claiming ``kind=class`` was minted by someone, not walked."""
        self._refused(base_confidence="speculative")

    def test_a_speculative_method_node_is_not_proof(self) -> None:
        """Otherwise one pass's mint would justify the next one's retarget."""
        self._refused(method_confidence="speculative")

    def test_a_stdlib_base_keeps_the_sentinel(self) -> None:
        """``from pathlib import Path`` + ``Path.cwd()``: no node for the base."""
        nodes, edges = cross_glob_graph()
        nodes, edges = close(
            nodes, [deferred_edge(module="pathlib", base="Path", attr="cwd")]
        )
        self.assertEqual(targets(edges, CALLER), ["symbol:unresolved:cwd"])
        self.assertNotIn("symbol:py:pathlib:Path.cwd", nodes)

    def test_a_refused_class_base_keeps_its_hint(self) -> None:
        """A later round gets to ask again once the class is walked."""
        _nodes, edges = self._closed(method=False)
        self.assertIn(IMPORT_ATTR_PROP, one(edges, CALLER)["props"])

    def test_a_refused_class_base_keeps_its_sentinel_node(self) -> None:
        """The drop is reference-counted, not unconditional."""
        nodes, _edges = self._closed(method=False)
        self.assertIn(CLASS_SENTINEL, nodes)


if __name__ == "__main__":
    unittest.main()
