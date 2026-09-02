"""Four strategies must resolve a glob the way ADR 0112 says (bd b9xgd).

bd uhxjc made ``weld.glob_match.walk_glob`` correct for a wildcard in a
*directory* segment. Four strategies never moved onto the shared resolver at
all, so they kept their own copy of the guard ADR 0112 records as deleted --
``(root / pattern).parent`` plus ``is_dir()`` -- and the walker fix cannot
reach code that does not call the walker. The two shapes fail differently,
which is why this probe asserts both directions:

* ``fastapi`` and ``pydantic`` early-return when the parent is not a
  directory. For ``api/*/routers/*.py`` that parent is the literal path
  ``<root>/api/*``, and for a ``**`` pattern ``<root>/svc/**``; neither is ever
  a directory, so the strategy emits **nothing**. Silence, not a subset --
  bd t06t again, where bd t06t's own fix did not reach.
* ``compose`` and ``events_config`` fall back to ``parent = root`` and glob the
  root directory, so they emit the **wrong** set rather than none, which is
  worse: a partial result looks like an answer. The fixture ships a root-level
  ``docker-compose.decoy.yml`` that no configured glob names and asserts it is
  absent -- the assertion the silent pair cannot make.

System-level on purpose, through a real ``python -m weld discover`` in a
subprocess over a real git repo, because the unit layers each look fine alone:
``walk_glob`` resolves these patterns correctly today and
``resolve_source_files`` records the right in-scope inventory, so only the
graph a whole run writes shows the strategies disagreeing with both. That also
makes this the probe for the ADR 0101 direction bd uhxjc got bitten by, in
reverse: *strategies* narrower than the inventory means nodes missing for files
the graph considers covered, and the staleness cases pin that adding the nodes
does not flip the repo to permanently stale in the process.

``boundary_entrypoint`` rides along on ``api/*/main.py``: already on the shared
resolver, so it doubles as the control that the segment shape resolves end to
end, and its ``boundary:`` nodes are what let the FastAPI case assert that the
boundary lookup -- the one genuinely parent-derived *label* in these four -- is
per router directory rather than computed once for the whole glob.

Siblings: ``weld_strategy_recursive_glob_test`` (the ``**`` battery these four
join), ``strategy_glob_resolve_pin_test`` (the structural pin against a fifth
copy), ``weld_discover_segment_glob_e2e_test`` (the same shape one layer down,
on the walker rather than its callers).
"""

from __future__ import annotations

import json
import unittest

from weld._stale_reasons import CONTENT_DIFFERS, NEVER_INGESTED
from weld.tests._cli_e2e_harness import CliRepoHarness
from weld.tests._segment_glob_fixture import (
    BURIED,
    COMPOSE,
    CONFIG,
    CONTRACTS,
    DECOY,
    DEEP_CONTRACT,
    DEEP_ROUTER,
    PYTHON_INPUTS,
    ROUTERS,
    TREE,
    router,
)


