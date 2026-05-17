"""Cross-repo resolver confidence audit (ADR 0050).

ADR 0050 §"Cross-repo resolvers": resolvers with disambiguating evidence
emit ``definite``; resolvers that guess from a name match alone emit
``speculative``. This test fixes the per-resolver classification so a
refactor cannot silently demote a definite-class resolver or, worse,
promote a speculative-class resolver to definite.

The taxonomy applied here:

* ``grpc_service_binding`` -- definite. Matches qualified service +
  exact method name; the proto file is the source-of-truth declaration.
* ``compose_topology`` -- definite. Reads explicit ``depends_on``
  declarations from a parsed compose YAML.
* ``service_graph`` -- definite. Matches host (=child name) + HTTP
  method + exact path. Three pieces of disambiguating evidence.
* ``package_import_resolver`` -- speculative. Pure name match between
  a Python ``imports_from`` entry and a sibling ``package`` node's
  ``name`` field; ambiguous package names will over-include.
* Manual override -- definite. The user explicitly declared the edge.

Each resolver's emitted edges are exercised against a small fake
graph payload that triggers a single match, then the test inspects the
``props.confidence`` value on every emitted ``CrossRepoEdge``.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Ensure weld package is importable from the repo root
_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.contract import CONFIDENCE_VALUES  # noqa: E402
from weld.cross_repo import Override, ResolverContext  # noqa: E402


def _make_context(children: dict, *, strategies: list[str], root: str = ".") -> ResolverContext:
    """Build a :class:`ResolverContext` with synthetic child graphs."""
    return ResolverContext(
        workspace_root=root,
        cross_repo_strategies=strategies,
        children=children,
        child_hashes={name: "0" * 64 for name in children},
    )


class _FakeGraph:
    """Minimal Graph stand-in exposing ``_data`` and ``dump`` + ``nodes``."""

    def __init__(self, nodes: dict, edges: list) -> None:
        self._data = {"nodes": nodes, "edges": edges}
        # service_graph reads via dump(); package_import_resolver reads
        # via .nodes; both shapes are exercised.
        self.nodes = [
            {"id": nid, **n} for nid, n in nodes.items()
        ]

    def dump(self) -> dict:
        return self._data


class GrpcServiceBindingConfidenceTest(unittest.TestCase):
    """grpc_service_binding emits ``definite``: service+method match."""

    def test_emitted_edges_carry_definite_confidence(self) -> None:
        from weld.cross_repo.grpc_service_binding import (
            GrpcServiceBindingResolver,
        )

        # Service-side child: declares Catalog.GetItem rpc.
        service_nodes = {
            "rpc:grpc:catalog.v1.catalog.getitem": {
                "type": "rpc",
                "label": "GetItem",
                "props": {
                    "source_strategy": "grpc_proto",
                    "service": "catalog.v1.Catalog",
                    "method": "GetItem",
                    "confidence": "definite",
                },
            },
        }
        service_graph = _FakeGraph(service_nodes, [])

        # Client-side child: invokes that rpc id via grpc_bindings.
        client_nodes = {
            "file:client.py": {
                "type": "file",
                "label": "client",
                "props": {"source_strategy": "tree_sitter"},
            },
        }
        client_edges = [
            {
                "from": "file:client.py",
                "to": "rpc:grpc:catalog.v1.catalog.getitem",
                "type": "invokes",
                "props": {
                    "source_strategy": "grpc_bindings",
                    "confidence": "inferred",
                },
            },
        ]
        client_graph = _FakeGraph(client_nodes, client_edges)

        ctx = _make_context(
            {"server": service_graph, "client": client_graph},
            strategies=["grpc_service_binding"],
        )
        edges = GrpcServiceBindingResolver().resolve(ctx)

        self.assertGreater(
            len(edges), 0,
            "grpc_service_binding fixture should produce at least one "
            "matched edge; check the synthetic graph wiring",
        )
        for edge in edges:
            self.assertEqual(
                edge.props.get("confidence"), "definite",
                f"grpc_service_binding edges must be definite; got "
                f"{edge.props!r}",
            )


class ComposeTopologyConfidenceTest(unittest.TestCase):
    """compose_topology emits ``definite``: parsed YAML config."""

    def test_emitted_edges_carry_definite_confidence(self) -> None:
        from weld.cross_repo.compose_topology import ComposeTopologyResolver

        # Synthesise a minimal docker-compose.yaml in a temp workspace.
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "docker-compose.yaml").write_text(
                "services:\n"
                "  service-a:\n"
                "    image: service-a\n"
                "    depends_on:\n"
                "      - service-b\n"
                "  service-b:\n"
                "    image: service-b\n"
            )
            children = {
                "service-a": _FakeGraph({}, []),
                "service-b": _FakeGraph({}, []),
            }
            ctx = _make_context(
                children, strategies=["compose_topology"], root=tmp,
            )
            edges = ComposeTopologyResolver().resolve(ctx)

        self.assertGreater(len(edges), 0)
        for edge in edges:
            self.assertEqual(
                edge.props.get("confidence"), "definite",
                f"compose_topology edges must be definite; got {edge.props!r}",
            )


class ServiceGraphConfidenceTest(unittest.TestCase):
    """service_graph emits ``definite``: host+method+path match."""

    def test_emitted_edges_carry_definite_confidence(self) -> None:
        from weld.cross_repo.service_graph import ServiceGraphResolver

        # Server child has a fastapi route GET /tokens
        server_nodes = {
            "route:GET:/tokens": {
                "type": "route",
                "label": "GET /tokens",
                "props": {
                    "source_strategy": "fastapi",
                    "confidence": "definite",
                },
            },
        }
        # Client child has an http_client call to http://server-child/tokens
        client_nodes = {
            "rpc:http:get:server-child/tokens": {
                "type": "rpc",
                "label": "GET http://server-child/tokens",
                "props": {
                    "source_strategy": "http_client",
                    "method": "GET",
                    "url": "http://server-child/tokens",
                    "confidence": "definite",
                },
            },
        }
        children = {
            "server-child": _FakeGraph(server_nodes, []),
            "client-child": _FakeGraph(client_nodes, []),
        }
        ctx = _make_context(children, strategies=["service_graph"])
        edges = ServiceGraphResolver().resolve(ctx)

        self.assertGreater(len(edges), 0)
        for edge in edges:
            self.assertEqual(
                edge.props.get("confidence"), "definite",
                f"service_graph edges must be definite; got {edge.props!r}",
            )


class PackageImportResolverConfidenceTest(unittest.TestCase):
    """package_import_resolver emits ``speculative``: pure name match."""

    def test_emitted_edges_carry_speculative_confidence(self) -> None:
        from weld.cross_repo.package_import_resolver import (
            PackageImportResolver,
        )

        # Source child has a python_module that imports 'shared_lib'.
        # Production :class:`weld.graph.Graph` stores ``imports_from`` under
        # ``props`` (see weld/strategies/python_module.py:238); the resolver
        # reads through ``_data['nodes']`` keyed by id and then drills into
        # ``props``, matching the access pattern in
        # :mod:`weld.cross_repo.grpc_service_binding`.
        source_nodes = {
            "module:source/main.py": {
                "type": "python_module",
                "label": "source/main.py",
                "props": {"imports_from": ["shared_lib"]},
            },
        }
        # Target child declares a package named 'shared_lib'. ``name``
        # also lives under ``props`` for the same reason as above.
        target_nodes = {
            "package:shared_lib": {
                "type": "package",
                "label": "shared_lib",
                "props": {"name": "shared_lib"},
            },
        }
        children = {
            "source-child": _FakeGraph(source_nodes, []),
            "target-child": _FakeGraph(target_nodes, []),
        }
        ctx = _make_context(
            children, strategies=["package_import_resolver"],
        )
        edges = PackageImportResolver().resolve(ctx)

        self.assertGreater(len(edges), 0)
        for edge in edges:
            self.assertEqual(
                edge.props.get("confidence"), "speculative",
                f"package_import_resolver edges must be speculative "
                f"(pure name match); got {edge.props!r}",
            )


class ManualOverrideConfidenceTest(unittest.TestCase):
    """Manual override edges (action=add) are ``definite`` by default.

    The user explicitly declared the edge in
    ``.weld/cross_repo_overrides.yaml`` -- that is the strongest possible
    statement of intent. ADR 0050 classifies it as definite (the
    ``manual_override`` strategy in the static defaults map).
    """

    def test_to_edge_carries_definite_confidence(self) -> None:
        override = Override(
            from_id="child-a\x1fnode-1",
            to_id="child-b\x1fnode-2",
            type="invokes",
            action="add",
            props={},
        )
        edge = override.to_edge()
        self.assertEqual(
            edge.props.get("confidence"), "definite",
            f"manual override edges must default to definite; got "
            f"{edge.props!r}",
        )

    def test_user_supplied_confidence_is_preserved(self) -> None:
        # The user is allowed to override the default explicitly. If
        # they say speculative, we honour it.
        override = Override(
            from_id="child-a\x1fnode-1",
            to_id="child-b\x1fnode-2",
            type="invokes",
            action="add",
            props={"confidence": "speculative"},
        )
        edge = override.to_edge()
        self.assertEqual(edge.props.get("confidence"), "speculative")


class AllResolversCarryValidConfidenceTest(unittest.TestCase):
    """Property test across every registered resolver.

    Iterates through the registry's known resolver names and asserts
    that, for the small fixture above, every emitted edge carries a
    confidence value drawn from CONFIDENCE_VALUES. Resolvers that
    happen to produce no edges for the fixture are exempt (a no-op
    pass is a no-op pass), but any resolver that *does* produce edges
    must produce them with a valid confidence value.
    """

    def test_no_emitted_edge_has_invalid_confidence(self) -> None:
        from weld.cross_repo import resolver_names, get_resolver

        # Build a minimal context that exercises every resolver path.
        # The grpc + service_graph resolvers will see no match here;
        # that is fine -- they pass the test by emitting nothing.
        ctx = _make_context(
            {
                "child-x": _FakeGraph({}, []),
                "child-y": _FakeGraph({}, []),
            },
            strategies=resolver_names(),
        )
        for name in resolver_names():
            cls = get_resolver(name)
            resolver = cls()
            edges = resolver.resolve(ctx)
            for edge in edges:
                conf = edge.props.get("confidence")
                self.assertIn(
                    conf, CONFIDENCE_VALUES,
                    f"resolver {name!r} emitted edge with invalid "
                    f"confidence={conf!r}: {edge.to_dict()}",
                )


if __name__ == "__main__":
    unittest.main()
