"""Integration tests: cross-repo edge assertions for polyrepo workspaces.

Registers four test-local resolvers (service_graph, compose_topology,
grpc_service_binding, package_import) against synthetic child graphs with
matching node pairs.  Asserts each resolver emits at least one cross_repo:*
edge, edges use canonical federation IDs, two runs are deterministic, and
removing a matching node eliminates only the affected edge.
"""

from __future__ import annotations

import json
import unittest

from weld.cross_repo.base import (
    CrossRepoEdge,
    CrossRepoResolver,
    ResolverContext,
    register_resolver,
    run_resolvers,
)
from weld.workspace import UNIT_SEPARATOR

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _Graph:
    """Minimal graph stub exposing nodes keyed by ID."""
    def __init__(self, nodes: dict[str, dict]) -> None:
        self._nodes = dict(nodes)
    def dump(self) -> dict:
        return {"nodes": dict(self._nodes), "edges": []}
    def get_node(self, node_id: str) -> dict | None:
        return self._nodes.get(node_id)
    def nodes(self) -> dict[str, dict]:
        return dict(self._nodes)

def _bytes(nodes: dict[str, dict]) -> bytes:
    return json.dumps({"nodes": nodes, "edges": []}, sort_keys=True).encode()

def _pref(child: str, nid: str) -> str:
    return f"{child}{UNIT_SEPARATOR}{nid}"

def _ctx(strategies: list[str], children: dict[str, tuple[_Graph, bytes]]) -> ResolverContext:
    return ResolverContext(
        workspace_root="/tmp/polyrepo-test",
        cross_repo_strategies=strategies,
        children={n: g for n, (g, _) in children.items()},
        child_hashes={n: ResolverContext.hash_bytes(b) for n, (_, b) in children.items()},
    )

# ---------------------------------------------------------------------------
# Test-local resolvers (cleaned up in tearDownClass)
# ---------------------------------------------------------------------------
_REG: list[str] = []

def _reg(name: str):
    def _w(cls: type[CrossRepoResolver]) -> type[CrossRepoResolver]:
        out = register_resolver(name)(cls)
        _REG.append(name)
        return out
    return _w

def _scan(ctx: ResolverContext, ntype: str, prop: str) -> dict[str, tuple[str, str]]:
    """Index nodes of *ntype* by *prop* across all children."""
    idx: dict[str, tuple[str, str]] = {}
    for child, graph in sorted(ctx.children.items()):
        for nid, node in sorted(graph.nodes().items()):
            if node.get("type") == ntype:
                idx[node.get("props", {}).get(prop, "")] = (child, nid)
    return idx

def _match(ctx: ResolverContext, src_type: str, src_prop: str,
           idx: dict[str, tuple[str, str]], etype: str, resolver: str,
           prop_key: str) -> list[CrossRepoEdge]:
    edges: list[CrossRepoEdge] = []
    for child, graph in sorted(ctx.children.items()):
        for nid, node in sorted(graph.nodes().items()):
            if node.get("type") != src_type:
                continue
            key = node.get("props", {}).get(src_prop, "")
            if key in idx:
                tgt_child, tgt_nid = idx[key]
                if tgt_child != child:
                    edges.append(CrossRepoEdge(
                        from_id=_pref(child, nid), to_id=_pref(tgt_child, tgt_nid),
                        type=etype, props={"resolver": resolver, prop_key: key},
                    ))
    return edges

@_reg("test_service_graph")
class _SvcGraph(CrossRepoResolver):
    name = "test_service_graph"
    def resolve(self, ctx: ResolverContext) -> list[CrossRepoEdge]:
        return _match(ctx, "http_client", "target_path",
                      _scan(ctx, "fastapi", "path"),
                      "cross_repo:invokes", "service_graph", "path")

