"""Gaps G2, G3 and G7: what the corpus's *connections* answer, or do not.

Three questions a Node user asks on day one and gets nothing back from: who
calls this shared function, where does this workspace import come from, and
what routes does this app expose (ADR 0142 D2, D3, D4; bd lrnx1.3, lrnx1.4,
lrnx1.7). Each probe lands **red on purpose** and its marker is flipped by the
fix that owns it rather than by this file (ADR 0142 D7). All three are flipped
now: G3's, because a workspace member name and a ``tsconfig`` alias bind to the
files behind them; G2's, because a call to an imported first-party name binds
to the definition it names and is attributed to the export it was written
inside; and G7's, because the app router is a declared framework strategy and
an exported ``GET`` under ``app/`` is a route. All three are plain tests now,
and any regression in any of them is a plain failure -- which is why this
module no longer imports the marker decorator at all.

Like its sibling, this module runs against the hand-wired configuration
(:data:`weld.tests._node_eval_corpus.WIRED_DISCOVER_YAML`) so each probe is
red for its own reason rather than for gap G1's.

Two pass-today assurance probes ride along, and they are the corpus's answer
to "did a fix cost us coverage": express routes across both dialects,
including the chained form, and every ``npm run`` target the manifests
declare. Both are asserted as equalities against ground truth stated in the
corpus module -- "the routes that were found are correct" is true of a run
that found none, which is the shape that let a whole ecosystem go missing in
the field-eval rounds.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld.tests._graph_invariants import graph_nodes
from weld.tests._node_eval_corpus import (
    EXPRESS_ROUTES,
    FIRST_PARTY_IMPORTER,
    FIRST_PARTY_TARGETS,
    FORMAT_PRICE,
    FORMAT_PRICE_CALLER_FILES,
    FORMAT_PRICE_FILE,
    MANIFEST_SCRIPT_TARGETS,
    NEXT_ROUTE_FILE,
    NEXT_ROUTE_PATH,
)
from weld.tests._node_eval_e2e_harness import (
    NodeEvalWorkspace,
    edges_from,
    file_node_id,
    node_props,
    nodes_of_type,
)
#: The bd issue that owns each fix -- issue-id suffixes, the full ledger ids
#: being tracker-internal.
_BD_FIXES = {
    "G2": "lrnx1.3",
    "G3": "lrnx1.4",
    "G7": "lrnx1.7",
}

_WS: NodeEvalWorkspace | None = None
_TMP: tempfile.TemporaryDirectory | None = None


def setUpModule() -> None:
    global _WS, _TMP
    _TMP = tempfile.TemporaryDirectory()
    _WS = NodeEvalWorkspace.monorepo(Path(_TMP.name))
    _WS.bootstrap_wired()


def tearDownModule() -> None:
    if _TMP is not None:
        _TMP.cleanup()


def workspace() -> NodeEvalWorkspace:
    assert _WS is not None, "setUpModule did not run"
    return _WS


class ResolutionProbes(unittest.TestCase):
    ws: NodeEvalWorkspace
    graph: dict

    @classmethod
    def setUpClass(cls) -> None:
        cls.ws = workspace()
        cls.graph = cls.ws.graph()

    def _definition_of(self, label: str, rel_path: str) -> str:
        """The one symbol node that holds *label*'s definition in *rel_path*.

        Found by *what it is* -- the node that knows the file this function is
        written in -- rather than by a spelled id, so the probe does not
        depend on an id convention the fix might legitimately change.
        """
        hits = sorted(
            node_id
            for node_id, node in nodes_of_type(self.graph, "symbol").items()
            if node.get("label") == label
            and node_props(node).get("file") == rel_path
        )
        self.assertEqual(
            len(hits), 1,
            f"expected one node to hold the definition of {label} in "
            f"{rel_path}, got {hits}",
        )
        return hits[0]

    def test_g2_wd_callers_answers_for_a_typescript_definition(self) -> None:
        """``wd callers`` on a shared TypeScript function names its callers.

        Compared by the *files* the callers live in, not by their ids: ADR
        0142 D2 moves caller attribution from the file sentinel to the
        enclosing function where the grammar yields nesting, so a probe keyed
        on today's sentinel ids would go red on the fix that repairs it. The
        file set is the recall claim either way -- three TypeScript call
        sites, no more and no fewer.

        ``legacy.js`` deliberately does not call this function: JavaScript
        extraction is gap G6, and a caller set that spanned both would leave
        this probe red for two reasons at once.
        """
        definition = self._definition_of(FORMAT_PRICE, FORMAT_PRICE_FILE)
        result = self.ws.wd("callers", definition, "--json").check()
        nodes = graph_nodes(self.graph)

        reported: set[str] = set()
        for caller in result.json().get("callers", []):
            node_id = str(caller.get("id"))
            props = node_props(nodes.get(node_id, {}))
            reported.add(str(props.get("file") or caller.get("file")))

        self.assertEqual(
            reported, set(FORMAT_PRICE_CALLER_FILES),
            f"`wd callers {definition}` does not name the files that call it:"
            f"\n{result.output}",
        )

    def test_g3_first_party_names_bind_to_files(self) -> None:
        """A first-party import spelling resolves to the file that defines it.

        The TypeScript edition of the no-first-party-external invariant (ADR
        0142 D3, ADR 0141 D4's one-spelling principle at the resolution
        layer). It is written here rather than taken from
        ``_graph_invariants.assert_no_first_party_external`` because that
        helper reasons over *dotted Python module paths* and an external
        marker this node does not carry: ``@acme/shared`` is neither dotted
        nor flagged ``external``, it is flagged ``origin: unresolved``. When
        the fix lands, promoting a shared ecosystem-agnostic form of this
        check is the natural follow-up.

        Two halves: no package node claims a name this repo itself declares,
        and each first-party spelling reaches the file behind it.
        """
        offenders = sorted(
            f"{node_id} (name={node_props(node).get('name')!r}, "
            f"origin={node_props(node).get('origin')!r})"
            for node_id, node in nodes_of_type(self.graph, "package").items()
            if str(node_props(node).get("name")) in FIRST_PARTY_TARGETS
            and node_props(node).get("origin") != "project"
        )
        self.assertEqual(
            offenders, [],
            "a package node claims a name this workspace declares itself: "
            f"{offenders}",
        )

        importer = file_node_id(self.graph, FIRST_PARTY_IMPORTER)
        nodes = graph_nodes(self.graph)
        reached = {
            str(node_props(nodes.get(str(edge.get("to")), {})).get("file"))
            for edge in edges_from(self.graph, importer)
        }
        unbound = sorted(
            f"{specifier} -> {target}"
            for specifier, target in FIRST_PARTY_TARGETS.items()
            if not any(
                hit != "None" and hit.startswith(target) for hit in reached
            )
        )
        self.assertEqual(
            unbound, [],
            f"{FIRST_PARTY_IMPORTER} imports first-party names that bind to no "
            f"file: {unbound}; it reaches "
            f"{sorted(r for r in reached if r != 'None')}",
        )

    def test_g7_app_router_handlers_become_route_nodes(self) -> None:
        """An exported HTTP-verb function under ``app/`` is an inbound route.

        Asserted on the node's shape -- type ``route``, ``method``, ``path``,
        and the file it was declared in, the inbound-surface vocabulary of
        ADR 0086 -- rather than on its id. The id convention
        (``route:<VERB>:<path>``, shared by express, gin and axum under
        ADR 0071) is one the Next.js strategy is expected to join, but a probe
        has no business fixing it before the strategy exists. The path is the
        directory chain, which is the whole of the app-router convention this
        gap is about; the file is a property every route strategy in the repo
        already satisfies, so a route attributed to nowhere is not a fix.

        Marker flipped by ``_BD_FIXES["G7"]``: ``weld.strategies.next`` is now
        a declared framework strategy beside express (ADR 0071's mechanism,
        ADR 0142 D4), and the generous config above wires it. The assertion is
        unchanged -- what changed is the graph underneath it.
        """
        exposed = {
            (
                str(node_props(node).get("method")),
                str(node_props(node).get("path")),
                str(node_props(node).get("file")),
            )
            for node in nodes_of_type(self.graph, "route").values()
        }
        self.assertIn(
            ("GET", NEXT_ROUTE_PATH, NEXT_ROUTE_FILE), exposed,
            f"the app-router handler exposes no route node; the graph has "
            f"{sorted(exposed)}",
        )

    # -- pass-today assurance ---------------------------------------------

    def test_express_routes_are_found_across_both_dialects(self) -> None:
        """Wiring express still yields every route the fixture registers.

        Scoped to the routes express itself minted, so gap G7's fix -- which
        adds Next.js route nodes to the same graph -- cannot turn this
        assurance red by succeeding.
        """
        express_routes = {
            (
                str(node_props(node).get("method")),
                str(node_props(node).get("path")),
            )
            for node in nodes_of_type(self.graph, "route").values()
            if node_props(node).get("source_strategy") == "express"
        }
        self.assertEqual(
            express_routes, set(EXPRESS_ROUTES),
            "the express strategy no longer finds the routes this workspace "
            "registers across .ts and .js, chained form included",
        )

    def test_package_json_scripts_reach_the_graph(self) -> None:
        """Every ``npm run`` target the four manifests declare is a node.

        The manifest strategy is grammar-free and reads package.json off
        disk, so this is the one pass-today claim in the corpus that holds
        whatever happens to the tree-sitter layer -- which makes it the
        cheapest early warning that a Node workspace stopped being read at
        all.
        """
        found = {
            (
                str(node.get("type")),
                str(node_props(node).get("file")),
                str(node_props(node).get("script_name")),
            )
            for node_type in ("build-target", "test-target")
            for node in nodes_of_type(self.graph, node_type).values()
        }
        self.assertEqual(
            found, set(MANIFEST_SCRIPT_TARGETS),
            "the npm scripts this workspace declares are not the ones the "
            "graph holds",
        )


if __name__ == "__main__":
    unittest.main()
