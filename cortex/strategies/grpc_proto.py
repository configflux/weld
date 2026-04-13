"""Strategy: proto service, rpc, message, and enum extraction (project-xoq.5.1).

Parses declared ``.proto`` files into cortex nodes per ADR 0018's static-truth
policy. The heavy lifting lives in :mod:`cortex.strategies.grpc_proto_parser`;
this module is the thin facade that turns a parsed ``ProtoFile`` into cortex
nodes and edges and exposes the strategy ``extract()`` entry point.

Supported shapes:

- ``package foo.bar;`` — namespace for top-level services, messages,
  and enums in the file.
- ``service Foo { rpc Bar(Req) returns (Resp); }`` — emits one
  ``rpc:grpc:<ns>.<Service>.<Method>`` node per rpc line, with the
  request and response type names recorded.
- ``message Foo { ... }`` — emits ``contract:grpc:<ns>.<Name>`` nodes
  (including nested ``Outer.Inner`` messages).
- ``enum Foo { ... }`` — emits ``enum:grpc:<ns>.<Name>`` nodes.

Emitted rpc nodes are stamped with ADR 0018 interaction metadata::

    protocol="grpc"
    surface_kind="request_response" | "stream"
    transport="http2"
    boundary_kind="inbound"
    declared_in="<rel-path>"

Every rpc is linked from its declaring ``file:<rel>`` node via an
``invokes`` edge; contract and enum nodes are linked via ``contains``
edges. Each rpc additionally carries an ``accepts`` edge to its
proto-declared request contract and a ``responds_with`` edge to its
response contract -- both stamped ``confidence="definite"`` because
the link comes from the proto text alone, with no cross-file or
runtime inference.

Out of scope (xoq.5.2 and beyond):

- Server-side registration in generated stubs or hand-written code.
- Client-side invocation sites in same-repo code.
- ``import`` resolution across proto files.
- Option parsing (``google.api.http``), custom extensions.
"""

from __future__ import annotations

from pathlib import Path

from cortex.strategies._helpers import StrategyResult, filter_glob_results
from cortex.strategies.grpc_proto_parser import ProtoFile, parse_proto_text

# ---------------------------------------------------------------------------
# Id helpers
# ---------------------------------------------------------------------------

def _qualified(package: str, name: str) -> str:
    return f"{package}.{name}" if package else name

def _rpc_id(package: str, service: str, method: str) -> str:
    return f"rpc:grpc:{_qualified(package, service)}.{method}"

def _contract_id(package: str, name: str) -> str:
    return f"contract:grpc:{_qualified(package, name)}"

def _enum_id(package: str, name: str) -> str:
    return f"enum:grpc:{_qualified(package, name)}"

def _proto_type_to_contract_id(package: str, type_name: str) -> str:
    """Map a proto type reference to a contract node id.

    Dotted references (``other.v1.Foo``) are treated as
    fully-qualified; bare names are namespaced with the declaring
    file's package. Primitives are not expected as rpc
    request/response types, but if one slips through we still emit a
    ``contract:grpc:<name>`` id and let discovery's dangling-edge
    sweep drop it when no matching node exists.
    """
    if "." in type_name:
        return f"contract:grpc:{type_name}"
    return f"contract:grpc:{_qualified(package, type_name)}"

def _edge(src: str, dst: str, etype: str, *, confidence: str) -> dict:
    return {
        "from": src,
        "to": dst,
        "type": etype,
        "props": {
            "source_strategy": "grpc_proto",
            "confidence": confidence,
        },
    }

# ---------------------------------------------------------------------------
# Fragment assembly
# ---------------------------------------------------------------------------

