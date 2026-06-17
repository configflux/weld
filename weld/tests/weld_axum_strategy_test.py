"""Tests for the axum discovery strategy (ADR 0071 / criterion 3, Rust).

The strategy scans Rust source for axum ``Router::new().route(...)``
callsites and emits one ``route:<METHOD>:<path>`` node per method-router
builder named in the registration (so a chained
``get(h).post(h2)`` mints two routes), plus a thin boundary ``file:``
placeholder and a diagnostic ``exposes`` edge from that file to each
route. The route-node shape mirrors the gin / fastapi / flask /
csharp_aspnet_routes convention that tier-check criterion 3 reads via
``check_axum`` in :mod:`tools._tier_check_framework_rust`.

Extraction is gated on a real ``use axum`` import so a ``.route(...)``
callsite on an unrelated builder does not over-fire.
"""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from weld._node_ids import file_id
from weld.contract import CONFIDENCE_VALUES, VALID_EDGE_TYPES, VALID_NODE_TYPES
from weld.strategies._axum_routes_helpers import (
    AXUM_VERBS,
    boundary_file_id,
    route_id,
    route_node,
)
from weld.strategies._helpers import StrategyResult
from weld.strategies.axum import extract


def _write(root: Path, name: str, body: str) -> None:
    (root / name).write_text(textwrap.dedent(body))


_AXUM_HEADER = "use axum::{Router, routing::get};\n"


class HelperFunctionsTest(unittest.TestCase):
    """Pure helpers from :mod:`weld.strategies._axum_routes_helpers`."""

    def test_route_id_uppercases_verb(self) -> None:
        self.assertEqual(route_id("get", "/api/ping"), "route:GET:/api/ping")

    def test_route_id_preserves_path_verbatim(self) -> None:
        # axum path params (``:id`` legacy / ``{id}`` 0.7, ``*path``) are
        # taken literally; the strategy does not normalise capture syntax.
        self.assertEqual(
            route_id("DELETE", "/users/{id}"), "route:DELETE:/users/{id}",
        )

    def test_boundary_file_id_matches_canonical_file_id(self) -> None:
        # The exposes edge only binds if the boundary id equals the
        # canonical tree-sitter ``file:`` id for the same path.
        self.assertEqual(boundary_file_id("src/main.rs"), file_id("src/main.rs"))
        self.assertEqual(boundary_file_id("src/main.rs"), "file:src/main")
        self.assertEqual(
            boundary_file_id("src/api/routes.rs"),
            "file:src/api/routes",
        )

    def test_route_node_shape_is_contract_valid(self) -> None:
        node = route_node(
            verb="GET", path="/health", rel_path="src/main.rs",
            source="route_builder",
        )
        self.assertEqual(node["type"], "route")
        self.assertIn(node["type"], VALID_NODE_TYPES)
        props = node["props"]
        self.assertEqual(props["source_strategy"], "axum")
        self.assertEqual(props["method"], "GET")
        self.assertEqual(props["path"], "/health")
        self.assertEqual(props["authority"], "canonical")
        self.assertIn(props["confidence"], CONFIDENCE_VALUES)
        self.assertEqual(props["boundary_kind"], "inbound")


class AxumMissingAndEmptyTest(unittest.TestCase):
    """Defensive cases: missing dir, no axum import, unreadable files."""

    def test_missing_glob_parent_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = extract(Path(tmp), {"glob": "svc/*.rs"}, {})
            self.assertIsInstance(result, StrategyResult)
            self.assertEqual(result.nodes, {})
            self.assertEqual(result.edges, [])

    def test_file_without_axum_import_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "main.rs", """\
                // A non-axum builder exposes .route-looking shapes but is
                // not axum, so the import gate must drop it.
                fn app() -> Other {
                    Other::new().route("/x", get(handler))
                }
            """)
            result = extract(root, {"glob": "**/*.rs"}, {})
            self.assertEqual(result.nodes, {})
            self.assertEqual(result.edges, [])

    def test_axum_extra_import_does_not_fire_the_gate(self) -> None:
        # ``use axum_extra::...`` must not satisfy the ``use axum`` gate
        # (longer crate name; word-boundary guard).
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "main.rs", """\
                use axum_extra::routing::RouterExt;
                fn app() -> Router {
                    Router::new().route("/x", get(handler))
                }
            """)
            result = extract(root, {"glob": "**/*.rs"}, {})
            self.assertEqual(result.nodes, {})

    def test_axum_import_but_no_routes_emits_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root, "main.rs",
                _AXUM_HEADER + "fn app() -> Router { Router::new() }\n",
            )
            result = extract(root, {"glob": "**/*.rs"}, {})
            self.assertEqual(result.nodes, {})


