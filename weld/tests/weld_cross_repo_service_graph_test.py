"""Tests for the :mod:`weld.cross_repo.service_graph` resolver.

The resolver inspects pre-loaded child graphs supplied by the framework
and emits ``cross_repo:calls`` edges from client-side HTTP call sites
(as produced by ``weld/strategies/http_client.py``) to inbound HTTP
endpoints in sibling children (as produced by the ``fastapi`` and
``runtime_contract`` strategies). All matching is static: a URL of the
form ``http://<sibling-name>[:<port>]/<path>`` is matched to any sibling
whose federated child name equals ``<sibling-name>``.

Fake child graphs are passed in via a ``ResolverContext`` and the
resolver's output is asserted directly -- no filesystem access required.
"""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr

from weld.cross_repo import (
    CrossRepoEdge,
    ResolverContext,
    get_resolver,
    resolver_names,
    run_resolvers,
)
from weld.cross_repo.service_graph import ServiceGraphResolver
from weld.workspace import UNIT_SEPARATOR


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _FakeGraph:
    """Minimal stand-in for :class:`weld.graph.Graph`.

    The resolver only calls :meth:`dump`, which returns a mapping with
    ``nodes`` and ``edges`` keys mirroring the real Graph payload.
    """

    def __init__(self, data: dict) -> None:
        self._data = data

    def dump(self) -> dict:
        return dict(self._data)


def _client_node(*, method: str, url: str) -> tuple[str, dict]:
    """Build the (id, node) pair weld's http_client strategy emits."""
    nid = f"rpc:http:out:{method}:{url}"
    return nid, {
        "type": "rpc",
        "label": f"{method} {url}",
        "props": {
            "method": method,
            "url": url,
            "source_strategy": "http_client",
            "protocol": "http",
            "surface_kind": "request_response",
            "transport": "http",
            "boundary_kind": "outbound",
        },
    }


def _route_node(*, method: str, path: str) -> tuple[str, dict]:
    """Build the (id, node) pair weld's fastapi strategy emits."""
    nid = f"route:{method}:{path}"
    return nid, {
        "type": "route",
        "label": f"{method} {path}",
        "props": {
            "method": method,
            "path": path,
            "source_strategy": "fastapi",
            "protocol": "http",
            "surface_kind": "request_response",
            "transport": "http",
            "boundary_kind": "inbound",
        },
    }


def _contract_node(*, method: str, path: str) -> tuple[str, dict]:
    """Build the (id, node) pair weld's runtime_contract strategy emits."""
    slug = path.strip("/").replace("/", "-") or "root"
    nid = f"rpc:runtime-contract/{method.lower()}-{slug}"
    return nid, {
        "type": "rpc",
        "label": f"{method} {path}",
        "props": {
            "source_strategy": "runtime_contract",
            "protocol": "http",
            "surface_kind": "request_response",
            "transport": "http",
            "boundary_kind": "inbound",
        },
    }


def _mkgraph(*pairs: tuple[str, dict]) -> _FakeGraph:
    """Assemble a fake graph from (id, node) pairs."""
    return _FakeGraph({"nodes": dict(pairs), "edges": []})


