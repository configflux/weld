"""Tests for the express discovery strategy (ADR 0064 criterion 3, TS/JS).

The strategy scans TypeScript / JavaScript source for express
handler-registration callsites and emits one ``route:<METHOD>:<path>``
node per registration -- both the direct ``app.get("/p", h)`` /
``router.post("/p", h)`` form and the chained
``app.route("/p").get(h).post(h)`` form (so a chain mints one route per
verb), plus a thin boundary ``file:`` placeholder and a diagnostic
``exposes`` edge from that file to each route. The route-node shape
mirrors the axum / gin / fastapi / flask / csharp_aspnet_routes
convention that tier-check criterion 3 reads via ``check_express`` in
:mod:`tools._tier_check_framework_typescript`.

Extraction is gated on a real ``express`` import / ``require`` so a
``.get("key")`` callsite on an unrelated object does not over-fire, and
the path must be server-relative (start with ``/``) so the express
settings-getter (``app.get("view engine")``) is not mistaken for a route.
"""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from weld._node_ids import file_id
from weld.contract import CONFIDENCE_VALUES, VALID_EDGE_TYPES, VALID_NODE_TYPES
from weld.strategies._express_routes_helpers import (
    EXPRESS_VERBS,
    boundary_file_id,
    route_id,
    route_node,
)
from weld.strategies._helpers import StrategyResult
from weld.strategies.express import extract


def _write(root: Path, name: str, body: str) -> None:
    (root / name).write_text(textwrap.dedent(body))


_EXPRESS_HEADER = "import express from 'express';\nconst app = express();\n"


class HelperFunctionsTest(unittest.TestCase):
    """Pure helpers from :mod:`weld.strategies._express_routes_helpers`."""

    def test_route_id_uppercases_verb(self) -> None:
        self.assertEqual(route_id("get", "/api/ping"), "route:GET:/api/ping")

    def test_route_id_preserves_path_verbatim(self) -> None:
        # express path params (``:id``) and wildcards (``*``) are taken
        # literally; the strategy does not normalise capture syntax.
        self.assertEqual(
            route_id("DELETE", "/users/:id"), "route:DELETE:/users/:id",
        )

    def test_boundary_file_id_matches_canonical_file_id(self) -> None:
        # The exposes edge only binds if the boundary id equals the
        # canonical tree-sitter ``file:`` id for the same path.
        self.assertEqual(boundary_file_id("src/app.ts"), file_id("src/app.ts"))
        self.assertEqual(boundary_file_id("src/app.ts"), "file:src/app")
        self.assertEqual(
            boundary_file_id("src/routes/admin.ts"),
            "file:src/routes/admin",
        )

    def test_route_node_shape_is_contract_valid(self) -> None:
        node = route_node(
            verb="GET", path="/health", rel_path="src/app.ts",
            source="verb_call",
        )
        self.assertEqual(node["type"], "route")
        self.assertIn(node["type"], VALID_NODE_TYPES)
        props = node["props"]
        self.assertEqual(props["source_strategy"], "express")
        self.assertEqual(props["method"], "GET")
        self.assertEqual(props["path"], "/health")
        self.assertEqual(props["authority"], "canonical")
        self.assertEqual(props["language"], "typescript")
        self.assertIn(props["confidence"], CONFIDENCE_VALUES)
        self.assertEqual(props["boundary_kind"], "inbound")


