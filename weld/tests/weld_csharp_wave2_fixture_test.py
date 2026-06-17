"""Integration test: Wave 2 C# strategies on the reference fixture.

Exercises ``csharp_aspnet_routes``, ``csharp_efcore``, and
``csharp_test_framework`` together against the upgraded
``weld/tests/fixtures/csharp_project/`` ASP.NET Core + EF Core sample.
The fixture already ships the seams Wave 2 consumes (controller +
attributes, DbContext + DbSets, xUnit ``[Fact]`` markers), so this
test is the canary for "Wave 1 + Wave 2 produce a coherent combined
graph".

It also exercises the ADR 0046 coordination seam: the
``csharp_test_framework`` strategy emits a ``test-suite -[contains]->
file:`` edge whose target ID equals the file ID emitted by the
``test_peer`` strategy. Downstream consumers traversing
``test-suite -[contains]-> file <-[tests]- file:`` (test peer) pick up
the test framework label as edge provenance.

Per ADR 0050 every emitted edge must carry a ``confidence`` value
drawn from :data:`weld.contract.CONFIDENCE_VALUES`; the combined edge
set is asserted at the end of the test.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

# Ensure the in-source weld package is on sys.path when run outside
# Bazel. The test file lives two levels under the repo root.

from weld.contract import CONFIDENCE_VALUES  # noqa: E402
from weld.strategies.csharp_aspnet_routes import (  # noqa: E402
    extract as routes_extract,
)
from weld.strategies.csharp_efcore import (  # noqa: E402
    extract as efcore_extract,
)
from weld.strategies.csharp_test_framework import (  # noqa: E402
    extract as test_framework_extract,
)
from weld.strategies.test_peer import (  # noqa: E402
    extract as test_peer_extract,
)


def _fixture_root() -> Path:
    """Return the fixture path, preferring the Bazel runfiles location."""
    here = Path(__file__).resolve().parent
    candidate = here / "fixtures" / "csharp_project"
    if candidate.is_dir():
        return candidate
    runfiles = os.environ.get("RUNFILES_DIR")
    if runfiles:
        rf_candidate = (
            Path(runfiles)
            / "_main"
            / "weld"
            / "tests"
            / "fixtures"
            / "csharp_project"
        )
        if rf_candidate.is_dir():
            return rf_candidate
    return candidate


class CsharpAspnetRoutesFixtureTest(unittest.TestCase):
    """ASP.NET routes detected from the fixture's OrdersController."""

    def setUp(self) -> None:
        self.root = _fixture_root()
        if not self.root.is_dir():
            self.skipTest(f"Fixture not reachable at {self.root}")
        self.result = routes_extract(
            self.root, {"glob": "**/*.cs"}, {},
        )

    def test_controller_class_emitted(self) -> None:
        # OrdersController is declared in
        # ``src/Sample.Web/Controllers/OrdersController.cs`` with
        # ``[Route("api/[controller]")]``.
        controller_id = (
            "symbol:csharp:Sample.Web.Controllers.OrdersController"
        )
        self.assertIn(controller_id, self.result.nodes)
        props = self.result.nodes[controller_id]["props"]
        self.assertEqual(props["kind"], "controller")
        self.assertEqual(props["route_prefix"], "api/[controller]")

    def test_http_get_and_post_routes_emitted(self) -> None:
        # ``[HttpGet("{id}")]`` -> GET /api/orders/{id}
        # ``[HttpPost]``        -> POST /api/orders
        self.assertIn("route:GET:/api/orders/{id}", self.result.nodes)
        self.assertIn("route:POST:/api/orders", self.result.nodes)

    def test_exposes_edges_link_controller_to_routes(self) -> None:
        controller_id = (
            "symbol:csharp:Sample.Web.Controllers.OrdersController"
        )
        exposed = {
            edge["to"]
            for edge in self.result.edges
            if edge["type"] == "exposes" and edge["from"] == controller_id
        }
        self.assertEqual(
            exposed,
            {
                "route:GET:/api/orders/{id}",
                "route:POST:/api/orders",
            },
        )

    def test_every_route_edge_is_definite(self) -> None:
        for edge in self.result.edges:
            self.assertEqual(edge["props"]["confidence"], "definite")
            self.assertIn(edge["props"]["confidence"], CONFIDENCE_VALUES)


