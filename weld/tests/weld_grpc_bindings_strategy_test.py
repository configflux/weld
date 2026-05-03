"""Tests for the static gRPC bindings strategy (tracked project).

The ``grpc_bindings`` strategy statically links Python server
implementations and client stub call sites back to rpc node ids
declared by the ``grpc_proto`` strategy. Per ADR 0018's static-truth
policy, detection is structural only:

- Server binding: a Python class that subclasses any ``*Servicer``
  symbol imported from a ``*_pb2_grpc`` module is treated as a server
  implementation. Each method it defines whose name matches an rpc
  method declared in a sibling ``.proto`` service produces an
  ``implements`` edge from the class's symbol node to the qualified
  rpc id, plus an ``invokes`` edge from the declaring file.

- Client binding: an assignment of shape ``stub = FooServiceStub(ch)``
  where ``FooServiceStub`` was imported from a ``*_pb2_grpc`` module,
  followed by ``stub.Method(...)`` calls in the same function scope,
  produces an ``invokes`` edge from the file to the qualified rpc id.

All emitted edges carry ``confidence="inferred"`` and
``source_strategy="grpc_bindings"``. Edges intentionally dangle against
declared rpc node ids -- discovery's dangling-edge sweep resolves them
when the ``grpc_proto`` fragment is also in the graph, and drops them
otherwise.
"""

from __future__ import annotations

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.contract import validate_fragment  # noqa: E402
from weld.strategies.grpc_bindings import extract  # noqa: E402

def _write(pkg: Path, name: str, body: str) -> None:
    (pkg / name).write_text(textwrap.dedent(body))

_PROTO_CATALOG = """\
    syntax = "proto3";
    package catalog.v1;
    service CatalogService {
      rpc GetProduct(GetProductRequest) returns (GetProductResponse);
      rpc ListProducts(ListProductsRequest) returns (stream Product);
    }
    message GetProductRequest { string product_id = 1; }
    message GetProductResponse { string id = 1; }
    message ListProductsRequest { int32 page_size = 1; }
    message Product { string id = 1; }
"""

def _seed_proto(root: Path) -> None:
    pkg = root / "proto" / "catalog" / "v1"
    pkg.mkdir(parents=True)
    _write(pkg, "catalog.proto", _PROTO_CATALOG)

def _run(root: Path, py_glob: str = "src/**/*.py") -> tuple[dict, list, list]:
    result = extract(
        root,
        {"glob": py_glob, "proto_glob": "proto/**/*.proto"},
        {},
    )
    return result.nodes, result.edges, list(result.discovered_from)

# ---------------------------------------------------------------------------
# Server binding tests
# ---------------------------------------------------------------------------

