"""Tests for the ``csharp_aspnet_routes`` strategy (ADR 0056 Wave 2)."""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from weld.contract import CONFIDENCE_VALUES, VALID_NODE_TYPES
from weld.strategies._csharp_routes_helpers import (
    _strip_controller_suffix,
    join_route,
    route_id,
    symbol_id,
)
from weld.strategies._helpers import StrategyResult
from weld.strategies.csharp_aspnet_routes import extract


class HelperFunctionsTest(unittest.TestCase):
    """Helpers from :mod:`weld.strategies._csharp_routes_helpers`."""

    def test_symbol_id_with_namespace(self) -> None:
        self.assertEqual(
            symbol_id("Sample.Web.Controllers", "OrdersController"),
            "symbol:csharp:Sample.Web.Controllers.OrdersController",
        )

    def test_symbol_id_without_namespace(self) -> None:
        self.assertEqual(
            symbol_id("", "Foo"),
            "symbol:csharp:Foo",
        )

    def test_route_id_uppercases_verb(self) -> None:
        self.assertEqual(
            route_id("Get", "/api/orders"),
            "route:GET:/api/orders",
        )

    def test_strip_controller_suffix_removes_suffix(self) -> None:
        self.assertEqual(
            _strip_controller_suffix("OrdersController"), "Orders",
        )

    def test_strip_controller_keeps_literal_controller(self) -> None:
        # A class literally named ``Controller`` is left intact so the
        # expansion does not collapse to an empty string.
        self.assertEqual(_strip_controller_suffix("Controller"), "Controller")

    def test_strip_controller_no_suffix_is_passthrough(self) -> None:
        self.assertEqual(_strip_controller_suffix("Service"), "Service")


class JoinRouteTest(unittest.TestCase):
    """``join_route`` concatenates prefix + template + expands tokens."""

    def test_join_with_controller_token(self) -> None:
        # The MVC ``[controller]`` token expands to the lower-cased
        # class name minus ``Controller``.
        self.assertEqual(
            join_route(
                "api/[controller]",
                "{id}",
                controller_name="OrdersController",
                action_name="Get",
            ),
            "/api/orders/{id}",
        )

    def test_join_with_action_token(self) -> None:
        self.assertEqual(
            join_route(
                "api",
                "[action]/{id}",
                controller_name="OrdersController",
                action_name="GetById",
            ),
            "/api/GetById/{id}",
        )

    def test_join_strips_leading_and_trailing_slashes(self) -> None:
        # ``Route("/api/")`` + ``HttpGet("/")`` must not produce
        # duplicate slashes in the final ID.
        self.assertEqual(
            join_route(
                "/api/",
                "/",
                controller_name="Foo",
                action_name="Method",
            ),
            "/api",
        )

    def test_join_empty_inputs_yields_root(self) -> None:
        self.assertEqual(
            join_route("", "", controller_name="X", action_name="Y"),
            "/",
        )

    def test_join_preserves_unknown_tokens(self) -> None:
        # Non-MVC ``{...}`` and ``[...]`` patterns are preserved
        # verbatim so downstream consumers can inspect them.
        self.assertEqual(
            join_route(
                "/api",
                "{version:apiVersion}/items",
                controller_name="ItemsController",
                action_name="List",
            ),
            "/api/{version:apiVersion}/items",
        )


class ControllerExtractionTest(unittest.TestCase):
    """End-to-end ``extract()`` cases for controller routes."""

    def _write(self, path: Path, body: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body), encoding="utf-8")

    def test_route_attribute_with_http_get_emits_route_node(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write(
                root / "Web" / "Controllers" / "OrdersController.cs",
                """\
                using Microsoft.AspNetCore.Mvc;
                namespace Sample.Web.Controllers;

                [ApiController]
                [Route("api/[controller]")]
                public class OrdersController : ControllerBase
                {
                    [HttpGet("{id}")]
                    public Task GetAsync(int id) => Task.CompletedTask;
                }
                """,
            )
            result = extract(root, {"glob": "**/*.cs"}, {})
            self.assertIsInstance(result, StrategyResult)

            controller_id = (
                "symbol:csharp:Sample.Web.Controllers.OrdersController"
            )
            self.assertIn(controller_id, result.nodes)
            self.assertEqual(
                result.nodes[controller_id]["props"]["kind"], "controller",
            )

            route_id_value = "route:GET:/api/orders/{id}"
            self.assertIn(route_id_value, result.nodes)
            self.assertIn("route", VALID_NODE_TYPES)
            self.assertEqual(
                result.nodes[route_id_value]["props"]["confidence"], "definite",
            )
            self.assertEqual(
                result.nodes[route_id_value]["props"]["route_source"],
                "attribute",
            )

            edge = next(
                e for e in result.edges
                if e["type"] == "exposes" and e["to"] == route_id_value
            )
            self.assertEqual(edge["from"], controller_id)
            self.assertEqual(edge["props"]["confidence"], "definite")

    def test_multiple_http_verbs_emit_multiple_routes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            self._write(
                root / "X.cs",
                """\
                using Microsoft.AspNetCore.Mvc;
                namespace App;

                [Route("api/[controller]")]
                public class WidgetsController : ControllerBase
                {
                    [HttpGet]
                    public Task List() => Task.CompletedTask;

                    [HttpPost]
                    public Task Create() => Task.CompletedTask;

                    [HttpDelete("{id}")]
                    public Task Delete(int id) => Task.CompletedTask;
                }
                """,
            )
            result = extract(root, {"glob": "**/*.cs"}, {})
            paths = sorted(
                nid for nid in result.nodes if nid.startswith("route:")
            )
            self.assertEqual(
                paths,
                [
                    "route:DELETE:/api/widgets/{id}",
                    "route:GET:/api/widgets",
                    "route:POST:/api/widgets",
                ],
            )

    def test_no_route_attribute_falls_back_to_class_name_heuristic(self) -> None:
        # An ``[ApiController]`` class without ``[Route]`` still emits
        # routes -- the verb-only attribute defines the path.
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "X.cs").write_text(
                textwrap.dedent("""\
                using Microsoft.AspNetCore.Mvc;
                namespace App;
                [ApiController]
                public class FooController : ControllerBase
                {
                    [HttpGet("ping")]
                    public Task Ping() => Task.CompletedTask;
                }
                """),
                encoding="utf-8",
            )
            result = extract(root, {"glob": "**/*.cs"}, {})
            self.assertIn("route:GET:/ping", result.nodes)

    def test_non_controller_class_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "Service.cs").write_text(
                textwrap.dedent("""\
                namespace App;
                public class Service
                {
                    public void Method() { }
                }
                """),
                encoding="utf-8",
            )
            result = extract(root, {"glob": "**/*.cs"}, {})
            self.assertEqual(result.nodes, {})


