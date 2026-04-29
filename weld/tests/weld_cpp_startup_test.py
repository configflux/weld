"""Tests for C++ startup entrypoint enrichment."""

from __future__ import annotations

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)


class CppStartupTraceTest(unittest.TestCase):
    """C++ ``main`` files should join the trace interaction surface."""

    def test_cpp_main_emits_traceable_runtime_flow(self) -> None:
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            service = root / "services" / "payments" / "src"
            service.mkdir(parents=True)
            (service / "main.cpp").write_text(
                textwrap.dedent("""\
                    #include <grpcpp/grpcpp.h>

                    int main(int argc, char** argv) {
                        grpc::ServerBuilder builder;
                        (void)argc;
                        (void)argv;
                        return 0;
                    }
                """)
            )

            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     return_value={
                         "exports": ["main"],
                         "classes": [],
                         "imports": [],
                     },
                 ):
                result = tree_sitter.extract(
                    root=root,
                    source={"glob": "services/**/*.cpp", "language": "cpp"},
                    context={},
                )

        entrypoint = "entrypoint:services/payments/src/main"
        boundary = "boundary:services/payments/src/main:process"
        service_id = "service:payments"
        self.assertEqual(result.nodes[entrypoint]["type"], "entrypoint")
        self.assertEqual(result.nodes[boundary]["type"], "boundary")
        self.assertEqual(result.nodes[service_id]["type"], "service")
        self.assertEqual(result.nodes[entrypoint]["props"]["framework"], "grpc")
        self.assertEqual(result.nodes[boundary]["props"]["kind"], "runtime_process")
        edge_keys = {(e["from"], e["to"], e["type"]) for e in result.edges}
        self.assertIn((boundary, entrypoint, "exposes"), edge_keys)
        self.assertIn((service_id, entrypoint, "contains"), edge_keys)
        self.assertIn((service_id, boundary, "contains"), edge_keys)

    def test_cpp_main_declaration_is_not_startup(self) -> None:
        from weld.strategies import _cpp_tree_sitter

        self.assertFalse(
            _cpp_tree_sitter.is_startup_source(
                "include/app.h",
                "int main(int argc, char** argv);\n",
                {"exports": ["main"]},
            )
        )


if __name__ == "__main__":
    unittest.main()
