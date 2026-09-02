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
  pointed at ``symbol:py:collections:Counter``;
* an inferred name must not land on the importing file itself. ``_link_imports``
  drops a self-edge, so a self-answer does not merely misdirect the edge -- it
  deletes the import, and the third-party package node is never minted at all
  (ADR 0143 D3 guard 4, the closure-side twin of the strategy rule).
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


class SelfNamedThirdPartyImportTest(unittest.TestCase):
    """A file named after the package it imports must not resolve to itself.

    ``weld/providers/anthropic.py`` does ``import anthropic``, and the ancestor
    walk offers ``weld.providers.anthropic`` -- which *is* a file node: the
    importing file. ``_link_imports`` then dropped the edge at its self-edge
    guard, so the dependency left no package node and no edge at all. Verified
    on this checkout before the refusal: not one of ``anthropic``, ``openai``,
    ``ollama`` or ``tree_sitter`` had a ``package:python:`` node.

    Not a wrong edge but a silently missing one, which is the worse of the two
    for the readers that act on it -- ``wd impact``, package inventories, and
    the cross-repo ``package_graph`` join, none of which can see that anything
    is gone.
    """

    SOURCE = "file:weld/providers/anthropic"

    def setUp(self) -> None:
        self.nodes, self.edges = close({
            "file:weld/providers/__init__": file_node(
                "weld/providers/__init__.py"
            ),
            self.SOURCE: file_node(
                "weld/providers/anthropic.py",
                ["anthropic", "anthropic.Anthropic", "weld.providers"],
            ),
        })

    def test_the_third_party_import_keeps_its_package_node(self) -> None:
        edge = depends_on(self.edges, self.SOURCE)["anthropic"]
        self.assertEqual(edge["to"], "package:python:anthropic")
        self.assertEqual(edge["props"]["resolution"], "external")
        self.assertIs(self.nodes[edge["to"]]["props"]["external"], True)

    def test_both_candidate_groups_refuse(self) -> None:
        """One assertion, because each package node needs a different group.

        ``import anthropic`` is lost in the whole-name group, which offers
        ``weld.providers.anthropic`` directly. ``from anthropic import
        Anthropic`` is captured as ``anthropic.Anthropic``, whose whole-name
        readings all miss -- it is the *member-stripped* group that offers the
        same self-answer and loses it there. Refuse in only one group and
        exactly one of these two nodes goes missing. The pair is what a file
        not named ``anthropic.py`` already got.
        """
        self.assertEqual(
            package_ids(self.nodes),
            ["package:python:anthropic", "package:python:anthropic.anthropic"],
        )

    def test_the_first_party_import_beside_it_still_resolves(self) -> None:
        self.assertEqual(
            depends_on(self.edges, self.SOURCE)["weld.providers"]["to"],
            "file:weld/providers/__init__",
        )


class RefusedCandidateDoesNotStopTheWalkTest(unittest.TestCase):
    """Refusing the importer's own module skips it; it does not end the walk.

    ``pkg/sub/thing.py`` importing ``thing`` beside a real ``pkg/thing.py``: the
    deepest reading (``pkg.sub.thing``) is the importer itself and is refused,
    and the next one out is a file the graph holds. Ending the walk at the
    refusal would mint an external node beside that file instead -- N4 itself,
    one directory further up, traded for the defect this guard removes.
    """

    def test_the_next_ancestor_still_answers(self) -> None:
        nodes, edges = close({
            "file:pkg/thing": file_node("pkg/thing.py"),
            "file:pkg/sub/thing": file_node("pkg/sub/thing.py", ["thing"]),
        })
        self.assertEqual(
            depends_on(edges, "file:pkg/sub/thing")["thing"]["to"],
            "file:pkg/thing",
        )
        self.assertEqual(package_ids(nodes), [])


class SelfNamedImporterGetsTheOrdinaryAnswerTest(unittest.TestCase):
    """The refusal buys parity with every other importer, and only that.

    Measured on this checkout, three of the four packages the bug hid --
    ``anthropic``, ``openai``, ``tree_sitter`` -- already have a speculative
    ``symbol:`` stub for a member the source imports by name, so the literal
    lookup answers with the stub before the external minter is reached. That is
    the direct lookup's shipped behaviour (``test_a_literal_name_still_resolves
    _through_the_stub`` above), it is filename-independent, and it is not what
    this guard fixes. What the guard fixes is that the file *named after the
    package* was answering differently from every other file in the repo.

    So the assertion is equality between the two, not a particular target: the
    self-named importer must be indistinguishable from a differently-named one.
    Pinning the target instead would make this suite fail the day the residue
    (bd 5038-d853w, "a module import resolves onto one
    arbitrary member stub, not the module") is decided either way.
    """

    STUB = "symbol:py:anthropic:Anthropic"
    IMPORTS = ("anthropic", "anthropic.Anthropic")

    def _resolve_from(
        self, path: str, node_id: str,
    ) -> tuple[list[str], list[tuple[str, str, str]]]:
        nodes, edges = close({
            self.STUB: {
                "type": "symbol",
                "label": "Anthropic",
                "props": {
                    "module": "anthropic",
                    "qualname": "Anthropic",
                    "language": PY,
                    "confidence": "speculative",
                },
            },
            node_id: file_node(path, list(self.IMPORTS)),
        })
        resolved = [
            (name, edge["to"], edge["props"]["resolution"])
            for name, edge in sorted(depends_on(edges, node_id).items())
        ]
        return package_ids(nodes), resolved

    def test_being_named_after_the_package_changes_nothing(self) -> None:
        self.assertEqual(
            self._resolve_from(
                "weld/providers/anthropic.py", "file:weld/providers/anthropic"
            ),
            self._resolve_from(
                "weld/providers/notnamed.py", "file:weld/providers/notnamed"
            ),
        )

    def test_the_import_is_no_longer_dropped(self) -> None:
        """The one thing this guard does owe on its own: an answer at all."""
        _packages, resolved = self._resolve_from(
            "weld/providers/anthropic.py", "file:weld/providers/anthropic"
        )
        self.assertEqual(
            [name for name, _to, _res in resolved], sorted(self.IMPORTS)
        )


class LiteralSelfImportIsStillDroppedTest(unittest.TestCase):
    """The refusal binds the inferred ranks only, never the direct lookup.

    ``import weld.providers.anthropic`` inside that same file names the module
    the graph already holds, by the spelling the index keys it under. Resolving
    it to the importer and dropping the self-edge is the right answer; minting
    ``package:python:weld.providers.anthropic`` beside the file node instead
    would be N4 itself -- an ``external=True`` twin of a first-party module.
    """

    def test_a_literal_self_import_mints_no_external_package(self) -> None:
        nodes, edges = close({
            "file:weld/providers/anthropic": file_node(
                "weld/providers/anthropic.py", ["weld.providers.anthropic"]
            ),
        })
        self.assertEqual(package_ids(nodes), [])
        self.assertEqual(depends_on(edges, "file:weld/providers/anthropic"), {})


if __name__ == "__main__":
    unittest.main()
