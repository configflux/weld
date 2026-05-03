"""Fixture graphs for gRPC cross-repo resolver tests."""

from __future__ import annotations

import json

from weld._node_ids import entity_id, file_id
from weld.cross_repo.base import ResolverContext

UNIT_SEP = "\x1f"


class FakeGraph:
    """Minimal stand-in for :class:`weld.graph.Graph` with ``_data``."""

    def __init__(self, nodes: dict | None = None, edges: list | None = None) -> None:
        self._data = {
            "nodes": dict(nodes or {}),
            "edges": list(edges or []),
        }


def make_context(
    *,
    strategies: list[str] | None = None,
    children: dict[str, FakeGraph] | None = None,
) -> ResolverContext:
    """Build a ResolverContext from fake graphs."""
    children = children or {}
    raw_bytes = {
        name: json.dumps(graph._data).encode()
        for name, graph in children.items()
    }
    hashes = {
        name: ResolverContext.hash_bytes(raw)
        for name, raw in raw_bytes.items()
    }
    return ResolverContext(
        workspace_root="/tmp/workspace",
        cross_repo_strategies=list(
            strategies if strategies is not None else ["grpc_service_binding"]
        ),
        children=children,
        child_hashes=hashes,
    )


def proto_service_graph(
    service_name: str, methods: list[str], package: str = ""
) -> FakeGraph:
    """Build a fake graph with grpc_proto rpc nodes for a service.

    Mirrors :mod:`weld.strategies.grpc_proto`'s canonical-id contract
    (ADR 0041 § Layer 1): rpc / file ids route through ``entity_id`` /
    ``file_id`` so the cross-repo resolver sees the same shape as the
    live strategy.
    """
    qualified = f"{package}.{service_name}" if package else service_name
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    for method in methods:
        rpc_id = entity_id("rpc", platform="grpc", name=f"{qualified}.{method}")
        nodes[rpc_id] = {
            "type": "rpc",
            "label": f"{service_name}.{method}",
            "props": {
                "service": qualified,
                "method": method,
                "source_strategy": "grpc_proto",
                "protocol": "grpc",
            },
        }
        edges.append({
            "from": file_id(f"proto/{service_name.lower()}.proto"),
            "to": rpc_id,
            "type": "invokes",
            "props": {"source_strategy": "grpc_proto", "confidence": "definite"},
        })
    return FakeGraph(nodes=nodes, edges=edges)


def client_stub_graph(
    service_name: str, methods: list[str], package: str = "",
    source_file: str = "client.py",
) -> FakeGraph:
    """Build a fake graph with grpc_bindings invokes edges."""
    qualified = f"{package}.{service_name}" if package else service_name
    edges: list[dict] = []
    for method in methods:
        rpc_id = entity_id("rpc", platform="grpc", name=f"{qualified}.{method}")
        edges.append({
            "from": file_id(source_file),
            "to": rpc_id,
            "type": "invokes",
            "props": {"source_strategy": "grpc_bindings", "confidence": "inferred"},
        })
    return FakeGraph(edges=edges)
