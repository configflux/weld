"""Strategy: static gRPC server and client bindings (tracked project).

Links Python server implementations and client stub call sites back
to the ``rpc:grpc:<package>.<Service>.<Method>`` nodes emitted by the
``grpc_proto`` strategy. Per ADR 0018's static-truth policy, detection
is purely structural: no runtime, no data-flow, no stub execution.

Supported shapes:

- Server binding: a class whose base list contains a ``*Servicer``
  identifier imported from a ``*_pb2_grpc`` module. The class name's
  ``*Servicer`` suffix maps to a simple service name that is resolved
  via the proto index to a qualified service. For every method on the
  class whose name matches a declared rpc of that service, an
  ``implements`` edge is emitted from the class symbol to the rpc
  node, plus an ``invokes`` edge from the declaring file.
- Client binding: ``stub = <pb2_grpc>.<FooServiceStub>(...)`` followed
  by ``stub.Method(...)`` in the same function scope, where ``Method``
  is a declared rpc of the resolved service. Emits an ``invokes`` edge
  from the declaring file to the rpc node.

All edges carry ``source_strategy="grpc_bindings"`` and
``confidence="inferred"`` because the class/stub-name-to-service
mapping is a codegen convention rather than a direct proto
cross-reference. Edges intentionally dangle against declared rpc ids;
discovery's dangling-edge sweep resolves or drops them depending on
whether the ``grpc_proto`` fragment is present in the graph.

Out of scope (ADR 0018): following stub instances through ``self``
assignments or across functions, generated ``*_pb2_grpc.py`` parsing
(proto text is authoritative), and any shape that requires data flow.
"""

from __future__ import annotations

import ast
from pathlib import Path

from weld.strategies._helpers import StrategyResult, filter_glob_results
from weld.strategies.grpc_proto_parser import parse_proto_text

_SERVICER_SUFFIX = "Servicer"
_STUB_SUFFIX = "Stub"
_PB2_GRPC_SUFFIX = "_pb2_grpc"

# ---------------------------------------------------------------------------
# Proto index
# ---------------------------------------------------------------------------

class ProtoIndex:
    """Map simple service names to qualified ids and declared methods.

    Built once per ``extract()`` by scanning every ``.proto`` file
    under ``proto_glob``. ``qualified_service`` returns the dotted
    ``<package>.<Service>`` for a bare simple name, or ``None`` when
    unknown or ambiguous (declared in more than one package --
    ambiguity is a silent drop per ADR 0018). ``methods`` returns the
    declared rpc method names for a qualified service.
    """

    def __init__(self) -> None:
        self._simple_to_qualified: dict[str, list[str]] = {}
        self._methods: dict[str, frozenset[str]] = {}

    def ingest_proto(self, text: str) -> None:
        """Merge a parsed proto file into the index."""
        pf = parse_proto_text(text)
        package = pf.package
        for service in pf.services:
            qualified = (
                f"{package}.{service.name}" if package else service.name
            )
            self._simple_to_qualified.setdefault(service.name, []).append(
                qualified
            )
            self._methods[qualified] = frozenset(
                rpc.name for rpc in service.rpcs
            )

    def qualified_service(self, simple: str) -> str | None:
        candidates = self._simple_to_qualified.get(simple)
        if not candidates:
            return None
        # Ambiguous: the simple name exists in multiple packages. Drop
        # the binding rather than guess which one the Python code meant.
        if len({c for c in candidates}) > 1:
            return None
        return candidates[0]

    def methods(self, qualified: str) -> frozenset[str]:
        return self._methods.get(qualified, frozenset())

def _build_proto_index(root: Path, pattern: str) -> ProtoIndex:
    """Scan ``root`` under ``pattern`` and build a ``ProtoIndex``."""
    index = ProtoIndex()
    for proto_path in filter_glob_results(root, sorted(root.glob(pattern))):
        if not proto_path.is_file() or proto_path.suffix != ".proto":
            continue
        try:
            text = proto_path.read_text(encoding="utf-8")
        except OSError:
            continue
        index.ingest_proto(text)
    return index

