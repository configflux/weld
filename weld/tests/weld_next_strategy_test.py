"""The Next.js app-router strategy: which files are routes, and at what URL.

The strategy has no callsite grammar to get wrong (that is its express
sibling's job) -- it has *conventions*, and every one of them is a rule about
which files count and what URL a directory chain spells. So this file is
organised around those rules rather than around the functions that implement
them:

* the derivation itself, unit-tested on paths (no filesystem, no I/O), which
  is where the app-directory anchor, route groups, parallel slots, private
  folders and dynamic segments are pinned -- the conventions ADR 0142 D4
  names, in the shape :mod:`weld.strategies._next_routes_helpers` documents
  them (bd lrnx1.7);
* what a handler module exports, across the three export spellings that reach
  a real ``route.ts``;
* what the strategy as a whole emits from a tree on disk -- node shape, the
  diagnostic ``exposes`` edge, provenance, determinism, and the contract
  vocabulary every emitted node and edge must satisfy.

The negative cases carry as much weight as the positive ones here, because
this strategy claims files by *name*: a repository with a ``src/lib/route.ts``
or a ``page.ts`` helper module must get no routes from either, and neither
must a handler someone commented out.
"""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from weld._node_ids import file_id
from weld.contract import CONFIDENCE_VALUES, VALID_EDGE_TYPES, VALID_NODE_TYPES
from weld.strategies._helpers import StrategyResult
from weld.strategies._next_routes_helpers import (
    NEXT_SOURCE_STRATEGY,
    NEXT_VERBS,
    ROUTE_SOURCE_HANDLER,
    ROUTE_SOURCE_PAGE,
    route_path,
)
from weld.strategies.next import extract

#: A handler module that exports the two verbs the readiness corpus's own
#: ``app/api/orders/route.ts`` exports.
_ORDERS_ROUTE_TS = """\
import { formatPrice } from "@acme/shared";

export async function GET(): Promise<Response> {
  return Response.json({ total: formatPrice(4200) });
}

export async function POST(request: Request): Promise<Response> {
  return Response.json({ ok: true });
}
"""

_PAGE_TSX = """\
export default function Home() {
  return <main>hi</main>;
}
"""