@_reg("test_compose_topology")
class _Compose(CrossRepoResolver):
    name = "test_compose_topology"
    def resolve(self, ctx: ResolverContext) -> list[CrossRepoEdge]:
        edges: list[CrossRepoEdge] = []
        net_svc: dict[str, list[tuple[str, str]]] = {}
        for child, graph in sorted(ctx.children.items()):
            for nid, node in sorted(graph.nodes().items()):
                if node.get("type") == "compose_service":
                    for net in node.get("props", {}).get("networks", []):
                        net_svc.setdefault(net, []).append((child, nid))
        for net, members in sorted(net_svc.items()):
            for i, (ca, na) in enumerate(members):
                for cb, nb in members[i + 1:]:
                    if ca != cb:
                        pair = sorted([_pref(ca, na), _pref(cb, nb)])
                        edges.append(CrossRepoEdge(
                            from_id=pair[0], to_id=pair[1],
                            type="cross_repo:shares_network",
                            props={"resolver": "compose_topology", "network": net},
                        ))
        return edges

@_reg("test_grpc_service_binding")
class _Grpc(CrossRepoResolver):
    name = "test_grpc_service_binding"
    def resolve(self, ctx: ResolverContext) -> list[CrossRepoEdge]:
        return _match(ctx, "grpc_client", "service_name",
                      _scan(ctx, "grpc_proto", "service_name"),
                      "cross_repo:binds", "grpc_service_binding", "service")

@_reg("test_package_import")
class _PkgImport(CrossRepoResolver):
    name = "test_package_import"
    def resolve(self, ctx: ResolverContext) -> list[CrossRepoEdge]:
        return _match(ctx, "import", "package_name",
                      _scan(ctx, "package_export", "package_name"),
                      "cross_repo:depends_on", "package_import", "package")

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
_API_NODES: dict[str, dict] = {
    "func:fetch_tokens": {"type": "http_client", "label": "fetch_tokens",
        "props": {"target_path": "/tokens", "method": "POST",
                  "host": "services-auth", "port": 8080}},
    "stub:auth_grpc": {"type": "grpc_client", "label": "AuthServiceStub",
        "props": {"service_name": "auth.AuthService", "channel": "services-auth:50051"}},
    "import:shared_models": {"type": "import", "label": "shared_models",
        "props": {"package_name": "shared-models", "version": ">=1.0"}},
    "compose:api-svc": {"type": "compose_service", "label": "api-svc",
        "props": {"image": "api:latest", "networks": ["backend"], "ports": ["8080:8080"]}},
}
_AUTH_NODES: dict[str, dict] = {
    "endpoint:post_tokens": {"type": "fastapi", "label": "POST /tokens",
        "props": {"path": "/tokens", "method": "POST", "handler": "create_token"}},
    "proto:auth_service": {"type": "grpc_proto", "label": "AuthService",
        "props": {"service_name": "auth.AuthService", "file": "auth.proto"}},
    "export:shared_models": {"type": "package_export", "label": "shared-models",
        "props": {"package_name": "shared-models", "version": "1.2.0"}},
    "compose:auth-svc": {"type": "compose_service", "label": "auth-svc",
        "props": {"image": "auth:latest", "networks": ["backend"], "ports": ["8081:8081"]}},
}

_STRATS = ["test_service_graph", "test_compose_topology",
           "test_grpc_service_binding", "test_package_import"]