# ---------------------------------------------------------------------------
# Python-side detection
# ---------------------------------------------------------------------------

def _collect_pb2_grpc_imports(tree: ast.Module) -> dict[str, str]:
    """Return a map of local-name -> ``*_pb2_grpc`` module basename.

    Handles ``import pkg.foo_pb2_grpc [as x]``,
    ``from pkg import foo_pb2_grpc [as x]``, and
    ``from pkg.foo_pb2_grpc import FooServiceStub, FooServiceServicer``.
    Modules whose basename does not end in ``_pb2_grpc`` are ignored.
    """
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                last = alias.name.split(".")[-1]
                if last.endswith(_PB2_GRPC_SUFFIX):
                    bound = alias.asname or last
                    out[bound] = last
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            last = module.split(".")[-1]
            if last.endswith(_PB2_GRPC_SUFFIX):
                # ``from pkg.foo_pb2_grpc import X`` -- every imported
                # name is known to live under ``foo_pb2_grpc``.
                for alias in node.names:
                    bound = alias.asname or alias.name
                    out[bound] = last
            else:
                # ``from pkg.v1 import foo_pb2_grpc`` -- the imported
                # name itself is the module.
                for alias in node.names:
                    if alias.name.endswith(_PB2_GRPC_SUFFIX):
                        bound = alias.asname or alias.name
                        out[bound] = alias.name
    return out

