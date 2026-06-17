"""Tests for the gin discovery strategy (ADR 0071 / criterion 3, GO-2).

The strategy scans Go source for gin handler-registration callsites and
emits one ``route:<METHOD>:<path>`` node per registration (per concrete
verb for ``Any``), plus a thin boundary ``file:`` placeholder and a
diagnostic ``exposes`` edge from that file to each route. The route-node
shape mirrors the fastapi / flask / csharp_aspnet_routes convention that
tier-check criterion 3 reads via ``check_gin`` in
:mod:`tools._tier_check_framework_go`.

Extraction is gated on a real ``github.com/gin-gonic/gin`` import so a
``.GET(...)`` callsite on an unrelated builder does not over-fire.
"""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from weld._node_ids import file_id
from weld.contract import CONFIDENCE_VALUES, VALID_EDGE_TYPES, VALID_NODE_TYPES
from weld.strategies._gin_routes_helpers import (
    GIN_VERBS,
    boundary_file_id,
    route_id,
    route_node,
)
from weld.strategies._helpers import StrategyResult
from weld.strategies.gin import extract


def _write(root: Path, name: str, body: str) -> None:
    (root / name).write_text(textwrap.dedent(body))


_GIN_HEADER = (
    'package main\n'
    'import "github.com/gin-gonic/gin"\n'
)


class HelperFunctionsTest(unittest.TestCase):
    """Pure helpers from :mod:`weld.strategies._gin_routes_helpers`."""

    def test_route_id_uppercases_verb(self) -> None:
        self.assertEqual(route_id("get", "/api/ping"), "route:GET:/api/ping")

    def test_route_id_preserves_path_verbatim(self) -> None:
        # gin path params (``:id``, ``*all``) are taken literally.
        self.assertEqual(
            route_id("DELETE", "/users/:id"), "route:DELETE:/users/:id",
        )

    def test_boundary_file_id_matches_canonical_file_id(self) -> None:
        # The exposes edge only binds if the boundary id equals the
        # canonical tree-sitter ``file:`` id for the same path.
        self.assertEqual(boundary_file_id("main.go"), file_id("main.go"))
        self.assertEqual(boundary_file_id("main.go"), "file:main")
        self.assertEqual(
            boundary_file_id("internal/api/server.go"),
            "file:internal/api/server",
        )

    def test_route_node_shape_is_contract_valid(self) -> None:
        node = route_node(
            verb="GET", path="/health", rel_path="main.go",
            source="verb_method",
        )
        self.assertEqual(node["type"], "route")
        self.assertIn(node["type"], VALID_NODE_TYPES)
        props = node["props"]
        self.assertEqual(props["source_strategy"], "gin")
        self.assertEqual(props["method"], "GET")
        self.assertEqual(props["path"], "/health")
        self.assertEqual(props["authority"], "canonical")
        self.assertIn(props["confidence"], CONFIDENCE_VALUES)
        self.assertEqual(props["boundary_kind"], "inbound")


class GinMissingAndEmptyTest(unittest.TestCase):
    """Defensive cases: missing dir, no gin import, unreadable files."""

    def test_missing_glob_parent_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = extract(Path(tmp), {"glob": "svc/*.go"}, {})
            self.assertIsInstance(result, StrategyResult)
            self.assertEqual(result.nodes, {})
            self.assertEqual(result.edges, [])

    def test_file_without_gin_import_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "main.go", """\
                package main
                // A net/http mux exposes .GET-looking shapes but is not gin.
                func main() {
                    mux.GET("/x", handler)
                }
            """)
            result = extract(root, {"glob": "**/*.go"}, {})
            self.assertEqual(result.nodes, {})
            self.assertEqual(result.edges, [])

    def test_gin_import_but_no_routes_emits_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "main.go", _GIN_HEADER + "func main() { _ = gin.Default() }\n")
            result = extract(root, {"glob": "**/*.go"}, {})
            self.assertEqual(result.nodes, {})