class CsharpEfCoreFixtureTest(unittest.TestCase):
    """EF Core extraction over the fixture's OrderDbContext."""

    def setUp(self) -> None:
        self.root = _fixture_root()
        if not self.root.is_dir():
            self.skipTest(f"Fixture not reachable at {self.root}")
        self.result = efcore_extract(self.root, {"glob": "**/*.cs"}, {})

    def test_dbcontext_symbol_emitted(self) -> None:
        # ``Sample.Dal.OrderDbContext : DbContext``.
        dbcontext_id = "symbol:csharp:Sample.Dal.OrderDbContext"
        self.assertIn(dbcontext_id, self.result.nodes)
        props = self.result.nodes[dbcontext_id]["props"]
        self.assertEqual(props["kind"], "dbcontext")
        self.assertEqual(
            props["entities"], ["Customer", "Order"],
        )

    def test_order_entity_uses_table_attribute(self) -> None:
        # ``Order.cs`` carries ``[Table("orders")]`` -- confidence
        # ``definite``.
        order = self.result.nodes["entity:Order"]
        self.assertEqual(order["props"]["table"], "orders")
        self.assertEqual(order["props"]["table_confidence"], "definite")

    def test_customer_entity_falls_back_to_pluralisation(self) -> None:
        # ``Customer.cs`` has no ``[Table]`` attribute -- the table
        # name is the lower-cased plural of the class name.
        customer = self.result.nodes["entity:Customer"]
        self.assertEqual(customer["props"]["table"], "customers")
        self.assertEqual(customer["props"]["table_confidence"], "inferred")

    def test_contains_edges_link_dbcontext_to_entities(self) -> None:
        dbcontext_id = "symbol:csharp:Sample.Dal.OrderDbContext"
        contained = {
            edge["to"]
            for edge in self.result.edges
            if edge["type"] == "contains" and edge["from"] == dbcontext_id
        }
        self.assertEqual(contained, {"entity:Order", "entity:Customer"})


class CsharpTestFrameworkFixtureTest(unittest.TestCase):
    """xUnit ``[Fact]`` detection over the fixture's test project."""

    def setUp(self) -> None:
        self.root = _fixture_root()
        if not self.root.is_dir():
            self.skipTest(f"Fixture not reachable at {self.root}")
        self.result = test_framework_extract(
            self.root, {"glob": "**/*.cs"}, {},
        )

    def test_xunit_test_suite_emitted(self) -> None:
        suite_id = "test-suite:Sample.Tests.OrdersControllerTests"
        self.assertIn(suite_id, self.result.nodes)
        props = self.result.nodes[suite_id]["props"]
        self.assertEqual(props["test_framework"], "xunit")
        # Two ``[Fact]`` methods in the fixture, sorted alphabetically.
        self.assertEqual(
            props["methods"],
            [
                "Get_returns_order_with_supplied_id",
                "Post_round_trips_payload",
            ],
        )

    def test_contains_edge_to_test_file_emitted(self) -> None:
        suite_id = "test-suite:Sample.Tests.OrdersControllerTests"
        edge = next(
            e for e in self.result.edges
            if e["type"] == "contains" and e["from"] == suite_id
        )
        self.assertEqual(edge["props"]["test_framework"], "xunit")
        self.assertEqual(edge["props"]["confidence"], "definite")