def _ctx(children: dict[str, _FakeGraph]) -> ResolverContext:
    hashes = {
        name: ResolverContext.hash_bytes(
            repr(sorted(graph.dump().items())).encode("utf-8")
        )
        for name, graph in children.items()
    }
    return ResolverContext(
        workspace_root="/tmp/workspace",
        cross_repo_strategies=["service_graph"],
        children=children,
        child_hashes=hashes,
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class RegistrationTests(unittest.TestCase):
    """The resolver class is reachable through the framework registry."""

    def test_service_graph_is_registered(self) -> None:
        self.assertIn("service_graph", resolver_names())

    def test_registered_class_is_service_graph_resolver(self) -> None:
        self.assertIs(get_resolver("service_graph"), ServiceGraphResolver)

    def test_resolver_declares_matching_name(self) -> None:
        self.assertEqual(ServiceGraphResolver.name, "service_graph")


# ---------------------------------------------------------------------------
# Matching logic
# ---------------------------------------------------------------------------


class ServiceGraphMatchingTests(unittest.TestCase):
    """Positive matches between http_client callers and server endpoints."""

    def test_matches_fastapi_route_cross_repo(self) -> None:
        cid, cnode = _client_node(
            method="GET", url="http://services-auth:8080/tokens"
        )
        rid, rnode = _route_node(method="GET", path="/tokens")
        ctx = _ctx({
            "services-api": _mkgraph((cid, cnode)),
            "services-auth": _mkgraph((rid, rnode)),
        })

        edges = ServiceGraphResolver().resolve(ctx)

        self.assertEqual(len(edges), 1)
        edge = edges[0]
        self.assertEqual(edge.from_id, f"services-api{UNIT_SEPARATOR}{cid}")
        self.assertEqual(edge.to_id, f"services-auth{UNIT_SEPARATOR}{rid}")
        self.assertEqual(edge.type, "cross_repo:calls")
        self.assertEqual(edge.props["method"], "GET")
        self.assertEqual(edge.props["path"], "/tokens")
        self.assertEqual(edge.props["host"], "services-auth")
        self.assertEqual(edge.props["port"], 8080)
        self.assertEqual(edge.props["source_strategy"], "service_graph")

    def test_matches_runtime_contract_endpoint_cross_repo(self) -> None:
        cid, cnode = _client_node(
            method="POST", url="http://services-auth/v1/tokens"
        )
        eid, enode = _contract_node(method="POST", path="/v1/tokens")
        ctx = _ctx({
            "services-api": _mkgraph((cid, cnode)),
            "services-auth": _mkgraph((eid, enode)),
        })

        edges = ServiceGraphResolver().resolve(ctx)

        self.assertEqual(len(edges), 1)
        edge = edges[0]
        self.assertEqual(edge.to_id, f"services-auth{UNIT_SEPARATOR}{eid}")
        self.assertEqual(edge.type, "cross_repo:calls")
        self.assertEqual(edge.props["method"], "POST")
        self.assertEqual(edge.props["path"], "/v1/tokens")
        self.assertEqual(edge.props["host"], "services-auth")
        self.assertIsNone(edge.props.get("port"))

    def test_host_matching_is_case_insensitive(self) -> None:
        # RFC 3986: hostnames are case-insensitive. urlsplit lower-cases
        # them but workspaces.yaml allows mixed-case child names.
        cid, cnode = _client_node(
            method="GET", url="http://Services-Auth/users"
        )
        rid, rnode = _route_node(method="GET", path="/users")
        ctx = _ctx({
            "services-api": _mkgraph((cid, cnode)),
            "services-auth": _mkgraph((rid, rnode)),
        })

        edges = ServiceGraphResolver().resolve(ctx)

        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0].to_id, f"services-auth{UNIT_SEPARATOR}{rid}")

    def test_https_scheme_is_also_supported(self) -> None:
        cid, cnode = _client_node(
            method="GET", url="https://services-auth/users"
        )
        rid, rnode = _route_node(method="GET", path="/users")
        ctx = _ctx({
            "services-api": _mkgraph((cid, cnode)),
            "services-auth": _mkgraph((rid, rnode)),
        })

        edges = ServiceGraphResolver().resolve(ctx)

        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0].props["host"], "services-auth")