class GrpcServerBindingTest(unittest.TestCase):
    """Python Servicer subclasses bind methods to declared rpc ids."""

    def test_servicer_subclass_binds_declared_rpc_methods(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed_proto(root)
            pkg = root / "src" / "server"
            pkg.mkdir(parents=True)
            _write(pkg, "catalog.py", """\
                from catalog.v1 import catalog_pb2_grpc

                class CatalogServicerImpl(catalog_pb2_grpc.CatalogServiceServicer):
                    def GetProduct(self, request, context):
                        return None

                    def ListProducts(self, request, context):
                        return iter(())

                    def SomeHelper(self):
                        return 1
            """)
            nodes, edges, discovered = _run(root)
            # ADR 0041 § Layer 1: rpc ids lowercased; file ids drop ext.
            rpc_get = "rpc:grpc:catalog.v1.catalogservice.getproduct"
            rpc_list = "rpc:grpc:catalog.v1.catalogservice.listproducts"
            sym = "symbol:src/server/catalog.py:CatalogServicerImpl"
            # implements edges from the class symbol to each declared rpc.
            impls = {
                (e["from"], e["to"]) for e in edges if e["type"] == "implements"
            }
            self.assertIn((f"{sym}.GetProduct", rpc_get), impls)
            self.assertIn((f"{sym}.ListProducts", rpc_list), impls)
            # ``SomeHelper`` is not a declared rpc method -- no edge.
            self.assertFalse(any(
                e["to"].endswith("SomeHelper") for e in edges
            ))
            # confidence is ``inferred`` because the class->service
            # mapping is by naming convention.
            impl_edges = [e for e in edges if e["type"] == "implements"]
            for e in impl_edges:
                self.assertEqual(e["props"]["confidence"], "inferred")
                self.assertEqual(
                    e["props"]["source_strategy"], "grpc_bindings"
                )
            # file->rpc invokes edge emitted for the server side too.
            file_node_id = "file:src/server/catalog"
            inbound = {
                (e["from"], e["to"]) for e in edges
                if e["type"] == "invokes"
                and e["from"] == file_node_id
            }
            self.assertIn((file_node_id, rpc_get), inbound)
            self.assertIn((file_node_id, rpc_list), inbound)
            self.assertIn("src/server/catalog.py", discovered)

    def test_class_without_servicer_parent_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed_proto(root)
            pkg = root / "src" / "server"
            pkg.mkdir(parents=True)
            _write(pkg, "other.py", """\
                from catalog.v1 import catalog_pb2_grpc
                class Helper:
                    def GetProduct(self, request, context):
                        return None
            """)
            _, edges, _ = _run(root)
            self.assertEqual(
                [e for e in edges if e["type"] == "implements"], []
            )

    def test_unknown_servicer_name_is_dropped(self) -> None:
        """Class inherits from ``*Servicer`` but no matching proto service."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed_proto(root)
            pkg = root / "src" / "server"
            pkg.mkdir(parents=True)
            _write(pkg, "ghost.py", """\
                from other.v1 import ghost_pb2_grpc
                class GhostImpl(ghost_pb2_grpc.GhostServiceServicer):
                    def Haunt(self, request, context):
                        return None
            """)
            _, edges, _ = _run(root)
            self.assertEqual(
                [e for e in edges if e["type"] == "implements"], []
            )

# ---------------------------------------------------------------------------
# Client binding tests
# ---------------------------------------------------------------------------

class GrpcClientBindingTest(unittest.TestCase):
    """Client stub call sites bind to declared rpc ids."""

    def test_stub_method_call_emits_invokes_edge(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed_proto(root)
            pkg = root / "src" / "client"
            pkg.mkdir(parents=True)
            _write(pkg, "caller.py", """\
                import grpc
                from catalog.v1 import catalog_pb2_grpc

                def fetch(channel):
                    stub = catalog_pb2_grpc.CatalogServiceStub(channel)
                    return stub.GetProduct(None)
            """)
            nodes, edges, discovered = _run(root)
            rpc_get = "rpc:grpc:catalog.v1.catalogservice.getproduct"
            file_node_id = "file:src/client/caller"
            matches = [
                e for e in edges
                if e["type"] == "invokes"
                and e["from"] == file_node_id
                and e["to"] == rpc_get
            ]
            self.assertEqual(len(matches), 1)
            self.assertEqual(matches[0]["props"]["confidence"], "inferred")
            self.assertEqual(
                matches[0]["props"]["source_strategy"], "grpc_bindings"
            )
            self.assertIn("src/client/caller.py", discovered)

    def test_unknown_method_on_stub_is_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed_proto(root)
            pkg = root / "src" / "client"
            pkg.mkdir(parents=True)
            _write(pkg, "caller.py", """\
                from catalog.v1 import catalog_pb2_grpc
                def fetch(ch):
                    stub = catalog_pb2_grpc.CatalogServiceStub(ch)
                    return stub.NoSuchMethod(None)
            """)
            _, edges, _ = _run(root)
            self.assertEqual(
                [e for e in edges if e["type"] == "invokes"], []
            )

    def test_dynamic_method_name_is_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed_proto(root)
            pkg = root / "src" / "client"
            pkg.mkdir(parents=True)
            _write(pkg, "caller.py", """\
                from catalog.v1 import catalog_pb2_grpc
                def fetch(ch, name):
                    stub = catalog_pb2_grpc.CatalogServiceStub(ch)
                    return getattr(stub, name)(None)
            """)
            _, edges, _ = _run(root)
            self.assertEqual(
                [e for e in edges if e["type"] == "invokes"], []
            )

    def test_stub_from_unknown_service_is_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed_proto(root)
            pkg = root / "src" / "client"
            pkg.mkdir(parents=True)
            _write(pkg, "caller.py", """\
                from other.v1 import other_pb2_grpc
                def fetch(ch):
                    stub = other_pb2_grpc.OtherServiceStub(ch)
                    return stub.Do(None)
            """)
            _, edges, _ = _run(root)
            self.assertEqual(
                [e for e in edges if e["type"] == "invokes"], []
            )

# ---------------------------------------------------------------------------
# Fragment validation
# ---------------------------------------------------------------------------

class GrpcBindingsFragmentValidatesTest(unittest.TestCase):
    """Strategy output must pass contract.validate_fragment."""

    def test_fragment_is_contract_valid(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed_proto(root)
            pkg = root / "src" / "app"
            pkg.mkdir(parents=True)
            _write(pkg, "server.py", """\
                from catalog.v1 import catalog_pb2_grpc
                class Impl(catalog_pb2_grpc.CatalogServiceServicer):
                    def GetProduct(self, request, context):
                        return None
            """)
            _write(pkg, "client.py", """\
                from catalog.v1 import catalog_pb2_grpc
                def call(ch):
                    stub = catalog_pb2_grpc.CatalogServiceStub(ch)
                    return stub.GetProduct(None)
            """)
            nodes, edges, _ = _run(root)
            fragment = {"nodes": nodes, "edges": edges, "discovered_from": []}
            errors = validate_fragment(
                fragment,
                source_label="strategy:grpc_bindings",
                allow_dangling_edges=True,
            )
            self.assertEqual(errors, [], f"unexpected errors: {errors}")

class GrpcBindingsRobustnessTest(unittest.TestCase):
    """Graceful degradation on missing protos / malformed input."""

    def test_no_proto_files_yields_empty_result(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pkg = root / "src"
            pkg.mkdir(parents=True)
            _write(pkg, "a.py", "x = 1\n")
            nodes, edges, discovered = _run(root)
            self.assertEqual((nodes, edges, discovered), ({}, [], []))

    def test_missing_glob_yields_empty_result(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed_proto(root)
            result = extract(root, {}, {})
            self.assertEqual(result.nodes, {})
            self.assertEqual(result.edges, [])
            self.assertEqual(list(result.discovered_from), [])

    def test_python_without_pb2_grpc_import_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _seed_proto(root)
            pkg = root / "src"
            pkg.mkdir(parents=True)
            _write(pkg, "a.py", """\
                class Foo:
                    def GetProduct(self, request, context):
                        return None
            """)
            _, edges, _ = _run(root)
            self.assertEqual(edges, [])

if __name__ == "__main__":
    unittest.main()