class GinVerbMethodTest(unittest.TestCase):
    """``r.GET("/path", ...)`` and verb siblings."""

    def test_get_emits_route_node_and_exposes_edge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "main.go", _GIN_HEADER + textwrap.dedent("""\
                func main() {
                    r := gin.Default()
                    r.GET("/health", func(c *gin.Context) {})
                }
            """))
            result = extract(root, {"glob": "**/*.go"}, {})
            self.assertIn("route:GET:/health", result.nodes)
            node = result.nodes["route:GET:/health"]
            self.assertEqual(node["type"], "route")
            self.assertEqual(node["props"]["source_strategy"], "gin")
            self.assertEqual(node["props"]["route_source"], "verb_method")
            # Exactly one exposes edge, bound to the canonical file id.
            self.assertEqual(len(result.edges), 1)
            edge = result.edges[0]
            self.assertEqual(edge["type"], "exposes")
            self.assertIn(edge["type"], VALID_EDGE_TYPES)
            self.assertEqual(edge["from"], "file:main")
            self.assertEqual(edge["to"], "route:GET:/health")
            # A boundary file placeholder exists so the edge is not dangling.
            self.assertIn("file:main", result.nodes)

    def test_all_verb_methods_recognised(self) -> None:
        body = _GIN_HEADER + "func main() {\n\tr := gin.Default()\n"
        for verb in GIN_VERBS:
            body += f'\tr.{verb}("/{verb.lower()}", h)\n'
        body += "}\n"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "main.go", body)
            result = extract(root, {"glob": "**/*.go"}, {})
            for verb in GIN_VERBS:
                self.assertIn(f"route:{verb}:/{verb.lower()}", result.nodes)

    def test_group_receiver_routes_recognised(self) -> None:
        # The strategy matches the verb method on any receiver, including
        # a route-group variable; group-prefix join is a documented
        # non-goal so the literal relative path is used.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "main.go", _GIN_HEADER + textwrap.dedent("""\
                func main() {
                    r := gin.Default()
                    api := r.Group("/api")
                    api.GET("/ping", handler)
                }
            """))
            result = extract(root, {"glob": "**/*.go"}, {})
            self.assertIn("route:GET:/ping", result.nodes)


class GinAnyAndHandleTest(unittest.TestCase):
    """``Any`` explodes across verbs; ``Handle`` reads the verb literal."""

    def test_any_explodes_into_one_route_per_verb(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "main.go", _GIN_HEADER + textwrap.dedent("""\
                func main() {
                    r := gin.Default()
                    r.Any("/proxy", handler)
                }
            """))
            result = extract(root, {"glob": "**/*.go"}, {})
            for verb in GIN_VERBS:
                self.assertIn(f"route:{verb}:/proxy", result.nodes)
            self.assertEqual(
                result.nodes["route:GET:/proxy"]["props"]["route_source"],
                "any",
            )

    def test_handle_reads_method_literal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "main.go", _GIN_HEADER + textwrap.dedent("""\
                func main() {
                    r := gin.Default()
                    r.Handle("PATCH", "/users/:id", handler)
                }
            """))
            result = extract(root, {"glob": "**/*.go"}, {})
            self.assertIn("route:PATCH:/users/:id", result.nodes)
            self.assertEqual(
                result.nodes["route:PATCH:/users/:id"]["props"]["route_source"],
                "handle",
            )

    def test_handle_with_unknown_verb_is_dropped(self) -> None:
        # A dynamic / typo'd method must not mint a junk route.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "main.go", _GIN_HEADER + textwrap.dedent("""\
                func main() {
                    r := gin.Default()
                    r.Handle("WAT", "/weird", handler)
                }
            """))
            result = extract(root, {"glob": "**/*.go"}, {})
            self.assertEqual(result.nodes, {})

    def test_commented_out_registration_is_not_a_route(self) -> None:
        # A ``// r.GET(...)`` line must not mint a route (the line-comment
        # strip in _scan_routes); the live registration below still does.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "main.go", _GIN_HEADER + textwrap.dedent("""\
                func main() {
                    r := gin.Default()
                    // r.GET("/disabled", handler)
                    r.GET("/live", handler)
                }
            """))
            result = extract(root, {"glob": "**/*.go"}, {})
            self.assertIn("route:GET:/live", result.nodes)
            self.assertNotIn("route:GET:/disabled", result.nodes)


class GinDeterminismTest(unittest.TestCase):
    """ADR 0012: route ids are emitted in a stable sorted order."""

    def test_route_ids_are_sorted_independent_of_source_order(self) -> None:
        # Declared out of order in source; emitted ids must still be sorted.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "main.go", _GIN_HEADER + textwrap.dedent("""\
                func main() {
                    r := gin.Default()
                    r.POST("/zebra", h)
                    r.GET("/apple", h)
                    r.PUT("/mango", h)
                }
            """))
            result = extract(root, {"glob": "**/*.go"}, {})
            route_ids = [n for n in result.nodes if n.startswith("route:")]
            self.assertEqual(route_ids, sorted(route_ids))

    def test_duplicate_route_id_emits_single_node_and_edge(self) -> None:
        # ``Any`` provides GET:/dup and a direct GET:/dup also appears;
        # the first sorted entry wins, leaving one node + one edge for it.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "main.go", _GIN_HEADER + textwrap.dedent("""\
                func main() {
                    r := gin.Default()
                    r.GET("/dup", h)
                    r.Any("/dup", h)
                }
            """))
            result = extract(root, {"glob": "**/*.go"}, {})
            get_edges = [
                e for e in result.edges if e["to"] == "route:GET:/dup"
            ]
            self.assertEqual(len(get_edges), 1)


if __name__ == "__main__":
    unittest.main()
