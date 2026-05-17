"""Tests for C# language support in the tree-sitter strategy."""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock


class CSharpTreeSitterSupportTest(unittest.TestCase):
    """C# support adds metadata and using dependency edges."""

    def _make_csharp_tree(self, tmp: str) -> Path:
        root = Path(tmp)
        src = root / "src"
        src.mkdir()
        (root / "Sample.Api.csproj").write_text(
            textwrap.dedent("""\
                <Project Sdk="Microsoft.NET.Sdk.Web">
                  <PropertyGroup>
                    <RootNamespace>Sample.Api</RootNamespace>
                  </PropertyGroup>
                  <ItemGroup>
                    <PackageReference Include="Microsoft.AspNetCore.Mvc" />
                  </ItemGroup>
                </Project>
            """),
            encoding="utf-8",
        )
        (src / "OrdersController.cs").write_text(
            textwrap.dedent("""\
                using System.Threading.Tasks;
                using Microsoft.AspNetCore.Mvc;
                using Sample.Api.Contracts;

                namespace Sample.Api.Controllers;

                [ApiController]
                public class OrdersController {
                    [HttpGet("{id}")]
                    public Task<OrderDto> GetAsync(int id) =>
                        Task.FromResult(new OrderDto(id));
                    private string Helper => "ok";
                }
            """)
        )
        return root

    def test_csharp_extract_adds_metadata_and_dependency_edges(self) -> None:
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = self._make_csharp_tree(td)
            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     return_value={
                         "exports": ["OrdersController", "GetAsync", "Helper"],
                         "classes": ["OrdersController"],
                         "imports": [
                             "System.Threading.Tasks",
                             "Microsoft.AspNetCore.Mvc",
                             "Sample.Api.Contracts",
                         ],
                         "methods": ["GetAsync"],
                         "properties": ["Helper"],
                         "attributes": ["ApiController", "HttpGet"],
                         "namespaces": ["Sample.Api.Controllers"],
                     },
                 ):
                result = tree_sitter.extract(
                    root,
                    {"glob": "**/*.cs", "language": "csharp"},
                    {},
                )
        file_node = next(n for n in result.nodes.values() if n["type"] == "file")
        props = file_node["props"]
        self.assertEqual(props["types"], ["OrdersController"])
        self.assertIn("ApiController", props["attributes"])
        self.assertEqual(props["method_visibility"]["GetAsync"], ["public"])
        self.assertEqual(props["property_visibility"]["Helper"], ["private"])
        deps = [e for e in result.edges if e["type"] == "depends_on"]
        self.assertEqual(len(deps), 3)
        # ADR 0041 § Layer 1: package ids route through ``canonical_slug``
        # which lowercases mixed-case names like ``Microsoft.AspNetCore.Mvc``.
        self.assertIn("package:csharp:microsoft.aspnetcore.mvc", result.nodes)
        self.assertEqual(
            result.nodes["package:csharp:system.threading.tasks"]["props"][
                "origin"
            ],
            "stdlib",
        )
        self.assertEqual(
            result.nodes["package:csharp:microsoft.aspnetcore.mvc"]["props"][
                "origin"
            ],
            "external",
        )
        self.assertEqual(
            result.nodes["package:csharp:sample.api.contracts"]["props"][
                "origin"
            ],
            "project",
        )
        # The pre-migration id is preserved on ``aliases`` per the
        # one-minor-version deprecation timeline in ADR 0041.
        self.assertIn(
            "package:csharp:Microsoft.AspNetCore.Mvc",
            result.nodes["package:csharp:microsoft.aspnetcore.mvc"][
                "props"
            ]["aliases"],
        )

    def test_csharp_wrapper_sets_language_and_strategy_label(self) -> None:
        from weld.strategies import csharp, tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = self._make_csharp_tree(td)
            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     return_value={
                         "exports": ["OrdersController"],
                         "classes": ["OrdersController"],
                         "imports": [],
                     },
                 ):
                result = csharp.extract(root, {"glob": "**/*.cs"}, {})
        node = next(n for n in result.nodes.values() if n["type"] == "file")
        self.assertEqual(node["props"]["source_strategy"], "csharp")

    def test_program_cs_emits_startup_entrypoint_and_boundary(self) -> None:
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "services" / "api"
            src.mkdir(parents=True)
            (src / "Program.cs").write_text(
                textwrap.dedent("""\
                    using Microsoft.AspNetCore.Builder;
                    using Microsoft.Extensions.Hosting;

                    var builder = WebApplication.CreateBuilder(args);
                    var app = builder.Build();
                    app.Run();
                """)
            )
            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     return_value={
                         "exports": [],
                         "classes": [],
                         "imports": [
                             "Microsoft.AspNetCore.Builder",
                             "Microsoft.Extensions.Hosting",
                         ],
                     },
                 ):
                result = tree_sitter.extract(
                    root,
                    {"glob": "**/*.cs", "language": "csharp"},
                    {},
                )

        self.assertIn("entrypoint:services/api/Program", result.nodes)
        self.assertIn("boundary:services/api/Program:host", result.nodes)
        self.assertIn("service:api", result.nodes)
        self.assertIn(
            "startup",
            result.nodes["entrypoint:services/api/Program"]["props"]["description"],
        )
        edge_keys = {(e["from"], e["to"], e["type"]) for e in result.edges}
        self.assertIn(
            ("boundary:services/api/Program:host",
             "entrypoint:services/api/Program", "exposes"),
            edge_keys,
        )
        self.assertIn(
            ("service:api", "entrypoint:services/api/Program", "contains"),
            edge_keys,
        )

        # ADR 0041 Layer 3 file-anchor-symmetry: the *file* anchor for
        # a startup source must carry "entrypoint" in props.roles so the
        # built-in allow-list exempts it without per-repo
        # .weld/discover.yaml entries. The Program.cs top-level
        # statements path has no namespace declaration, so
        # csharp_package correctly skips it -- without this role tag
        # the file anchor would have outgoing 'contains' edges and no
        # inbound edge, tripping the rule.
        file_node = next(
            n for n in result.nodes.values()
            if n["type"] == "file"
            and n["props"]["file"].endswith("Program.cs")
        )
        self.assertIn("entrypoint", file_node["props"]["roles"])
        self.assertIn("implementation", file_node["props"]["roles"])

    def test_non_startup_csharp_file_keeps_implementation_role_only(self) -> None:
        """Non-startup .cs files must NOT receive the 'entrypoint' role."""
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = self._make_csharp_tree(td)
            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     return_value={
                         "exports": ["OrdersController"],
                         "classes": ["OrdersController"],
                         "imports": ["Microsoft.AspNetCore.Mvc"],
                     },
                 ):
                result = tree_sitter.extract(
                    root,
                    {"glob": "**/*.cs", "language": "csharp"},
                    {},
                )
        file_node = next(n for n in result.nodes.values() if n["type"] == "file")
        roles = file_node["props"]["roles"]
        self.assertEqual(roles, ["implementation"])
        self.assertNotIn("entrypoint", roles)

    def test_csharp_grammar_aliases_match_pypi_package(self) -> None:
        from weld.strategies._ts_parse import (
            grammar_module_name,
            grammar_package_name,
        )

        self.assertEqual(grammar_module_name("csharp"), "tree_sitter_c_sharp")
        self.assertEqual(grammar_package_name("csharp"), "tree-sitter-c-sharp")

    def test_init_detect_maps_cs_extension(self) -> None:
        from weld.init_detect import EXT_TO_LANG

        self.assertEqual(EXT_TO_LANG.get(".cs"), "csharp")

    def test_mixed_decl_kinds_promote_to_canonical_kinds(self) -> None:
        """Per-decl-kind buckets propagate canonical kinds through extract.

        ADR 0064 § 1: a file declaring one class, one interface, one
        struct, and one record must yield four symbol nodes whose
        ``kind`` matches the declaration construct (no collapse to
        ``class``). Also pins that the file node's ``types`` prop is
        the union of every type-like name so
        ``_csharp_inheritance.build_project_file_index`` still resolves
        non-class bases.
        """
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "src"
            src.mkdir()
            (src / "Mixed.cs").write_text(
                textwrap.dedent("""\
                    namespace Sample;
                    public class MyClass {}
                    public interface IMyService {}
                    public struct Point {}
                    public record Person(string Name);
                """),
                encoding="utf-8",
            )
            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     return_value={
                         "exports": [
                             "Sample",
                             "MyClass",
                             "IMyService",
                             "Point",
                             "Person",
                         ],
                         "classes": ["MyClass"],
                         "interfaces": ["IMyService"],
                         "structs": ["Point"],
                         "records": ["Person"],
                         "imports": [],
                         "namespaces": ["Sample"],
                     },
                 ):
                result = tree_sitter.extract(
                    root,
                    {"glob": "**/*.cs", "language": "csharp"},
                    {},
                )
        file_node = next(n for n in result.nodes.values() if n["type"] == "file")
        # Union of class/interface/struct/record names; per-decl ordering
        # within the union is implementation-detail, so compare as sets.
        self.assertEqual(
            set(file_node["props"]["types"]),
            {"MyClass", "IMyService", "Point", "Person"},
        )
        # The four symbol nodes carry the four canonical kinds.
        kind_by_label = {
            n["label"]: n["props"]["kind"]
            for n in result.nodes.values()
            if n["type"] == "symbol" and "kind" in n["props"]
        }
        self.assertEqual(
            {
                "MyClass": "class",
                "IMyService": "interface",
                "Point": "struct",
                "Person": "record",
            }.items() <= kind_by_label.items(),
            True,
            f"expected canonical kinds, got {kind_by_label}",
        )

    def test_real_csharp_grammar_emits_disjoint_decl_buckets(self) -> None:
        """End-to-end: real tree-sitter parse yields disjoint buckets.

        Without mocks: parses a fixture through the actual
        ``tree_sitter_c_sharp`` grammar and verifies that each
        per-decl-kind query captures only its construct (e.g. the
        ``interfaces`` bucket contains ``IMyService`` and nothing
        else). This guards against future regressions that would
        re-merge the queries.
        """
        try:
            import tree_sitter  # noqa: F401
            import tree_sitter_c_sharp  # noqa: F401
        except Exception:
            self.skipTest("tree_sitter / tree_sitter_c_sharp not available")
        from weld.strategies._ts_parse import parse_file_symbols
        from weld.strategies.tree_sitter import load_language_queries

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "Mixed.cs"
            src.write_text(
                textwrap.dedent("""\
                    namespace Sample;
                    public class MyClass {}
                    public interface IMyService {}
                    public struct Point {}
                    public record Person(string Name);
                """),
                encoding="utf-8",
            )
            queries = load_language_queries("csharp")
            symbols = parse_file_symbols(src, "csharp", queries)
        self.assertEqual(symbols.get("classes"), ["MyClass"])
        self.assertEqual(symbols.get("interfaces"), ["IMyService"])
        self.assertEqual(symbols.get("structs"), ["Point"])
        self.assertEqual(symbols.get("records"), ["Person"])


if __name__ == "__main__":
    unittest.main()