class ExpressMissingAndEmptyTest(unittest.TestCase):
    """Defensive cases: missing dir, no express import, unreadable files."""

    def test_missing_glob_parent_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = extract(Path(tmp), {"glob": "svc/*.ts"}, {})
            self.assertIsInstance(result, StrategyResult)
            self.assertEqual(result.nodes, {})
            self.assertEqual(result.edges, [])

    def test_file_without_express_import_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "app.ts", """\
                // A map exposes .get-looking shapes but is not express, so
                // the import gate must drop it.
                const cache = new Map();
                cache.get('/x');
                app.get('/y', handler);
            """)
            result = extract(root, {"glob": "**/*.ts"}, {})
            self.assertEqual(result.nodes, {})
            self.assertEqual(result.edges, [])

    def test_express_session_import_does_not_fire_the_gate(self) -> None:
        # ``from 'express-session'`` must not satisfy the ``express`` gate
        # (longer package name; quote-bounded guard).
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "app.ts", """\
                import session from 'express-session';
                app.get('/x', handler);
            """)
            result = extract(root, {"glob": "**/*.ts"}, {})
            self.assertEqual(result.nodes, {})

    def test_express_import_but_no_routes_emits_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root, "app.ts",
                _EXPRESS_HEADER + "export const port = 3000;\n",
            )
            result = extract(root, {"glob": "**/*.ts"}, {})
            self.assertEqual(result.nodes, {})

    def test_require_form_satisfies_the_import_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "server.js", """\
                const express = require('express');
                const app = express();
                app.put('/cfg', handler);
            """)
            result = extract(root, {"glob": "**/*.js"}, {})
            self.assertIn("route:PUT:/cfg", result.nodes)