class AxumRouteBuilderTest(unittest.TestCase):
    """``.route("/path", get(handler))`` and verb siblings."""

    def test_get_emits_route_node_and_exposes_edge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            _write(root, "src/main.rs", _AXUM_HEADER + textwrap.dedent("""\
                fn app() -> Router {
                    Router::new().route("/health", get(health))
                }
            """))
            result = extract(root, {"glob": "**/*.rs"}, {})
            self.assertIn("route:GET:/health", result.nodes)
            node = result.nodes["route:GET:/health"]
            self.assertEqual(node["type"], "route")
            self.assertEqual(node["props"]["source_strategy"], "axum")
            self.assertEqual(node["props"]["route_source"], "route_builder")
            # Exactly one exposes edge, bound to the canonical file id.
            self.assertEqual(len(result.edges), 1)
            edge = result.edges[0]
            self.assertEqual(edge["type"], "exposes")
            self.assertIn(edge["type"], VALID_EDGE_TYPES)
            self.assertEqual(edge["from"], "file:src/main")
            self.assertEqual(edge["to"], "route:GET:/health")
            # A boundary file placeholder exists so the edge is not dangling.
            self.assertIn("file:src/main", result.nodes)

    def test_all_verb_builders_recognised(self) -> None:
        body = _AXUM_HEADER + "fn app() -> Router {\n\tRouter::new()\n"
        for verb in AXUM_VERBS:
            body += f'\t\t.route("/{verb.lower()}", {verb.lower()}(h))\n'
        body += "}\n"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "main.rs", body)
            result = extract(root, {"glob": "**/*.rs"}, {})
            for verb in AXUM_VERBS:
                self.assertIn(f"route:{verb}:/{verb.lower()}", result.nodes)

    def test_method_chaining_explodes_into_one_route_per_verb(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "main.rs", _AXUM_HEADER + textwrap.dedent("""\
                fn app() -> Router {
                    Router::new()
                        .route("/users", get(list_users).post(create_user))
                }
            """))
            result = extract(root, {"glob": "**/*.rs"}, {})
            self.assertIn("route:GET:/users", result.nodes)
            self.assertIn("route:POST:/users", result.nodes)
            self.assertEqual(
                result.nodes["route:GET:/users"]["props"]["route_source"],
                "route_builder",
            )

    def test_non_axum_builder_in_arg_is_dropped(self) -> None:
        # A handler-layer call (``.layer(...)``) or unknown builder must
        # not mint a junk verb; only AXUM_VERBS survive.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "main.rs", _AXUM_HEADER + textwrap.dedent("""\
                fn app() -> Router {
                    Router::new()
                        .route("/x", get(h).layer(mw))
                        .route("/y", any(h))
                }
            """))
            result = extract(root, {"glob": "**/*.rs"}, {})
            # ``get`` survives; ``layer`` (not a verb) does not mint a route.
            self.assertIn("route:GET:/x", result.nodes)
            route_ids = {n for n in result.nodes if n.startswith("route:")}
            # ``any`` is not an axum builder -> /y produces no route at all.
            self.assertEqual(route_ids, {"route:GET:/x"})

    def test_wildcard_and_capture_paths_taken_verbatim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "main.rs", _AXUM_HEADER + textwrap.dedent("""\
                fn app() -> Router {
                    Router::new()
                        .route("/assets/*path", get(serve))
                        .route("/users/{id}", get(show))
                }
            """))
            result = extract(root, {"glob": "**/*.rs"}, {})
            self.assertIn("route:GET:/assets/*path", result.nodes)
            self.assertIn("route:GET:/users/{id}", result.nodes)


class AxumCommentTest(unittest.TestCase):
    """Commented-out registrations must not mint routes (false-positive)."""

    def test_commented_out_registration_is_not_a_route(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "main.rs", _AXUM_HEADER + textwrap.dedent("""\
                fn app() -> Router {
                    Router::new()
                        // .route("/disabled", get(disabled))
                        .route("/live", get(live))
                }
            """))
            result = extract(root, {"glob": "**/*.rs"}, {})
            self.assertIn("route:GET:/live", result.nodes)
            self.assertNotIn("route:GET:/disabled", result.nodes)


class AxumDeterminismTest(unittest.TestCase):
    """ADR 0012: route ids are emitted in a stable sorted order."""

    def test_route_ids_are_sorted_independent_of_source_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "main.rs", _AXUM_HEADER + textwrap.dedent("""\
                fn app() -> Router {
                    Router::new()
                        .route("/zebra", post(z))
                        .route("/apple", get(a))
                        .route("/mango", put(m))
                }
            """))
            result = extract(root, {"glob": "**/*.rs"}, {})
            route_ids = [n for n in result.nodes if n.startswith("route:")]
            self.assertEqual(route_ids, sorted(route_ids))

    def test_duplicate_route_id_emits_single_node_and_edge(self) -> None:
        # The same GET:/dup declared twice -> one node + one edge.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "main.rs", _AXUM_HEADER + textwrap.dedent("""\
                fn app() -> Router {
                    Router::new()
                        .route("/dup", get(a))
                        .route("/dup", get(b))
                }
            """))
            result = extract(root, {"glob": "**/*.rs"}, {})
            get_edges = [
                e for e in result.edges if e["to"] == "route:GET:/dup"
            ]
            self.assertEqual(len(get_edges), 1)


if __name__ == "__main__":
    unittest.main()