def _file_has_pb2_grpc_import(tree: ast.Module) -> bool:
    """Cheap pre-filter: only walk files that reference ``*_pb2_grpc``."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[-1].endswith(_PB2_GRPC_SUFFIX):
                    return True
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.split(".")[-1].endswith(_PB2_GRPC_SUFFIX):
                return True
            for alias in node.names:
                if alias.name.endswith(_PB2_GRPC_SUFFIX):
                    return True
    return False

def _servicer_base_name(
    cls: ast.ClassDef, pb2_grpc: dict[str, str]
) -> str | None:
    """Return the simple service name when *cls* binds to a Servicer.

    Accepts ``class Foo(module.FooServiceServicer):`` and
    ``class Foo(FooServiceServicer):`` shapes where the qualifying
    or bare base name is known to come from a ``*_pb2_grpc`` module.
    """
    for base in cls.bases:
        attr_name: str | None = None
        if isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name):
            # ``catalog_pb2_grpc.FooServiceServicer``
            if base.value.id in pb2_grpc and base.attr.endswith(_SERVICER_SUFFIX):
                attr_name = base.attr
        elif isinstance(base, ast.Name):
            # ``FooServiceServicer`` imported directly.
            if base.id in pb2_grpc and base.id.endswith(_SERVICER_SUFFIX):
                attr_name = base.id
        if attr_name is not None:
            simple = attr_name[: -len(_SERVICER_SUFFIX)]
            if simple:
                return simple
    return None

def _stub_ctor_service(
    call: ast.Call, pb2_grpc: dict[str, str]
) -> str | None:
    """Return the simple service name if *call* constructs a ``*Stub``."""
    func = call.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        if func.value.id in pb2_grpc and func.attr.endswith(_STUB_SUFFIX):
            simple = func.attr[: -len(_STUB_SUFFIX)]
            return simple or None
    if isinstance(func, ast.Name):
        if func.id in pb2_grpc and func.id.endswith(_STUB_SUFFIX):
            simple = func.id[: -len(_STUB_SUFFIX)]
            return simple or None
    return None

def _stub_vars_in_function(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    pb2_grpc: dict[str, str],
) -> dict[str, str]:
    """Return ``{stub_var_name: simple_service_name}`` for *func*.

    Only ``Name = Call(...)`` assignments are recognised; tuple
    unpacking and attribute targets are out of scope per ADR 0018.
    """
    out: dict[str, str] = {}
    for node in ast.walk(func):
        if not isinstance(node, ast.Assign):
            continue
        if not (len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)):
            continue
        if not isinstance(node.value, ast.Call):
            continue
        simple = _stub_ctor_service(node.value, pb2_grpc)
        if simple is not None:
            out[node.targets[0].id] = simple
    return out

def _collect_stub_calls(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    stubs: dict[str, str],
) -> list[tuple[str, str]]:
    """Return ``(simple_service, method)`` pairs for each stub call.

    Only ``<stub_var>.<Method>(...)`` shapes are recognised.
    """
    found: list[tuple[str, str]] = []
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        func_node = node.func
        if not isinstance(func_node, ast.Attribute):
            continue
        if not isinstance(func_node.value, ast.Name):
            continue
        var = func_node.value.id
        if var not in stubs:
            continue
        found.append((stubs[var], func_node.attr))
    return found

# ---------------------------------------------------------------------------
# Edge helpers
# ---------------------------------------------------------------------------

def _rpc_id(qualified_service: str, method: str) -> str:
    return f"rpc:grpc:{qualified_service}.{method}"

def _edge(src: str, dst: str, etype: str) -> dict:
    return {
        "from": src,
        "to": dst,
        "type": etype,
        "props": {
            "source_strategy": "grpc_bindings",
            "confidence": "inferred",
        },
    }

# ---------------------------------------------------------------------------
# Per-file processing
# ---------------------------------------------------------------------------

def _process_file(
    tree: ast.Module,
    rel_path: str,
    index: ProtoIndex,
    edges: list[dict],
) -> bool:
    """Process one parsed Python module. Returns True if any edge was emitted."""
    pb2_grpc = _collect_pb2_grpc_imports(tree)
    if not pb2_grpc:
        return False
    file_id = f"file:{rel_path}"
    emitted = False

    # Server bindings: Servicer subclasses.
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            simple = _servicer_base_name(node, pb2_grpc)
            if simple is None:
                continue
            qualified = index.qualified_service(simple)
            if qualified is None:
                continue
            declared_methods = index.methods(qualified)
            if not declared_methods:
                continue
            class_symbol = f"symbol:{rel_path}:{node.name}"
            for body_item in node.body:
                if not isinstance(
                    body_item, (ast.FunctionDef, ast.AsyncFunctionDef)
                ):
                    continue
                method_name = body_item.name
                if method_name not in declared_methods:
                    continue
                rpc_id = _rpc_id(qualified, method_name)
                edges.append(
                    _edge(
                        f"{class_symbol}.{method_name}", rpc_id, "implements"
                    )
                )
                edges.append(_edge(file_id, rpc_id, "invokes"))
                emitted = True

    # Client bindings: stub call sites inside function bodies.
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        stubs = _stub_vars_in_function(node, pb2_grpc)
        if not stubs:
            continue
        for simple, method in _collect_stub_calls(node, stubs):
            qualified = index.qualified_service(simple)
            if qualified is None:
                continue
            if method not in index.methods(qualified):
                continue
            rpc_id = _rpc_id(qualified, method)
            edges.append(_edge(file_id, rpc_id, "invokes"))
            emitted = True

    return emitted

# ---------------------------------------------------------------------------
# Strategy entry point
# ---------------------------------------------------------------------------

def _iter_python_sources(root: Path, pattern: str) -> list[Path]:
    matches = sorted(root.glob(pattern))
    return filter_glob_results(root, matches)

def extract(root: Path, source: dict, context: dict) -> StrategyResult:
    """Link Python gRPC server/client code to declared rpc surfaces."""
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    discovered_from: list[str] = []

    py_pattern = source.get("glob")
    if not py_pattern:
        return StrategyResult(nodes, edges, discovered_from)

    proto_pattern = source.get("proto_glob", "proto/**/*.proto")
    index = _build_proto_index(root, proto_pattern)

    for py in _iter_python_sources(root, py_pattern):
        if not py.is_file() or py.suffix != ".py":
            continue
        if py.name.startswith("_"):
            continue
        try:
            text = py.read_text(encoding="utf-8")
        except OSError:
            continue
        try:
            tree = ast.parse(text, filename=str(py))
        except SyntaxError:
            continue
        if not _file_has_pb2_grpc_import(tree):
            continue
        rel_path = str(py.relative_to(root))
        if _process_file(tree, rel_path, index, edges):
            discovered_from.append(rel_path)

    return StrategyResult(nodes, edges, discovered_from)