class ServiceGraphNoMatchTests(unittest.TestCase):
    """Cases where the resolver must emit no edge and no warning."""

    def test_unmatched_path_emits_nothing_and_is_silent(self) -> None:
        cid, cnode = _client_node(
            method="GET", url="http://services-auth:8080/nonexistent"
        )
        rid, rnode = _route_node(method="GET", path="/tokens")
        ctx = _ctx({
            "services-api": _mkgraph((cid, cnode)),
            "services-auth": _mkgraph((rid, rnode)),
        })

        buffer = io.StringIO()
        with redirect_stderr(buffer):
            edges = ServiceGraphResolver().resolve(ctx)

        self.assertEqual(edges, [])
        self.assertEqual(buffer.getvalue(), "")

    def test_host_not_a_sibling_emits_nothing(self) -> None:
        cid, cnode = _client_node(
            method="GET", url="http://external-service/users"
        )
        rid, rnode = _route_node(method="GET", path="/users")
        ctx = _ctx({
            "services-api": _mkgraph((cid, cnode)),
            "services-auth": _mkgraph((rid, rnode)),
        })

        self.assertEqual(ServiceGraphResolver().resolve(ctx), [])

    def test_path_only_url_is_ignored(self) -> None:
        # Path-only URLs are same-repo calls handled by the dangling-edge
        # sweep; the resolver must never synthesize a cross-repo edge.
        cid, cnode = _client_node(method="GET", url="/tokens")
        rid, rnode = _route_node(method="GET", path="/tokens")
        ctx = _ctx({
            "services-api": _mkgraph((cid, cnode)),
            "services-auth": _mkgraph((rid, rnode)),
        })

        self.assertEqual(ServiceGraphResolver().resolve(ctx), [])

    def test_client_calling_its_own_repo_is_not_cross_repo(self) -> None:
        cid, cnode = _client_node(
            method="GET", url="http://services-api/internal"
        )
        rid, rnode = _route_node(method="GET", path="/internal")
        ctx = _ctx({
            "services-api": _mkgraph((cid, cnode), (rid, rnode)),
        })

        self.assertEqual(ServiceGraphResolver().resolve(ctx), [])

    def test_method_mismatch_emits_nothing(self) -> None:
        cid, cnode = _client_node(method="POST", url="http://services-auth/users")
        rid, rnode = _route_node(method="GET", path="/users")
        ctx = _ctx({
            "services-api": _mkgraph((cid, cnode)),
            "services-auth": _mkgraph((rid, rnode)),
        })

        self.assertEqual(ServiceGraphResolver().resolve(ctx), [])


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class ServiceGraphDeterminismTests(unittest.TestCase):
    """Output must be byte-identical across resolves for identical inputs."""

    def test_two_resolves_produce_equal_edges(self) -> None:
        c1, cn1 = _client_node(method="GET", url="http://services-auth:8080/tokens")
        c2, cn2 = _client_node(method="POST", url="http://services-billing/charges")
        r1, rn1 = _route_node(method="GET", path="/tokens")
        r2, rn2 = _route_node(method="POST", path="/charges")
        ctx = _ctx({
            "services-api": _mkgraph((c1, cn1), (c2, cn2)),
            "services-auth": _mkgraph((r1, rn1)),
            "services-billing": _mkgraph((r2, rn2)),
        })

        first = [e.to_dict() for e in ServiceGraphResolver().resolve(ctx)]
        second = [e.to_dict() for e in ServiceGraphResolver().resolve(ctx)]
        self.assertEqual(first, second)
        self.assertEqual(len(first), 2)

    def test_output_is_sorted_regardless_of_child_iteration(self) -> None:
        # Insertion order of the children mapping must not affect output.
        cid, cnode = _client_node(method="GET", url="http://services-auth/users")
        rid, rnode = _route_node(method="GET", path="/users")
        ordered = _ctx({
            "services-api": _mkgraph((cid, cnode)),
            "services-auth": _mkgraph((rid, rnode)),
        })
        reversed_order = _ctx({
            "services-auth": _mkgraph((rid, rnode)),
            "services-api": _mkgraph((cid, cnode)),
        })
        first = [e.to_dict() for e in ServiceGraphResolver().resolve(ordered)]
        second = [
            e.to_dict() for e in ServiceGraphResolver().resolve(reversed_order)
        ]
        self.assertEqual(first, second)


# ---------------------------------------------------------------------------
# Side-effect safety
# ---------------------------------------------------------------------------


class ServiceGraphReadOnlyTests(unittest.TestCase):
    """The resolver must treat child graphs as read-only."""

    def test_child_graph_dump_is_unchanged_after_resolve(self) -> None:
        cid, cnode = _client_node(
            method="GET", url="http://services-auth:8080/tokens"
        )
        rid, rnode = _route_node(method="GET", path="/tokens")
        api_graph = _mkgraph((cid, cnode))
        auth_graph = _mkgraph((rid, rnode))
        before_api = api_graph.dump()
        before_auth = auth_graph.dump()
        ctx = _ctx({"services-api": api_graph, "services-auth": auth_graph})

        ServiceGraphResolver().resolve(ctx)

        self.assertEqual(api_graph.dump(), before_api)
        self.assertEqual(auth_graph.dump(), before_auth)


# ---------------------------------------------------------------------------
# End-to-end via the orchestrator
# ---------------------------------------------------------------------------


class ServiceGraphOrchestratorTests(unittest.TestCase):
    """Running through :func:`run_resolvers` gives the same output."""

    def test_run_resolvers_invokes_service_graph(self) -> None:
        cid, cnode = _client_node(method="GET", url="http://services-auth/users")
        rid, rnode = _route_node(method="GET", path="/users")
        ctx = _ctx({
            "services-api": _mkgraph((cid, cnode)),
            "services-auth": _mkgraph((rid, rnode)),
        })

        edges = run_resolvers(ctx)

        self.assertEqual(len(edges), 1)
        self.assertIsInstance(edges[0], CrossRepoEdge)
        self.assertEqual(edges[0].type, "cross_repo:calls")


if __name__ == "__main__":
    unittest.main()