class Adr0046CoordinationTest(unittest.TestCase):
    """The test-suite -> file id matches the test_peer -> file id.

    ADR 0046 emits ``file:<test> -[tests]-> file:<source>`` for any
    C# test file with a peer. ADR 0056 Wave 2 adds
    ``test-suite -[contains]-> file:<test>`` carrying the framework
    label. The two edges must agree on the file id so downstream
    consumers can join them.
    """

    def setUp(self) -> None:
        self.root = _fixture_root()
        if not self.root.is_dir():
            self.skipTest(f"Fixture not reachable at {self.root}")

    def test_test_suite_target_matches_test_peer_file_id(self) -> None:
        framework_result = test_framework_extract(
            self.root, {"glob": "**/*.cs"}, {},
        )
        peer_result = test_peer_extract(
            self.root, {"glob": "**/*Tests.cs"}, {},
        )

        # csharp_test_framework targets the test file via a
        # ``contains`` edge:
        framework_targets = {
            edge["to"]
            for edge in framework_result.edges
            if edge["type"] == "contains"
        }
        # test_peer emits a ``file:`` node for every matched test file
        # regardless of whether a peer source exists. Joining the two
        # populations on file id is the ADR 0046 coordination seam.
        peer_file_nodes = {
            nid for nid in peer_result.nodes if nid.startswith("file:")
        }
        joinable = framework_targets & peer_file_nodes
        self.assertEqual(
            len(joinable), 1,
            "Expected exactly one joinable test file in the fixture "
            f"(framework targets={framework_targets!r}, "
            f"peer file nodes={peer_file_nodes!r})",
        )

    def test_framework_label_available_at_test_suite_edge(self) -> None:
        # The "framework label propagates to test peer edges' provenance"
        # invariant is materialised by the ``test_framework`` prop on
        # the test-suite's contains edge -- not by mutating the
        # test_peer edge directly, which would require strategy
        # ordering. Consumers join the two by matching file ids.
        framework_result = test_framework_extract(
            self.root, {"glob": "**/*.cs"}, {},
        )
        contains_edges = [
            e for e in framework_result.edges if e["type"] == "contains"
        ]
        self.assertTrue(contains_edges)
        for edge in contains_edges:
            self.assertIn("test_framework", edge["props"])
            self.assertIn(
                edge["props"]["test_framework"],
                {"xunit", "nunit", "mstest"},
            )


class Adr0050ConfidenceCoverageTest(unittest.TestCase):
    """Every edge from every Wave 2 strategy carries a confidence value."""

    def setUp(self) -> None:
        self.root = _fixture_root()
        if not self.root.is_dir():
            self.skipTest(f"Fixture not reachable at {self.root}")

    def test_every_emitted_edge_has_valid_confidence(self) -> None:
        sources = (
            routes_extract(self.root, {"glob": "**/*.cs"}, {}),
            efcore_extract(self.root, {"glob": "**/*.cs"}, {}),
            test_framework_extract(self.root, {"glob": "**/*.cs"}, {}),
        )
        for result in sources:
            for edge in result.edges:
                self.assertIn(
                    edge["props"].get("confidence"),
                    CONFIDENCE_VALUES,
                    f"Edge {edge!r} from {result!r} missing confidence",
                )

    def test_every_emitted_node_has_valid_confidence(self) -> None:
        # ADR 0050 mandates confidence on every edge; the Wave 2
        # strategies also stamp it on every emitted node. Assert that
        # invariant so a future refactor cannot silently drop one.
        sources = (
            routes_extract(self.root, {"glob": "**/*.cs"}, {}),
            efcore_extract(self.root, {"glob": "**/*.cs"}, {}),
            test_framework_extract(self.root, {"glob": "**/*.cs"}, {}),
        )
        for result in sources:
            for nid, node in result.nodes.items():
                self.assertIn(
                    node["props"].get("confidence"),
                    CONFIDENCE_VALUES,
                    f"Node {nid!r} missing confidence",
                )


if __name__ == "__main__":
    unittest.main()