def _emit_services(
    pf: ProtoFile, rel_path: str, file_id: str,
    nodes: dict[str, dict], edges: list[dict],
) -> bool:
    emitted = False
    package = pf.package
    for service in pf.services:
        for rpc in service.rpcs:
            rid = _rpc_id(package, service.name, rpc.name)
            surface = (
                "stream"
                if (rpc.client_stream or rpc.server_stream)
                else "request_response"
            )
            nodes[rid] = {
                "type": "rpc",
                "label": f"{service.name}.{rpc.name}",
                "props": {
                    "service": _qualified(package, service.name),
                    "method": rpc.name,
                    "request_type": rpc.request_type,
                    "response_type": rpc.response_type,
                    "client_stream": rpc.client_stream,
                    "server_stream": rpc.server_stream,
                    "source_strategy": "grpc_proto",
                    "authority": "canonical",
                    "confidence": "definite",
                    "roles": ["implementation"],
                    # ADR 0018 interaction metadata.
                    "protocol": "grpc",
                    "surface_kind": surface,
                    "transport": "http2",
                    "boundary_kind": "inbound",
                    "declared_in": rel_path,
                },
            }
            edges.append(_edge(file_id, rid, "invokes", confidence="definite"))
            req_id = _proto_type_to_contract_id(package, rpc.request_type)
            resp_id = _proto_type_to_contract_id(package, rpc.response_type)
            edges.append(_edge(rid, req_id, "accepts", confidence="definite"))
            edges.append(
                _edge(rid, resp_id, "responds_with", confidence="definite")
            )
            emitted = True
    return emitted

def _emit_messages(
    pf: ProtoFile, rel_path: str, file_id: str,
    nodes: dict[str, dict], edges: list[dict],
) -> bool:
    emitted = False
    package = pf.package
    for msg in pf.messages:
        cid = _contract_id(package, msg.qualified_name)
        nodes[cid] = {
            "type": "contract",
            "label": msg.qualified_name,
            "props": {
                "name": msg.qualified_name,
                "fields": list(msg.fields),
                "source_strategy": "grpc_proto",
                "authority": "canonical",
                "confidence": "definite",
                "roles": ["implementation"],
                "protocol": "grpc",
                "declared_in": rel_path,
            },
        }
        edges.append(_edge(file_id, cid, "contains", confidence="definite"))
        emitted = True
    return emitted

def _emit_enums(
    pf: ProtoFile, rel_path: str, file_id: str,
    nodes: dict[str, dict], edges: list[dict],
) -> bool:
    emitted = False
    package = pf.package
    for enum in pf.enums:
        eid = _enum_id(package, enum.name)
        nodes[eid] = {
            "type": "enum",
            "label": enum.name,
            "props": {
                "name": enum.name,
                "members": list(enum.members),
                "source_strategy": "grpc_proto",
                "authority": "canonical",
                "confidence": "definite",
                "roles": ["implementation"],
                "protocol": "grpc",
                "declared_in": rel_path,
            },
        }
        edges.append(_edge(file_id, eid, "contains", confidence="definite"))
        emitted = True
    return emitted

def _build_fragment(
    pf: ProtoFile, rel_path: str, nodes: dict[str, dict], edges: list[dict]
) -> bool:
    """Turn one parsed proto file into cortex nodes/edges.

    Returns ``True`` when the file contributed at least one node,
    ``False`` when it had no recognisable declarations.
    """
    file_id = f"file:{rel_path}"
    s = _emit_services(pf, rel_path, file_id, nodes, edges)
    m = _emit_messages(pf, rel_path, file_id, nodes, edges)
    e = _emit_enums(pf, rel_path, file_id, nodes, edges)
    return s or m or e

# ---------------------------------------------------------------------------
# Strategy entry point
# ---------------------------------------------------------------------------

def _iter_sources(root: Path, pattern: str) -> list[Path]:
    matches = sorted(root.glob(pattern))
    return filter_glob_results(root, matches)

def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Extract proto declarations into rpc, contract, and enum nodes."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    pattern = source.get("glob")
    if not pattern:
        return StrategyResult(nodes, edges, discovered_from)

    for proto_path in _iter_sources(root, pattern):
        if not proto_path.is_file() or proto_path.suffix != ".proto":
            continue
        try:
            text = proto_path.read_text(encoding="utf-8")
        except OSError:
            continue
        pf = parse_proto_text(text)
        if not (pf.services or pf.messages or pf.enums):
            continue
        rel_path = str(proto_path.relative_to(root))
        if _build_fragment(pf, rel_path, nodes, edges):
            discovered_from.append(rel_path)

    return StrategyResult(nodes, edges, discovered_from)
