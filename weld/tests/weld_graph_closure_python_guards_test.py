"""The bounds on the closure's first-party Python import resolution.

Companion to ``weld_graph_closure_python_modules_test``, which pins what the
N4 fix resolves. This file pins what it must refuse to resolve -- the part
that decides whether the fix is worth having, because each guard here stands
between the fix and a defect at least as bad as the one it removes:

* the ``acme.platform.*`` package nodes are the cross-repo recall win the N4
  regression rode in on (8/12 -> 12/12 on the evaluator's workspace), so
  resolving them locally would trade one defect for a larger one;
* this repo really does contain ``weld/warnings.py`` and ``weld/trace.py``,
  and under Python 3 ``import warnings`` from ``weld/graph.py`` is the
  standard library, not that sibling;
* the module index also holds speculative ``symbol:`` stubs, and an inferred
  name that lands on one has invented a relationship rather than found one.
  Measured on this repo before the rule went in, ``import collections.abc``
  pointed at ``symbol:py:collections:Counter``.
"""

from __future__ import annotations

import unittest

from weld.tests._graph_closure_python_fixture import (
    PY,
    close,
    depends_on,
    file_node,
    package_ids,
)


class ExternalImportsSurviveTest(unittest.TestCase):
    """Names that genuinely live outside the repo must still be minted."""

    def setUp(self) -> None:
        self.nodes, self.edges = close({
            "file:src/broker": file_node("src/broker.py"),
            "file:src/main": file_node(
                "src/main.py",
                [
                    "acme.platform.order.schema.v1.event_pb2",
                    "acme.platform.order.schema.v1.event_pb2.OrderPlacedEvent",
                    "acme.platform.billing.schema.v1.event_pb2",
                    "asyncio",
                    "broker",
                ],
            ),
        })

    def test_both_external_families_still_mint_package_nodes(self) -> None:
        """Both the module and the member spelling keep their own node.

        A sibling repo's manifest joins against these ids, so the member
        spelling disappearing would be a silent cross-repo recall loss.
        """
        self.assertEqual(
            package_ids(self.nodes),
            [
                "package:python:acme.platform.billing.schema.v1.event_pb2",
                "package:python:acme.platform.order.schema.v1.event_pb2",
                "package:python:acme.platform.order.schema.v1.event_pb2"
                ".orderplacedevent",
                "package:python:asyncio",
            ],
        )

    def test_external_nodes_keep_their_external_marking(self) -> None:
        node = self.nodes[
            "package:python:acme.platform.order.schema.v1.event_pb2"
        ]
        self.assertIs(node["props"]["external"], True)
        self.assertEqual(node["props"]["origin"], "external")

    def test_stdlib_import_is_still_tagged_stdlib(self) -> None:
        self.assertEqual(
            self.nodes["package:python:asyncio"]["props"]["origin"], "stdlib"
        )

    def test_the_first_party_sibling_still_resolves(self) -> None:
        self.assertEqual(
            depends_on(self.edges, "file:src/main")["broker"]["to"],
            "file:src/broker",
        )


class StdlibIsNotCapturedBySiblingTest(unittest.TestCase):
    """A sibling named after a stdlib module must not capture the import."""

    def setUp(self) -> None:
        self.nodes, self.edges = close({
            "file:weld/warnings": file_node("weld/warnings.py"),
            "file:weld/trace": file_node("weld/trace.py"),
            "file:weld/graph": file_node(
                "weld/graph.py", ["warnings", "trace.Tracer", "weld.warnings"]
            ),
        })

    def test_bare_stdlib_name_stays_external(self) -> None:
        edge = depends_on(self.edges, "file:weld/graph")["warnings"]
        self.assertEqual(edge["to"], "package:python:warnings")
        self.assertEqual(
            self.nodes["package:python:warnings"]["props"]["origin"], "stdlib"
        )

    def test_stdlib_rooted_member_form_also_stays_external(self) -> None:
        self.assertEqual(
            depends_on(self.edges, "file:weld/graph")["trace.Tracer"]["to"],
            "package:python:trace.tracer",
        )

    def test_the_explicitly_qualified_sibling_still_resolves(self) -> None:
        """``weld.warnings`` names the sibling unambiguously -- it resolves."""
        self.assertEqual(
            depends_on(self.edges, "file:weld/graph")["weld.warnings"]["to"],
            "file:weld/warnings",
        )


class StdlibSubmoduleIsNotRereadTest(unittest.TestCase):
    """``import collections.abc`` must not be re-read as ``collections``.

    Dropping the last segment is how a referenced-symbol capture recovers its
    module, but on a stdlib name that segment is as likely to be a real
    submodule. Stdlib-rooted names are left to the literal lookup entirely.
    """

    def test_a_stdlib_submodule_import_stays_external(self) -> None:
        nodes, edges = close({
            "file:src/collections/__init__": file_node(
                "src/collections/__init__.py"
            ),
            "file:src/app": file_node("src/app.py", ["collections.abc"]),
        })
        edge = depends_on(edges, "file:src/app")["collections.abc"]
        self.assertEqual(edge["to"], "package:python:collections.abc")
        self.assertEqual(nodes[edge["to"]]["props"]["origin"], "stdlib")


class InferenceOnlyAcceptsFileNodesTest(unittest.TestCase):
    """A source-root guess may land on a file, never on a symbol stub."""

    def _close_with_stub(self, imports: list[str]) -> tuple[dict, list[dict]]:
        return close({
            "symbol:py:vendor.thing:Helper": {
                "type": "symbol",
                "label": "Helper",
                "props": {
                    "module": "vendor.thing",
                    "qualname": "Helper",
                    "language": PY,
                    "confidence": "speculative",
                },
            },
            "file:src/app": file_node("src/app.py", imports),
        })

    def test_a_guess_does_not_land_on_a_same_module_symbol_stub(self) -> None:
        """``vendor.thing.Other`` must not be answered by ``vendor.thing``'s stub."""
        _nodes, edges = self._close_with_stub(["vendor.thing.Other"])
        edge = depends_on(edges, "file:src/app")["vendor.thing.Other"]
        self.assertEqual(edge["to"], "package:python:vendor.thing.other")
        self.assertEqual(edge["props"]["resolution"], "external")

    def test_a_literal_name_still_resolves_through_the_stub(self) -> None:
        """Only *inference* is restricted; the direct lookup is unchanged."""
        _nodes, edges = self._close_with_stub(["vendor.thing"])
        edge = depends_on(edges, "file:src/app")["vendor.thing"]
        self.assertEqual(edge["to"], "symbol:py:vendor.thing:Helper")
        self.assertEqual(edge["props"]["resolution"], "local_module")


if __name__ == "__main__":
    unittest.main()