# Resolver name -> node key that, when removed from auth, kills that resolver's edge.
_REMOVAL_MAP: dict[str, str] = {
    "service_graph": "endpoint:post_tokens",
    "grpc_service_binding": "proto:auth_service",
    "compose_topology": "compose:auth-svc",
    "package_import": "export:shared_models",
}
# Resolver name -> provenance prop key that must appear on its edges.
_PROP_MAP: dict[str, str] = {
    "service_graph": "path", "compose_topology": "network",
    "grpc_service_binding": "service", "package_import": "package",
}

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class CrossRepoEdgeIntegrationTests(unittest.TestCase):
    """Cross-repo edge assertions with synthetic polyrepo fixtures."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._api = (_Graph(_API_NODES), _bytes(_API_NODES))
        cls._auth = (_Graph(_AUTH_NODES), _bytes(_AUTH_NODES))

    @classmethod
    def tearDownClass(cls) -> None:
        from weld.cross_repo import base as bmod
        for n in _REG:
            bmod._REGISTRY.pop(n, None)
        _REG.clear()

    def _full(self) -> list[CrossRepoEdge]:
        return run_resolvers(_ctx(_STRATS, {"services-api": self._api, "services-auth": self._auth}))

    def _by_resolver(self, edges: list[CrossRepoEdge], name: str) -> list[CrossRepoEdge]:
        return [e for e in edges if e.props.get("resolver") == name]

    # -- each resolver emits at least one cross_repo:* edge --

    def test_each_resolver_emits_at_least_one_edge(self) -> None:
        edges = self._full()
        for resolver in ("service_graph", "compose_topology",
                         "grpc_service_binding", "package_import"):
            subset = self._by_resolver(edges, resolver)
            self.assertGreaterEqual(len(subset), 1, f"{resolver} must emit >= 1 edge")
            for e in subset:
                self.assertTrue(e.type.startswith("cross_repo:"), e.type)

    def test_all_four_resolvers_contribute(self) -> None:
        seen = {e.props.get("resolver") for e in self._full()}
        expected = {"service_graph", "compose_topology", "grpc_service_binding", "package_import"}
        self.assertEqual(seen & expected, expected,
                         f"missing resolvers: {expected - seen}")

    # -- canonical federation IDs --

    def test_edge_ids_use_federation_format(self) -> None:
        for edge in self._full():
            self.assertIn(UNIT_SEPARATOR, edge.from_id)
            self.assertIn(UNIT_SEPARATOR, edge.to_id)

    def test_edges_reference_known_children(self) -> None:
        known = {"services-api", "services-auth"}
        for edge in self._full():
            self.assertIn(edge.from_id.split(UNIT_SEPARATOR)[0], known)
            self.assertIn(edge.to_id.split(UNIT_SEPARATOR)[0], known)

    def test_edges_span_different_children(self) -> None:
        for edge in self._full():
            self.assertNotEqual(edge.from_id.split(UNIT_SEPARATOR)[0],
                                edge.to_id.split(UNIT_SEPARATOR)[0])

    # -- two-run determinism --

    def test_two_runs_identical(self) -> None:
        a = json.dumps([e.to_dict() for e in self._full()], sort_keys=True)
        b = json.dumps([e.to_dict() for e in self._full()], sort_keys=True)
        self.assertEqual(a, b)

    # -- removing a matching node eliminates only that resolver's edge --

    def test_removal_eliminates_only_affected_edge(self) -> None:
        for resolver, node_key in _REMOVAL_MAP.items():
            with self.subTest(resolver=resolver):
                auth = dict(_AUTH_NODES)
                auth.pop(node_key)
                g, b = _Graph(auth), _bytes(auth)
                edges = run_resolvers(
                    _ctx(_STRATS, {"services-api": self._api, "services-auth": (g, b)}))
                self.assertEqual(len(self._by_resolver(edges, resolver)), 0,
                                 f"{resolver} edge must vanish when {node_key} removed")
                others = {e.props.get("resolver") for e in edges} - {resolver}
                remaining = set(_REMOVAL_MAP) - {resolver}
                self.assertTrue(remaining <= others,
                                f"other resolvers must still emit edges: {remaining - others}")

    # -- adding a call produces exactly one new edge --

    def test_adding_http_client_produces_one_new_edge(self) -> None:
        baseline = len(self._by_resolver(self._full(), "service_graph"))
        extended = dict(_API_NODES)
        extended["func:fetch_users"] = {
            "type": "http_client", "label": "fetch_users",
            "props": {"target_path": "/tokens", "method": "GET",
                      "host": "services-auth", "port": 8080},
        }
        g, b = _Graph(extended), _bytes(extended)
        edges = run_resolvers(
            _ctx(_STRATS, {"services-api": (g, b), "services-auth": self._auth}))
        self.assertEqual(len(self._by_resolver(edges, "service_graph")),
                         baseline + 1)

    # -- single child produces zero edges --

    def test_single_child_produces_no_edges(self) -> None:
        edges = run_resolvers(_ctx(_STRATS, {"services-api": self._api}))
        self.assertEqual(len(edges), 0)

    # -- provenance props --

    def test_edge_props_carry_provenance(self) -> None:
        edges = self._full()
        for resolver, prop_key in _PROP_MAP.items():
            with self.subTest(resolver=resolver):
                for e in self._by_resolver(edges, resolver):
                    self.assertIn(prop_key, e.props,
                                  f"{resolver} edge must carry '{prop_key}'")


if __name__ == "__main__":
    unittest.main()
