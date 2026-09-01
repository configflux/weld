"""Contract for the golden invariant hook (bd 5038-ipa1e, ADR 0139 mechanism 5).

The four golden families each prove the hook is wired into *their* compare and
regen paths. This file proves the thing they all depend on: that the hook
actually rejects each violation class, that a family cannot end up checking
nothing without saying so, and that the two applicability dimensions R6 named --
the child roster and the external-package prefix -- are load-bearing rather than
decoration.

Every payload here starts as a golden read off disk, which is the producer's own
output, and is injured by ``weld.graph_closure``'s own minters via
``_golden_violation_fixtures`` (ADR 0139 mechanism 1). The one exception is the
synthetic child graphs in :class:`FederatedRosterTest`, whose node ids are read
out of the root graph's own edges rather than typed in.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from weld.graph_closure import _ensure_package_node
from weld.tests._golden_invariants import (
    GoldenScope,
    check_golden_graph,
    child_graphs_from_repo_nodes,
    external_package_prefixes,
    parse_canonical_golden,
)
from weld.tests._golden_violation_fixtures import (
    with_dangling_edge,
    with_emptied_placeholder,
    with_fabricated_external,
)
from weld.tests._graph_invariants import graph_edges, graph_nodes
from weld.workspace import UNIT_SEPARATOR

_TESTS_DIR = Path(__file__).resolve().parent
_SINGLE_REPO_GOLDEN = (
    _TESTS_DIR / "fixtures" / "blast_radius" / "python_pip" / "expected" / "graph.json"
)
_FEDERATED_GOLDEN = _TESTS_DIR / "golden" / "demo_discover" / "05-polyrepo.json"
#: A genuinely non-Python family, for R6's prefix-or-visible-skip criterion.
_TYPESCRIPT_GOLDEN = (
    _TESTS_DIR / "golden" / "demo_discover" / "04-monorepo-typescript.json"
)

_SCOPE = GoldenScope(family="hook_contract")


def _golden(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class ViolationClassTest(unittest.TestCase):
    """Each class named in the acceptance criterion is rejected, and named."""

    def setUp(self) -> None:
        self.clean = _golden(_SINGLE_REPO_GOLDEN)

    def _rejected(self, payload: dict) -> str:
        with self.assertRaises(AssertionError) as caught:
            check_golden_graph(
                payload, scope=_SCOPE, label="injected", child_graphs={},
            )
        return str(caught.exception)

    def test_clean_golden_passes(self) -> None:
        """The half that keeps the other three honest.

        A hook that rejected everything would satisfy every rejection test below
        and fail the whole suite the moment it ran on what actually ships.
        """
        report = check_golden_graph(
            self.clean, scope=_SCOPE, label="python_pip", child_graphs={},
        )
        self.assertIn(f"{len(graph_nodes(self.clean))} nodes", report)
        self.assertIn(f"{len(graph_edges(self.clean))} edges", report)
        self.assertIn("no_orphan_stubs", report)

    def test_dangling_edge_is_rejected(self) -> None:
        message = self._rejected(with_dangling_edge(self.clean))
        self.assertIn("dangling edge endpoint", message)
        self.assertIn("no_such_node_in_this_graph", message)

    def test_fabricated_external_is_rejected(self) -> None:
        message = self._rejected(with_fabricated_external(self.clean))
        self.assertIn("already holds first-party", message)

    def test_emptied_placeholder_is_rejected(self) -> None:
        message = self._rejected(with_emptied_placeholder(self.clean))
        self.assertIn("placeholder shape", message)


class VacuityGuardTest(unittest.TestCase):
    """A family cannot quietly check nothing (ADR 0139 mechanism 5)."""

    def test_payload_with_no_nodes_and_no_edges_is_rejected(self) -> None:
        with self.assertRaises(AssertionError) as caught:
            check_golden_graph(
                {}, scope=_SCOPE, label="empty", child_graphs={},
            )
        self.assertIn("no nodes and no edges", str(caught.exception))

    def test_unparsed_canonical_text_is_rejected(self) -> None:
        text = _SINGLE_REPO_GOLDEN.read_text(encoding="utf-8")
        with self.assertRaises(AssertionError) as caught:
            check_golden_graph(
                text, scope=_SCOPE, label="text", child_graphs={},
            )
        self.assertIn("no nodes and no edges", str(caught.exception))

    def test_parse_canonical_golden_accepts_the_same_text(self) -> None:
        """The remedy the previous case points at actually works."""
        text = _SINGLE_REPO_GOLDEN.read_text(encoding="utf-8")
        payload = parse_canonical_golden(text, label="text")
        self.assertEqual(_golden(_SINGLE_REPO_GOLDEN), payload)

    def test_non_json_text_says_so(self) -> None:
        with self.assertRaises(AssertionError) as caught:
            parse_canonical_golden("not json at all", label="broken")
        self.assertIn("not JSON", str(caught.exception))

    def test_forgotten_child_roster_is_rejected(self) -> None:
        """``None`` is the shape a caller lands on by forgetting.

        Defaulting it to ``{}`` would turn every federated endpoint into a
        dangling one for anyone who omitted the argument -- so the omission has
        to be an error, not a quiet reinterpretation.
        """
        with self.assertRaises(AssertionError) as caught:
            check_golden_graph(
                _golden(_SINGLE_REPO_GOLDEN), scope=_SCOPE, label="x",
                child_graphs=None,
            )
        self.assertIn("explicit child_graphs", str(caught.exception))


class ScopeDeclarationTest(unittest.TestCase):
    """An unexplained skip is refused at construction (R6)."""

    def test_open_edges_without_a_reason_is_refused(self) -> None:
        with self.assertRaises(ValueError) as caught:
            GoldenScope(family="f", edges_close=False)
        self.assertIn("open_edges_reason", str(caught.exception))

    def test_reason_without_a_skip_is_refused(self) -> None:
        """A reason nobody would ever report is a comment pretending to be one."""
        with self.assertRaises(ValueError):
            GoldenScope(family="f", edges_close=True, open_edges_reason="why")

    def test_skip_reason_reaches_the_report(self) -> None:
        scope = GoldenScope(
            family="fragment", edges_close=False,
            open_edges_reason="extract() output is not a closed graph",
        )
        report = check_golden_graph(
            _golden(_SINGLE_REPO_GOLDEN), scope=scope, label="frag",
            child_graphs={},
        )
        self.assertIn("edges_resolve SKIPPED", report)
        self.assertIn("extract() output is not a closed graph", report)


class ExternalPrefixTest(unittest.TestCase):
    """R6's prefix-or-visible-skip, proven on a non-Python family.

    The base is the TypeScript monorepo golden rather than a Python one on
    purpose: R6's concern is a fixture whose language is not the
    ``package:python:`` default, and a criterion about non-Python families is
    only met by actually running on one.
    """

    def test_no_external_package_node_is_reported_not_swallowed(self) -> None:
        """Every golden this repo ships is in this state today.

        ``assert_no_first_party_external`` passing over a payload that mints no
        external package at all is correct and useless in equal measure; the
        report is what stops the two readings being indistinguishable.
        """
        clean = _golden(_TYPESCRIPT_GOLDEN)
        self.assertEqual((), external_package_prefixes(clean))
        report = check_golden_graph(
            clean, scope=_SCOPE, label="04-monorepo-typescript", child_graphs={},
        )
        self.assertIn("no_first_party_external vacuous", report)

    def test_a_non_python_fabricated_external_is_caught(self) -> None:
        """The concrete thing a hard-coded ``package:python:`` prefix would miss.

        Deriving the prefix from the payload is what makes this pass without
        anyone having guessed ``package:typescript:`` in advance -- and a
        language nobody has added yet is covered on the day its first node
        appears.
        """
        payload = _golden(_TYPESCRIPT_GOLDEN)
        shadowed = next(
            node_id[len("file:"):].rsplit("/", 1)[-1]
            for node_id in graph_nodes(payload)
            if node_id.startswith("file:")
        )
        _ensure_package_node(payload["nodes"], shadowed, "typescript")

        self.assertEqual(
            ("package:typescript:",), external_package_prefixes(payload),
        )
        with self.assertRaises(AssertionError) as caught:
            check_golden_graph(
                payload, scope=_SCOPE, label="ts", child_graphs={},
            )
        message = str(caught.exception)
        self.assertIn("package:typescript:", message)
        self.assertIn(f"shadows first-party module {shadowed!r}", message)


class FederatedRosterTest(unittest.TestCase):
    """The child roster decides whether a federated golden can be checked."""

    def setUp(self) -> None:
        self.root = _golden(_FEDERATED_GOLDEN)

    def _endpoints(self) -> list[tuple[str, str]]:
        """``(child, local id)`` for every federated endpoint the root names."""
        pairs = []
        for edge in graph_edges(self.root):
            for side in ("from", "to"):
                text = str(edge.get(side))
                if UNIT_SEPARATOR in text:
                    child, _, local = text.partition(UNIT_SEPARATOR)
                    pairs.append((child, local))
        return pairs

    def _seed_children(self, scratch: Path) -> None:
        """Write each child's graph, holding exactly the ids the root reaches.

        The ids come out of the root's own edges, so this cannot drift from what
        federation actually spells, and the child locations come out of the
        root's ``repo:`` nodes rather than from a restated roster.
        """
        wanted: dict[str, dict[str, dict]] = {}
        for child, local in self._endpoints():
            wanted.setdefault(child, {})[local] = {}
        for node_id, node in graph_nodes(self.root).items():
            if not node_id.startswith("repo:"):
                continue
            name = node_id[len("repo:"):]
            rel = (node.get("props") or {}).get("path")
            graph_path = scratch / rel / ".weld" / "graph.json"
            graph_path.parent.mkdir(parents=True, exist_ok=True)
            nodes = wanted.get(name, {})
            graph_path.write_text(json.dumps({"nodes": nodes}), encoding="utf-8")

    def test_the_polyrepo_golden_needs_children_to_resolve(self) -> None:
        """Non-vacuity: ``child_graphs={}`` is *wrong* here, not merely thinner.

        Without this, someone could simplify ``_discover_polyrepo`` back to
        returning a bare graph and the federated golden would go on passing --
        having silently stopped checking its only cross-repo edge.
        """
        with self.assertRaises(AssertionError) as caught:
            check_golden_graph(
                self.root, scope=_SCOPE, label="05-polyrepo", child_graphs={},
            )
        self.assertIn("is not registered", str(caught.exception))

    def test_children_read_from_the_scratch_tree_resolve_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp)
            self._seed_children(scratch)
            children = child_graphs_from_repo_nodes(self.root, scratch)
            report = check_golden_graph(
                self.root, scope=_SCOPE, label="05-polyrepo",
                child_graphs=children,
            )
        self.assertEqual(
            sorted(children),
            sorted(
                node_id[len("repo:"):]
                for node_id in graph_nodes(self.root)
                if node_id.startswith("repo:")
            ),
        )
        self.assertIn("edges_resolve over [", report)

    def test_a_child_path_leaving_the_scratch_tree_is_refused(self) -> None:
        """``props.path`` is repo text nobody vetted (ADR 0115).

        ``Path("/scratch") / "/elsewhere"`` is ``/elsewhere`` in pathlib, so an
        absolute or ``..``-bearing child path would have this roster answer from
        a file outside the tree it was handed. The positive control at the end is
        what makes the two ``None``s mean "refused" rather than "not found".
        """
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            scratch = base / "scratch"
            scratch.mkdir()
            outside = base / "outside" / ".weld"
            outside.mkdir(parents=True)
            (outside / "graph.json").write_text(
                json.dumps(_golden(_SINGLE_REPO_GOLDEN)), encoding="utf-8",
            )

            for spelling in (str(base / "outside"), "../outside"):
                payload = json.loads(json.dumps(self.root))
                for node in payload["nodes"].values():
                    node["props"]["path"] = spelling
                children = child_graphs_from_repo_nodes(payload, scratch)
                self.assertEqual(
                    {None}, set(children.values()),
                    f"child path {spelling!r} was read from outside the tree",
                )

            # Same file, named from inside: proves it was readable all along.
            payload = json.loads(json.dumps(self.root))
            for node in payload["nodes"].values():
                node["props"]["path"] = "outside"
            reachable = child_graphs_from_repo_nodes(payload, base)
            self.assertNotIn(None, reachable.values())

    def test_an_unreadable_child_is_unverifiable_not_absent(self) -> None:
        """A missing child graph maps to ``None`` and fails as unverifiable.

        The distinction matters when a scratch tree is built wrong: "could not
        be read" points at the harness, "is not registered" points at the graph.
        """
        with tempfile.TemporaryDirectory() as tmp:
            children = child_graphs_from_repo_nodes(self.root, Path(tmp))
        self.assertTrue(children)
        self.assertEqual({None}, set(children.values()))
        with self.assertRaises(AssertionError) as caught:
            check_golden_graph(
                self.root, scope=_SCOPE, label="05-polyrepo",
                child_graphs=children,
            )
        self.assertIn("could not be read", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