class SegmentGlobStrategyTest(CliRepoHarness, unittest.TestCase):
    """One repo, one ``wd discover``, then what each strategy resolved."""

    graph: dict
    state: dict

    @classmethod
    def setUpClass(cls) -> None:
        cls.setup_cli_repo(TREE, CONFIG)
        cls.wd("discover", "--output", ".weld/graph.json")
        cls.graph = cls.read_json(".weld/graph.json")
        cls.state = cls.read_json(".weld/discovery-state.json")

    # -- helpers ---------------------------------------------------------

    def _nodes(self) -> dict:
        return self.graph.get("nodes", {})

    def _files_for(self, strategy: str) -> set[str]:
        """Repo-relative paths *strategy* named as the source of some node.

        Asserted through ``props.file`` rather than by node id so the claim
        stays "this strategy read this file" -- the id spelling is the
        strategy's own business and may change without this contract changing.
        """
        return {
            (node.get("props") or {}).get("file")
            for node in self._nodes().values()
            if (node.get("props") or {}).get("source_strategy") == strategy
        } - {None}

    def _emitted_files(self) -> set[str]:
        """Every repo-relative path *some* node names, whichever strategy."""
        return {
            (node.get("props") or {}).get("file")
            for node in self._nodes().values()
        } - {None}

    def _edges_of_type(self, edge_type: str) -> set[tuple[str, str]]:
        return {
            (edge.get("from"), edge.get("to"))
            for edge in self.graph.get("edges", [])
            if edge.get("type") == edge_type
        }

    def _stale(self) -> dict:
        return json.loads(self.wd("stale", "--json").stdout)

    # -- fastapi / pydantic: the silent pair -----------------------------

    def test_fastapi_emits_a_route_for_every_matched_router(self) -> None:
        """The headline for the pair whose failure mode is silence."""
        covered = self._files_for("fastapi")
        expected = [*ROUTERS, DEEP_ROUTER]
        missing = [rel for rel in expected if rel not in covered]
        self.assertEqual(
            missing, [],
            f"fastapi emitted nothing for {missing}: a wildcard in a directory "
            f"segment resolved to nothing. Covered: {sorted(covered)}",
        )

    def test_fastapi_route_ids_name_the_declared_paths(self) -> None:
        """Not just *a* node per file -- the routes the fixture declares."""
        ids = set(self._nodes())
        for path in ("/orders", "/users", "/edge"):
            with self.subTest(route=path):
                self.assertIn(f"route:GET:{path}", ids)

    def test_fastapi_resolves_its_boundary_per_router_directory(self) -> None:
        """The one genuinely parent-derived *label*, and it must stay per file.

        ``_detect_boundary_file`` reads the parent of the router directory,
        which the old code computed once for the whole glob. Under a segment
        wildcard there is no single such directory, so each route must be
        exposed by *its own* app module, never by a sibling's.
        """
        exposes = self._edges_of_type("exposes")
        self.assertIn(("boundary:api/orders/main", "route:GET:/orders"), exposes)
        self.assertIn(("boundary:api/users/main", "route:GET:/users"), exposes)
        self.assertNotIn(
            ("boundary:api/orders/main", "route:GET:/users"), exposes,
            "a route was attributed to a sibling directory's boundary",
        )

    def test_pydantic_emits_a_contract_for_every_matched_file(self) -> None:
        covered = self._files_for("pydantic")
        expected = [*CONTRACTS, DEEP_CONTRACT]
        missing = [rel for rel in expected if rel not in covered]
        self.assertEqual(
            missing, [],
            f"pydantic emitted nothing for {missing}. Covered: "
            f"{sorted(covered)}",
        )
        for name in ("OrderContract", "UserContract", "EdgeContract"):
            with self.subTest(contract=name):
                self.assertIn(f"contract:{name}", set(self._nodes()))

    # -- compose / events_config: the wrong-set pair ---------------------

    def test_compose_emits_exactly_the_matched_files(self) -> None:
        """Both directions at once, which is what "wrong set" means.

        Equality rather than two membership checks: the defect adds a file no
        glob names *and* drops every file the wildcard segment names, and a
        subset assertion would let either half survive alone.
        """
        self.assertEqual(sorted(self._files_for("compose")), sorted(COMPOSE))

    def test_events_declares_channels_only_from_matched_files(self) -> None:
        """The same claim for the strategy that keys nodes on content.

        ``channel`` nodes carry no ``props.file``, so the topic literal is the
        per-file marker: one unique topic per compose file in the fixture.
        """
        ids = set(self._nodes())
        named = ("alpha-topic", "bravo-topic", "delta-topic", "flatly-topic")
        for topic in named:
            with self.subTest(topic=topic):
                self.assertIn(f"channel:kafka:{topic}", ids)
        for topic in ("decoy-topic", "buried-topic"):
            with self.subTest(topic=topic):
                self.assertNotIn(f"channel:kafka:{topic}", ids)

    # -- controls --------------------------------------------------------

    def test_a_file_no_glob_names_gets_no_node(self) -> None:
        """The root decoy, stated on its own so the diagnosis is unambiguous."""
        emitted = self._emitted_files()
        self.assertNotIn(DECOY, emitted)
        self.assertNotIn(BURIED, emitted)

    def test_a_literal_directory_glob_still_resolves_and_does_not_recurse(
        self,
    ) -> None:
        """Removing the guard must cost the single-directory path nothing.

        ``flat/docker-compose.*.yml`` has no wildcard in its directory part,
        so it keeps taking the flat branch. If the fix had widened that branch
        the buried sibling one directory down would appear.
        """
        covered = self._files_for("compose")
        self.assertIn(COMPOSE[3], covered)
        self.assertNotIn(BURIED, covered)

    # -- provenance and freshness (ADR 0017, ADR 0101) -------------------

    def test_provenance_names_every_matched_file(self) -> None:
        """A strategy that read a file owes ``discovered_from`` for it.

        Separate from the node assertions: this is the list the incremental
        basis and ``source_stale`` are computed from, and a strategy that read
        a file and emitted nothing for it would still owe the entry.
        """
        discovered_from = set(
            self.graph.get("meta", {}).get("discovered_from", [])
        )
        for rel in (*PYTHON_INPUTS, *COMPOSE):
            with self.subTest(file=rel):
                self.assertIn(rel, discovered_from)

    def test_the_strategies_cover_what_the_inventory_records(self) -> None:
        """The drift this issue is about, stated directly.

        ``resolve_source_files`` resolves the *same* entry to decide what is
        in scope, and it was already correct here. A strategy resolving it
        more narrowly is the drift the walker fix could not reach: nodes
        missing for files the graph considers covered, both freshness signals
        reading clean because the inventory is right.
        """
        inventory = set(self.state.get("files", {}))
        emitted = self._emitted_files()
        uncovered = sorted(
            rel for rel in (*PYTHON_INPUTS, *COMPOSE)
            if rel in inventory and rel not in emitted
        )
        self.assertEqual(
            uncovered, [],
            f"the inventory records {uncovered} as in scope and no strategy "
            f"emitted a node for them",
        )

    def test_a_freshly_discovered_repo_is_not_stale(self) -> None:
        """ADR 0101, in the direction bd uhxjc was bitten by.

        The inventory here is already correct (``resolve_source_files`` uses
        the fixed walker), so this is the assertion that widening the
        *strategy* side to match cannot flip the repo to permanently stale --
        a file the accounting marks in scope but the state can never cover
        refreshes on every read, forever.
        """
        stale = self._stale()
        self.assertEqual(
            stale.get("stale_sources"), [],
            "a repo reports stale sources immediately after a clean, "
            "successful `wd discover`",
        )
        self.assertFalse(stale.get("coverage_stale"), stale)
        self.assertFalse(stale.get("stale"), stale)

    def test_editing_a_matched_file_is_noticed(self) -> None:
        """ADR 0017 ``source_stale`` must reach these files too.

        The *reason* is the assertion, not the path: a file the strategies
        never read can still be listed, as ``NEVER_INGESTED``, so "is it
        listed?" answers yes for the opposite reason and proves nothing. Only
        ``CONTENT_DIFFERS`` means the graph knew the file and noticed the
        edit. The only case here that mutates the tree, so it restores the
        body on cleanup: its neighbours read the graph this one run produced.
        """
        target = self.root / ROUTERS[0]
        target.write_text(
            router("orders", "/orders") + "\n# touched\n",
            encoding="utf-8",
        )
        self.addCleanup(target.write_text, TREE[ROUTERS[0]], "utf-8")
        stale = self._stale()
        self.assertTrue(stale.get("source_stale"), stale)
        reasons = {
            entry.get("path"): entry.get("reason")
            for entry in stale.get("stale_sources", [])
        }
        self.assertEqual(reasons.get(ROUTERS[0]), CONTENT_DIFFERS, stale)
        self.assertNotIn(NEVER_INGESTED, reasons.values(), stale)

    def test_discover_reported_the_files_it_wrote(self) -> None:
        """Vacuity floor: an empty graph must not satisfy the above.

        Cheap, and it is the observation the bug report opens with -- a
        ``wd discover`` that exits 0 having emitted nothing for two of these
        four strategies. The runner already asserts the exit code, so what is
        left to pin is that a zero exit meant something.
        """
        self.assertGreaterEqual(len(self._nodes()), 10)


if __name__ == "__main__":
    unittest.main()