class ExpressVerbCallTest(unittest.TestCase):
    """Direct ``app.<verb>("/path", handler)`` registrations."""

    def test_get_emits_route_node_and_exposes_edge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            _write(root, "src/app.ts", _EXPRESS_HEADER + textwrap.dedent("""\
                app.get('/health', (req, res) => res.send('ok'));
            """))
            result = extract(root, {"glob": "**/*.ts"}, {})
            self.assertIn("route:GET:/health", result.nodes)
            node = result.nodes["route:GET:/health"]
            self.assertEqual(node["type"], "route")
            self.assertEqual(node["props"]["source_strategy"], "express")
            self.assertEqual(node["props"]["route_source"], "verb_call")
            # Exactly one exposes edge, bound to the canonical file id.
            self.assertEqual(len(result.edges), 1)
            edge = result.edges[0]
            self.assertEqual(edge["type"], "exposes")
            self.assertIn(edge["type"], VALID_EDGE_TYPES)
            self.assertEqual(edge["from"], "file:src/app")
            self.assertEqual(edge["to"], "route:GET:/health")
            # A boundary file placeholder exists so the edge is not dangling.
            self.assertIn("file:src/app", result.nodes)

    def test_all_verb_builders_recognised(self) -> None:
        body = _EXPRESS_HEADER
        for verb in EXPRESS_VERBS:
            body += f"app.{verb.lower()}('/{verb.lower()}', h);\n"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "app.ts", body)
            result = extract(root, {"glob": "**/*.ts"}, {})
            for verb in EXPRESS_VERBS:
                self.assertIn(f"route:{verb}:/{verb.lower()}", result.nodes)

    def test_router_receiver_is_recognised(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "app.ts", _EXPRESS_HEADER + textwrap.dedent("""\
                const router = express.Router();
                router.delete('/users/:id', removeUser);
            """))
            result = extract(root, {"glob": "**/*.ts"}, {})
            self.assertIn("route:DELETE:/users/:id", result.nodes)

    def test_use_is_not_a_route_verb(self) -> None:
        # ``app.use`` is middleware mounting, not a route registration.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "app.ts", _EXPRESS_HEADER + textwrap.dedent("""\
                app.use('/static', serveStatic);
                app.get('/ok', h);
            """))
            result = extract(root, {"glob": "**/*.ts"}, {})
            route_ids = {n for n in result.nodes if n.startswith("route:")}
            self.assertEqual(route_ids, {"route:GET:/ok"})

    def test_settings_getter_is_not_a_route(self) -> None:
        # ``app.get("view engine")`` is the express settings getter (one
        # non-path argument); the leading-slash guard drops it.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "app.ts", _EXPRESS_HEADER + textwrap.dedent("""\
                const engine = app.get('view engine');
                app.get('/real', h);
            """))
            result = extract(root, {"glob": "**/*.ts"}, {})
            route_ids = {n for n in result.nodes if n.startswith("route:")}
            self.assertEqual(route_ids, {"route:GET:/real"})

    def test_wildcard_and_capture_paths_taken_verbatim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "app.ts", _EXPRESS_HEADER + textwrap.dedent("""\
                app.get('/assets/*', serveAsset);
                app.get('/users/:id', showUser);
            """))
            result = extract(root, {"glob": "**/*.ts"}, {})
            self.assertIn("route:GET:/assets/*", result.nodes)
            self.assertIn("route:GET:/users/:id", result.nodes)

    def test_double_quoted_path_is_recognised(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root, "app.ts",
                _EXPRESS_HEADER + 'app.post("/users", createUser);\n',
            )
            result = extract(root, {"glob": "**/*.ts"}, {})
            self.assertIn("route:POST:/users", result.nodes)


class ExpressRouteChainTest(unittest.TestCase):
    """``app.route("/path").get(h).post(h)`` chained registrations."""

    def test_route_chain_explodes_into_one_route_per_verb(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "app.ts", _EXPRESS_HEADER + textwrap.dedent("""\
                app.route('/items')
                   .get(listItems)
                   .post(createItem);
            """))
            result = extract(root, {"glob": "**/*.ts"}, {})
            self.assertIn("route:GET:/items", result.nodes)
            self.assertIn("route:POST:/items", result.nodes)
            self.assertEqual(
                result.nodes["route:GET:/items"]["props"]["route_source"],
                "route_chain",
            )

    def test_route_chain_does_not_absorb_following_statement(self) -> None:
        # A chain terminated by ``;`` must not fold the next statement's
        # verb into the prior path.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "app.ts", _EXPRESS_HEADER + textwrap.dedent("""\
                app.route('/items').get(listItems);
                app.post('/other', createOther);
            """))
            result = extract(root, {"glob": "**/*.ts"}, {})
            self.assertIn("route:GET:/items", result.nodes)
            self.assertIn("route:POST:/other", result.nodes)
            # The /items path must NOT have absorbed the POST verb.
            self.assertNotIn("route:POST:/items", result.nodes)

    def test_route_chain_non_verb_member_is_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "app.ts", _EXPRESS_HEADER + textwrap.dedent("""\
                app.route('/x').all(guard).get(handler);
            """))
            result = extract(root, {"glob": "**/*.ts"}, {})
            # ``all`` and ``get`` are verbs; both survive.
            self.assertIn("route:ALL:/x", result.nodes)
            self.assertIn("route:GET:/x", result.nodes)


class ExpressCommentTest(unittest.TestCase):
    """Commented-out registrations must not mint routes (false-positive)."""

    def test_commented_out_registration_is_not_a_route(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "app.ts", _EXPRESS_HEADER + textwrap.dedent("""\
                // app.get('/disabled', disabled);
                app.get('/live', live);
            """))
            result = extract(root, {"glob": "**/*.ts"}, {})
            self.assertIn("route:GET:/live", result.nodes)
            self.assertNotIn("route:GET:/disabled", result.nodes)


class ExpressDeterminismTest(unittest.TestCase):
    """ADR 0012: route ids are emitted in a stable sorted order."""

    def test_route_ids_are_sorted_independent_of_source_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "app.ts", _EXPRESS_HEADER + textwrap.dedent("""\
                app.post('/zebra', z);
                app.get('/apple', a);
                app.put('/mango', m);
            """))
            result = extract(root, {"glob": "**/*.ts"}, {})
            route_ids = [n for n in result.nodes if n.startswith("route:")]
            self.assertEqual(route_ids, sorted(route_ids))

    def test_duplicate_route_id_emits_single_node_and_edge(self) -> None:
        # The same GET:/dup declared twice -> one node + one edge.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root, "app.ts", _EXPRESS_HEADER + textwrap.dedent("""\
                app.get('/dup', a);
                app.get('/dup', b);
            """))
            result = extract(root, {"glob": "**/*.ts"}, {})
            get_edges = [
                e for e in result.edges if e["to"] == "route:GET:/dup"
            ]
            self.assertEqual(len(get_edges), 1)


if __name__ == "__main__":
    unittest.main()