def _write(root: Path, rel: str, body: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def _routes(result: StrategyResult) -> dict[str, dict]:
    return {
        nid: node
        for nid, node in result.nodes.items()
        if node.get("type") == "route"
    }


def _exposed(result: StrategyResult) -> set[tuple[str, str, str]]:
    """``(method, path, file)`` per route node -- the probe's own vocabulary."""
    return {
        (
            str(node["props"]["method"]),
            str(node["props"]["path"]),
            str(node["props"]["file"]),
        )
        for node in _routes(result).values()
    }


class RoutePathDerivationTest(unittest.TestCase):
    """The URL a file's directory chain spells (no filesystem involved)."""

    def test_directory_chain_under_app_is_the_path(self) -> None:
        self.assertEqual(
            route_path("apps/web/src/app/api/orders/route.ts"), "/api/orders",
        )

    def test_no_api_prefix_rule_exists(self) -> None:
        """``api/`` is in the URL because it is a directory, not a convention.

        Next.js has no ``/api`` prefix in the app router (that was the pages
        router's ``pages/api``), so neither does this: the chain is taken
        verbatim, and a handler outside an ``api`` directory keeps the URL its
        own directories spell.
        """
        self.assertEqual(route_path("app/orders/route.ts"), "/orders")

    def test_handler_in_the_app_directory_serves_root(self) -> None:
        self.assertEqual(route_path("app/route.ts"), "/")
        self.assertEqual(route_path("src/app/page.tsx"), "/")

    def test_last_app_segment_is_the_anchor(self) -> None:
        """A package *called* ``app`` above the app directory is not it.

        ``packages/app/src/app/...`` is a real monorepo shape, and anchoring
        on the first ``app`` would serve ``/src/app/health`` for it.
        """
        self.assertEqual(
            route_path("packages/app/src/app/health/route.ts"), "/health",
        )

    def test_route_groups_do_not_appear_in_the_url(self) -> None:
        self.assertEqual(
            route_path("app/(marketing)/about/page.tsx"), "/about",
        )
        self.assertEqual(
            route_path("app/(shop)/(sale)/items/route.ts"), "/items",
        )

    def test_parallel_route_slots_do_not_appear_in_the_url(self) -> None:
        self.assertEqual(route_path("app/@modal/login/page.tsx"), "/login")

    def test_dynamic_segments_keep_their_source_spelling(self) -> None:
        """``[id]`` stays ``[id]``, as express keeps ``:id``."""
        self.assertEqual(
            route_path("app/blog/[slug]/route.ts"), "/blog/[slug]",
        )
        self.assertEqual(
            route_path("app/docs/[...path]/page.tsx"), "/docs/[...path]",
        )
        self.assertEqual(
            route_path("app/shop/[[...all]]/route.ts"), "/shop/[[...all]]",
        )

    def test_a_private_folder_is_not_routable(self) -> None:
        """``_folder`` opts out of routing, so it has no URL to report."""
        self.assertIsNone(route_path("app/_internal/route.ts"))
        self.assertIsNone(route_path("app/admin/_lib/helpers/route.ts"))

    def test_a_file_outside_any_app_directory_has_no_url(self) -> None:
        self.assertIsNone(route_path("src/lib/route.ts"))
        self.assertIsNone(route_path("apps/web/route.ts"))


class HandlerExportSpellingsTest(unittest.TestCase):
    """Which exports of a ``route.*`` module are handlers."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _verbs_for(self, body: str) -> set[str]:
        _write(self.root, "app/thing/route.ts", body)
        return {
            str(node["props"]["method"])
            for node in _routes(extract(self.root, {}, {})).values()
        }

    def test_function_declaration_form(self) -> None:
        self.assertEqual(
            self._verbs_for(
                "export async function GET() {}\nexport function DELETE() {}\n"
            ),
            {"GET", "DELETE"},
        )

    def test_const_binding_form(self) -> None:
        self.assertEqual(
            self._verbs_for("export const PATCH = async () => null;\n"),
            {"PATCH"},
        )

    def test_export_list_and_rename_forms(self) -> None:
        """``export { handler as GET }`` exports ``GET``, and is read as one."""
        self.assertEqual(
            self._verbs_for(
                "export { POST };\nexport { handler as PUT } from './h';\n"
            ),
            {"POST", "PUT"},
        )

    def test_a_rename_is_read_at_the_exported_name(self) -> None:
        """``export { GET as handler }`` exports ``handler`` -- not a route."""
        self.assertEqual(
            self._verbs_for("export { GET as handler };\n"), set(),
        )

    def test_every_declared_verb_is_recognised(self) -> None:
        """All seven app-router methods, so none is silently unsupported."""
        body = "".join(
            f"export async function {verb}() {{}}\n" for verb in NEXT_VERBS
        )
        self.assertEqual(self._verbs_for(body), set(NEXT_VERBS))

    def test_non_verb_exports_are_not_routes(self) -> None:
        """``dynamic`` / ``revalidate`` are route-segment config, not methods."""
        self.assertEqual(
            self._verbs_for(
                'export const dynamic = "force-dynamic";\n'
                "export const revalidate = 60;\n"
                "export function formatGET() {}\n"
            ),
            set(),
        )

    def test_a_commented_out_handler_is_not_a_route(self) -> None:
        self.assertEqual(
            self._verbs_for("// export async function GET() {}\n"), set(),
        )


class NextStrategyExtractionTest(unittest.TestCase):
    """What the strategy emits from a tree on disk."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _app(self) -> StrategyResult:
        """The corpus shape: a handler module, a page, and two decoys."""
        _write(self.root, "apps/web/src/app/api/orders/route.ts", _ORDERS_ROUTE_TS)
        _write(self.root, "apps/web/src/app/page.tsx", _PAGE_TSX)
        # Named like an app-router file but outside the app directory.
        _write(self.root, "apps/web/src/lib/route.ts", "export function GET() {}\n")
        # Inside it, but not a page: no default export.
        _write(
            self.root, "apps/web/src/app/helpers/page.ts",
            "export function helper() {}\n",
        )
        return extract(self.root, {}, {})

    def test_handler_module_exposes_one_route_per_verb(self) -> None:
        self.assertEqual(
            {
                exposure
                for exposure in _exposed(self._app())
                if exposure[1] == "/api/orders"
            },
            {
                ("GET", "/api/orders", "apps/web/src/app/api/orders/route.ts"),
                ("POST", "/api/orders", "apps/web/src/app/api/orders/route.ts"),
            },
        )

    def test_a_page_that_re_exports_its_default_is_still_a_page(self) -> None:
        """``export { default } from "./Home"`` is a default export too.

        A page that only re-labels a component elsewhere is a page, and a
        check that read the inline declaration alone would drop its URL.
        """
        _write(self.root, "app/about/page.tsx",
               'export { default } from "./AboutView";\n')
        _write(self.root, "app/team/page.tsx",
               'import { Team } from "./Team";\nexport { Team as default };\n')
        self.assertEqual(
            {e[1] for e in _exposed(extract(self.root, {}, {}))},
            {"/about", "/team"},
        )

    def test_a_page_exposes_the_get_its_url_answers(self) -> None:
        """A page is an inbound GET surface, not a node type of its own."""
        page = _routes(self._app())["route:GET:/"]
        self.assertEqual(page["type"], "route")
        self.assertEqual(page["props"]["route_source"], ROUTE_SOURCE_PAGE)
        self.assertEqual(page["props"]["file"], "apps/web/src/app/page.tsx")

    def test_route_source_separates_handlers_from_pages(self) -> None:
        sources = {
            node["props"]["route_source"]
            for node in _routes(self._app()).values()
        }
        self.assertEqual(sources, {ROUTE_SOURCE_HANDLER, ROUTE_SOURCE_PAGE})

    def test_decoy_files_contribute_nothing(self) -> None:
        """The two files named like app-router entries but serving no URL."""
        paths = {node["props"]["path"] for node in _routes(self._app()).values()}
        self.assertEqual(paths, {"/api/orders", "/"})

    def test_route_node_carries_the_inbound_surface_props(self) -> None:
        """The shared ADR 0086 prop set, so a polyglot route query sees one
        shape whichever framework declared the route."""
        props = _routes(self._app())["route:GET:/api/orders"]["props"]
        self.assertEqual(props["method"], "GET")
        self.assertEqual(props["path"], "/api/orders")
        self.assertEqual(props["source_strategy"], NEXT_SOURCE_STRATEGY)
        self.assertEqual(props["boundary_kind"], "inbound")
        self.assertEqual(props["protocol"], "http")
        self.assertEqual(props["transport"], "http")
        self.assertEqual(props["surface_kind"], "request_response")
        self.assertEqual(props["authority"], "canonical")
        self.assertEqual(props["confidence"], "definite")
        self.assertEqual(
            props["declared_in"], "apps/web/src/app/api/orders/route.ts",
        )

    def test_exposes_edge_targets_the_canonical_file_node_id(self) -> None:
        """The boundary id is the id tree_sitter mints for the same file, so
        the edge does not dangle when the two strategies are paired."""
        result = self._app()
        boundary = file_id("apps/web/src/app/api/orders/route.ts")
        edges = [
            edge for edge in result.edges
            if edge["to"] == "route:GET:/api/orders"
        ]
        self.assertEqual(len(edges), 1, result.edges)
        self.assertEqual(edges[0]["from"], boundary)
        self.assertEqual(edges[0]["type"], "exposes")
        self.assertEqual(
            edges[0]["props"]["source_strategy"], NEXT_SOURCE_STRATEGY,
        )
        self.assertIn(boundary, result.nodes)

    def test_emitted_nodes_and_edges_satisfy_the_contract(self) -> None:
        result = self._app()
        for nid, node in result.nodes.items():
            self.assertIn(node["type"], VALID_NODE_TYPES, nid)
            confidence = node["props"].get("confidence")
            if confidence is not None:
                self.assertIn(confidence, CONFIDENCE_VALUES, nid)
        for edge in result.edges:
            self.assertIn(edge["type"], VALID_EDGE_TYPES, edge)
            self.assertIn(edge["props"]["confidence"], CONFIDENCE_VALUES, edge)

    def test_provenance_covers_every_claimed_file(self) -> None:
        """Including one that yielded no route.

        A handler module that gains its first ``export function POST`` must be
        seen as dirty on the next incremental run; recording only files that
        emitted something is how that stops happening (the bd od2a rule the
        express strategy follows).
        """
        self.assertEqual(
            set(self._app().discovered_from),
            {
                "apps/web/src/app/api/orders/route.ts",
                "apps/web/src/app/page.tsx",
                "apps/web/src/app/helpers/page.ts",
            },
        )

    def test_extraction_is_deterministic(self) -> None:
        """ADR 0012: two runs over the same tree agree node-for-node."""
        first, second = self._app(), extract(self.root, {}, {})
        self.assertEqual(first.nodes, second.nodes)
        self.assertEqual(first.edges, second.edges)
        self.assertEqual(
            sorted(first.discovered_from), sorted(second.discovered_from),
        )

    def test_a_tree_with_no_app_directory_emits_nothing(self) -> None:
        _write(self.root, "src/server.ts", "export function GET() {}\n")
        result = extract(self.root, {}, {})
        self.assertEqual(result.nodes, {})
        self.assertEqual(result.edges, [])

    def test_javascript_dialects_are_read_too(self) -> None:
        """A Next app can be plain JavaScript; the conventions are identical."""
        _write(self.root, "app/health/route.js", "export function GET() {}\n")
        _write(self.root, "app/about/page.jsx", "export default function A() {}\n")
        self.assertEqual(
            {
                (exposure[0], exposure[1])
                for exposure in _exposed(extract(self.root, {}, {}))
            },
            {("GET", "/health"), ("GET", "/about")},
        )

    def test_exclude_is_honoured(self) -> None:
        _write(self.root, "app/health/route.ts", "export function GET() {}\n")
        _write(self.root, "vendor/app/health/route.ts", "export function GET() {}\n")
        result = extract(self.root, {"exclude": ["vendor/**"]}, {})
        self.assertEqual(
            {node["props"]["file"] for node in _routes(result).values()},
            {"app/health/route.ts"},
        )


if __name__ == "__main__":
    unittest.main()