class MinimalApiExtractionTest(unittest.TestCase):
    """``.MapVerb("/path", ...)`` callsites are picked up lexically."""

    def test_map_get_emits_route_with_inferred_confidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "Program.cs").write_text(
                textwrap.dedent("""\
                var app = builder.Build();
                app.MapGet("/health", () => Results.Ok("up"));
                app.Run();
                """),
                encoding="utf-8",
            )
            result = extract(root, {"glob": "**/*.cs"}, {})
            node = result.nodes["route:GET:/health"]
            self.assertEqual(node["props"]["confidence"], "inferred")
            self.assertEqual(
                node["props"]["route_source"], "minimal_api",
            )
            self.assertEqual(node["props"]["authority"], "derived")

    def test_minimal_api_does_not_clobber_attribute_route(self) -> None:
        # Same ``GET /api/orders`` declared via both forms; the
        # attribute-based emission must win (it carries the strongest
        # provenance and a controller reference).
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "C.cs").write_text(
                textwrap.dedent("""\
                using Microsoft.AspNetCore.Mvc;
                namespace App;
                [Route("api/[controller]")]
                public class OrdersController : ControllerBase
                {
                    [HttpGet]
                    public Task List() => Task.CompletedTask;
                }

                public static class Bootstrap
                {
                    public static void Wire(WebApplication app)
                    {
                        app.MapGet("/api/orders", () => 0);
                    }
                }
                """),
                encoding="utf-8",
            )
            result = extract(root, {"glob": "**/*.cs"}, {})
            node = result.nodes["route:GET:/api/orders"]
            # The attribute version wins -- confidence stays definite.
            self.assertEqual(node["props"]["confidence"], "definite")
            self.assertEqual(node["props"]["route_source"], "attribute")


class EdgeContractTest(unittest.TestCase):
    """ADR 0050 contract: every edge declares a CONFIDENCE_VALUES value."""

    def test_attribute_edges_are_definite(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "X.cs").write_text(
                textwrap.dedent("""\
                using Microsoft.AspNetCore.Mvc;
                namespace App;
                [Route("api/[controller]")]
                public class WidgetsController : ControllerBase
                {
                    [HttpGet] public Task L() => Task.CompletedTask;
                }
                """),
                encoding="utf-8",
            )
            result = extract(root, {"glob": "**/*.cs"}, {})
            for edge in result.edges:
                self.assertEqual(edge["props"]["confidence"], "definite")
                self.assertIn(edge["props"]["confidence"], CONFIDENCE_VALUES)

    def test_every_emitted_node_has_a_valid_confidence(self) -> None:
        # ADR 0050 strictly applies to edges, but route nodes also
        # carry confidence so the value distribution is honest from
        # both directions.
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "Program.cs").write_text(
                textwrap.dedent("""\
                var app = builder.Build();
                app.MapGet("/health", () => 0);
                app.Run();
                """),
                encoding="utf-8",
            )
            result = extract(root, {"glob": "**/*.cs"}, {})
            for node in result.nodes.values():
                self.assertIn(node["props"]["confidence"], CONFIDENCE_VALUES)


class RobustnessTest(unittest.TestCase):
    """Pathological inputs must not crash discovery."""

    def test_unreadable_file_does_not_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "Bad.cs").write_bytes(b"\xff\xfe\xfd not utf-8")
            result = extract(root, {"glob": "**/*.cs"}, {})
            self.assertEqual(result.nodes, {})

    def test_empty_directory_returns_empty_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            result = extract(root, {"glob": "**/*.cs"}, {})
            self.assertEqual(result.nodes, {})
            self.assertEqual(result.edges, [])


class DeterminismTest(unittest.TestCase):
    """Two consecutive runs yield byte-identical output."""

    def test_consecutive_runs_yield_identical_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "C.cs").write_text(
                textwrap.dedent("""\
                using Microsoft.AspNetCore.Mvc;
                namespace App;
                [Route("api/[controller]")]
                public class OrdersController : ControllerBase
                {
                    [HttpGet("{id}")] public Task G(int id) => Task.CompletedTask;
                }
                """),
                encoding="utf-8",
            )
            first = extract(root, {"glob": "**/*.cs"}, {})
            second = extract(root, {"glob": "**/*.cs"}, {})
            self.assertEqual(first.nodes, second.nodes)
            self.assertEqual(first.edges, second.edges)


if __name__ == "__main__":
    unittest.main()
