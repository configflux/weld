"""Integration tests for ADR 0042 C++ origin tagging.

End-to-end coverage of the layer-1 + layer-2 emission paths:

  * Layer-1 ``_extract_call_edges`` stamps ``origin="project"`` on
    project-defined symbol nodes and ``origin="unresolved"`` on
    unresolved sentinels.
  * The layer-1 ``file`` node minted by the tree-sitter strategy
    carries ``origin="project"`` for C++ sources.
  * Layer-2 ``cpp_resolver.resolve_includes_pass`` rewrites unresolved
    sentinels to fully-resolved symbol nodes whose ``origin`` matches
    the resolved header's location and the callee's namespace, with
    ``upgrade_origin`` semantics that never downgrade a definite tag.

The pure-helper unit tests live in ``weld_cpp_origin_test.py``.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)
_tests_dir = str(Path(__file__).resolve().parent)
if _tests_dir not in sys.path:
    sys.path.insert(0, _tests_dir)

from cpp_resolver_fakes import fake_call_edges, fake_parse  # noqa: E402

from weld.strategies import cpp_resolver  # noqa: E402


# ---------------------------------------------------------------------------
# Layer-1 / layer-2 fixture-based integration
# ---------------------------------------------------------------------------


class CppOriginFixtureTest(unittest.TestCase):
    """End-to-end origin tagging via the bundled cpp_clang fixture."""

    FIXTURE = Path(__file__).resolve().parent / "fixtures" / "cpp_clang"

    def _run(self, glob: str = "**/*.cpp"):
        from weld.strategies import tree_sitter

        with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
             mock.patch.object(
                 tree_sitter, "_parse_file_symbols", side_effect=fake_parse,
             ), \
             mock.patch.object(
                 tree_sitter, "_extract_call_edges", side_effect=fake_call_edges,
             ):
            return tree_sitter.extract(
                root=self.FIXTURE,
                source={
                    "glob": glob,
                    "language": "cpp",
                    "emit_calls": True,
                },
                context={},
            )

    def test_layer1_project_definitions_have_project_origin(self) -> None:
        """Every layer-1 definition symbol from a project source file
        carries ``origin="project"``."""
        result = self._run()
        # Out-of-class definitions in foo.cpp are layer-1 emissions.
        impl_id = "symbol:cpp:src.foo:Foo::bar"
        self.assertIn(impl_id, result.nodes)
        self.assertEqual(
            result.nodes[impl_id]["props"].get("origin"), "project",
        )

    def test_layer1_file_caller_has_project_origin(self) -> None:
        result = self._run()
        # Every C++ source file in the fixture mints a ``<file>`` caller.
        for nid, node in result.nodes.items():
            if not nid.startswith("symbol:cpp:"):
                continue
            if node.get("props", {}).get("qualname") != "<file>":
                continue
            self.assertEqual(
                node["props"].get("origin"), "project",
                msg=f"{nid} should be project-origin",
            )

    def test_layer1_unresolved_sentinels_have_unresolved_origin(self) -> None:
        result = self._run()
        # The fixture intentionally retains some unresolved sentinels
        # (callees with no matching header symbol).
        unresolved_ids = [
            nid for nid in result.nodes
            if nid.startswith("symbol:unresolved:")
        ]
        self.assertTrue(unresolved_ids, "fixture should retain sentinels")
        for nid in unresolved_ids:
            self.assertEqual(
                result.nodes[nid]["props"].get("origin"), "unresolved",
                msg=f"{nid} should be unresolved-origin",
            )

    def test_layer2_resolved_node_has_project_origin(self) -> None:
        """``Foo::bar`` resolves through ``include/foo.h`` (a repo-local
        header), so the layer-2 rewrite tags ``origin="project"``."""
        result = self._run()
        resolved_id = "symbol:cpp:include.foo:Foo::bar"
        self.assertIn(resolved_id, result.nodes)
        self.assertEqual(
            result.nodes[resolved_id]["props"].get("origin"), "project",
        )

    def test_layer1_file_node_has_project_origin(self) -> None:
        """The ``file`` nodes minted by tree_sitter for C++ sources
        carry origin="project"."""
        result = self._run()
        file_nodes = [
            (nid, node) for nid, node in result.nodes.items()
            if node.get("type") == "file"
            and node.get("props", {}).get("file", "").endswith(".cpp")
        ]
        self.assertTrue(file_nodes, "fixture should emit cpp file nodes")
        for nid, node in file_nodes:
            self.assertEqual(
                node["props"].get("origin"), "project",
                msg=f"{nid} should be project-origin",
            )


# ---------------------------------------------------------------------------
# Direct resolver tests with a synthetic system header
# ---------------------------------------------------------------------------


def _build_state(
    impl_path: Path,
    callee: str,
    hdr_abs_path: Path,
    hdr_rel_path: str,
    hdr_module: str,
) -> tuple[list[dict], dict[str, dict], list[dict]]:
    """Construct minimal layer-2 inputs for a single resolved-include test."""
    sentinel_id = f"symbol:unresolved:{callee}"
    file_caller = "symbol:cpp:src.main:<file>"

    nodes: dict[str, dict] = {
        file_caller: {
            "type": "symbol",
            "label": "src.main",
            "props": {
                "language": "cpp",
                "qualname": "<file>",
                "origin": "project",
            },
        },
        sentinel_id: {
            "type": "symbol",
            "label": callee,
            "props": {
                "language": "cpp",
                "qualname": callee,
                "resolved": False,
                "origin": "unresolved",
            },
        },
    }
    edges: list[dict] = [
        {
            "from": file_caller,
            "to": sentinel_id,
            "type": "calls",
            "props": {
                "resolved": False,
                "confidence": "speculative",
                "resolution": "unresolved",
            },
        },
    ]
    per_file: list[dict] = [
        {
            "abs_path": impl_path,
            "rel_path": "src/main.cpp",
            "module_path": "src.main",
            "imports": ['"system_hdr.h"'],
            "exports_set": set(),
            "classes_set": set(),
            "file_caller_id": file_caller,
        },
        {
            "abs_path": hdr_abs_path,
            "rel_path": hdr_rel_path,
            "module_path": hdr_module,
            "imports": [],
            "exports_set": {callee},
            "classes_set": set(),
            "file_caller_id": (
                f"symbol:cpp:{hdr_module}:<file>"
            ),
        },
    ]
    return per_file, nodes, edges


class CppResolverLayer2OriginTest(unittest.TestCase):
    """Drive ``resolve_includes_pass`` directly with synthetic state.

    We construct a per-file state list that points at synthetic header
    paths outside the repo root so we can exercise the stdlib / external
    branches without depending on the real host filesystem layout.
    """

    def test_layer2_rewrite_to_synthetic_stdlib_header(self) -> None:
        """Resolved include under a synthetic stdlib path -> stdlib."""
        callee = "vector"
        synthetic_hdr = Path("/usr/include/c++/13/vector")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src").mkdir()
            impl = root / "src" / "main.cpp"
            impl.write_text('#include "system_hdr.h"\n')
            per_file, nodes, edges = _build_state(
                impl, callee, synthetic_hdr,
                "system.vector", "system.vector",
            )
            with mock.patch.object(
                cpp_resolver,
                "resolve_cpp_include",
                return_value=synthetic_hdr,
            ):
                cpp_resolver.resolve_includes_pass(
                    root, per_file, nodes, edges,
                )
            resolved_id = f"symbol:cpp:system.vector:{callee}"
            self.assertIn(resolved_id, nodes)
            self.assertEqual(
                nodes[resolved_id]["props"].get("origin"), "stdlib",
            )

    def test_layer2_rewrite_to_synthetic_external_header(self) -> None:
        """Resolved include under ``/usr/include/boost/...`` -> external."""
        callee = "io_context"
        synthetic_hdr = Path("/usr/include/boost/asio/io_context.hpp")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src").mkdir()
            impl = root / "src" / "main.cpp"
            impl.write_text('#include "system_hdr.h"\n')
            per_file, nodes, edges = _build_state(
                impl, callee, synthetic_hdr,
                "external.boost.io_context", "external.boost.io_context",
            )
            with mock.patch.object(
                cpp_resolver,
                "resolve_cpp_include",
                return_value=synthetic_hdr,
            ):
                cpp_resolver.resolve_includes_pass(
                    root, per_file, nodes, edges,
                )
            resolved_id = (
                f"symbol:cpp:external.boost.io_context:{callee}"
            )
            self.assertIn(resolved_id, nodes)
            self.assertEqual(
                nodes[resolved_id]["props"].get("origin"), "external",
            )

    def test_layer2_std_namespace_overrides_path(self) -> None:
        """``std::max`` resolved through any header is stdlib-origin.

        Even if the resolver finds a repo-local header that re-exports
        the symbol, the ``std::`` namespace heuristic on the callee
        determines the classification.
        """
        callee = "std::max"
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src").mkdir()
            (root / "include").mkdir()
            impl = root / "src" / "main.cpp"
            hdr = root / "include" / "compat.h"
            impl.write_text('#include "compat.h"\n')
            hdr.write_text("// re-exports std::max\n")
            per_file, nodes, edges = _build_state(
                impl, callee, hdr,
                "include/compat.h", "include.compat",
            )
            with mock.patch.object(
                cpp_resolver,
                "resolve_cpp_include",
                return_value=hdr,
            ):
                cpp_resolver.resolve_includes_pass(
                    root, per_file, nodes, edges,
                )
            resolved_id = f"symbol:cpp:include.compat:{callee}"
            self.assertIn(resolved_id, nodes)
            self.assertEqual(
                nodes[resolved_id]["props"].get("origin"), "stdlib",
            )

    def test_layer2_does_not_downgrade_existing_origin(self) -> None:
        """If the resolved-id node already exists with a definite tag
        and layer 2 produces the same tag, the prior tag is preserved
        and never silently coerced to ``unresolved``."""
        callee = "Foo::bar"
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src").mkdir()
            (root / "include").mkdir()
            impl = root / "src" / "main.cpp"
            hdr = root / "include" / "foo.h"
            impl.write_text('#include "foo.h"\n')
            hdr.write_text("// foo\n")
            per_file, nodes, edges = _build_state(
                impl, callee, hdr,
                "include/foo.h", "include.foo",
            )
            resolved_id = f"symbol:cpp:include.foo:{callee}"
            nodes[resolved_id] = {
                "type": "symbol",
                "label": callee,
                "props": {
                    "language": "cpp",
                    "qualname": callee,
                    "origin": "project",
                    "confidence": "definite",
                },
            }
            with mock.patch.object(
                cpp_resolver,
                "resolve_cpp_include",
                return_value=hdr,
            ):
                cpp_resolver.resolve_includes_pass(
                    root, per_file, nodes, edges,
                )
            self.assertEqual(
                nodes[resolved_id]["props"].get("origin"), "project",
            )


if __name__ == "__main__":
    unittest.main()
