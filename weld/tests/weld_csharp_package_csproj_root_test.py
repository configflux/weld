"""Regression: ``csharp_package`` mints ``package:csharp:<root>`` from .csproj.

Sibling to :mod:`weld_csharp_package_strategy_test` covering the
csproj-derived self-namespace branch (federation gap 2dh6). The base
strategy test pins the source-derived ``namespace X.Y;`` contract; this
file pins the additional contract that a project's declared
``<RootNamespace>`` / ``<AssemblyName>`` / project-file-stem still
mints a producer node even when no ``.cs`` file declares that root
namespace in isolation, so cross-repo
:class:`weld.cross_repo.PackageImportResolver` matches can resolve
``using <RootNamespace>`` consumers against the producing library.

Split out from the base test file to keep both files under the
repo-wide 400-line cap (CLAUDE.md § Line-Count Policy) while keeping
the two concerns -- source-derived and csproj-derived -- cohesive in
separate files.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


from weld.strategies.csharp_package import extract  # noqa: E402


class CsharpPackageCsprojRootTest(unittest.TestCase):
    """``csharp_package.extract`` must mint ``package:csharp:<root>`` per project."""

    def test_csproj_root_namespace_minted_when_no_source_declares_it(
        self,
    ) -> None:
        """A ``<RootNamespace>`` declared in ``.csproj`` must mint a
        ``package:csharp:<root>`` node even if no ``.cs`` file under the
        glob declares ``namespace <root>;`` directly.

        Regression (federation, 2dh6): the ``Newtonsoft.Json`` corpus has
        sub-namespaces (``Newtonsoft.Json.Linq`` etc.) declared in source
        but its root ``Newtonsoft.Json`` is only consistently declared in
        SOME files and the strategy must still ALWAYS surface a producer
        node for the root so sibling repos that consume ``Newtonsoft.Json``
        via ``using`` can resolve cross-repo ``depends_on`` edges through
        ``package_import_resolver`` (which matches by ``props.name``).
        """
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src" / "Newtonsoft.Json").mkdir(parents=True)
            (root / "src" / "Newtonsoft.Json" / "Newtonsoft.Json.csproj").write_text(
                "<Project Sdk=\"Microsoft.NET.Sdk\">\n"
                "  <PropertyGroup>\n"
                "    <RootNamespace>Newtonsoft.Json</RootNamespace>\n"
                "    <AssemblyName>Newtonsoft.Json</AssemblyName>\n"
                "  </PropertyGroup>\n"
                "</Project>\n",
                encoding="utf-8",
            )
            # The only .cs file under the project declares a SUB-namespace,
            # never the root itself. The source-derived branch will only
            # mint ``package:csharp:newtonsoft.json.linq``; the csproj-derived
            # branch must mint ``package:csharp:newtonsoft.json`` so the root
            # producer exists for sibling repos.
            (root / "src" / "Newtonsoft.Json" / "JToken.cs").write_text(
                "namespace Newtonsoft.Json.Linq;\n"
                "public class JToken { }\n",
                encoding="utf-8",
            )
            result = extract(root, {"glob": "src/**/*.cs"}, {})

        self.assertIn("package:csharp:newtonsoft.json", result.nodes)
        node = result.nodes["package:csharp:newtonsoft.json"]
        self.assertEqual(node["type"], "package")
        self.assertEqual(node["props"]["name"], "Newtonsoft.Json")
        self.assertEqual(node["props"]["language"], "csharp")
        self.assertEqual(node["props"]["source_strategy"], "csharp_package")
        self.assertEqual(node["props"]["origin"], "project")

    def test_csproj_root_namespace_has_at_least_one_contains_edge(self) -> None:
        """The csproj-derived ``package:csharp:<root>`` must still satisfy
        the ADR 0060 file-anchor invariant: every minted package has at
        least one outgoing ``contains -> file:*`` edge. The csproj branch
        anchors on every ``.cs`` file under the project directory whose
        declared namespace is the root or a descendant of the root."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src" / "Newtonsoft.Json").mkdir(parents=True)
            (root / "src" / "Newtonsoft.Json" / "Newtonsoft.Json.csproj").write_text(
                "<Project Sdk=\"Microsoft.NET.Sdk\">\n"
                "  <PropertyGroup>\n"
                "    <RootNamespace>Newtonsoft.Json</RootNamespace>\n"
                "  </PropertyGroup>\n"
                "</Project>\n",
                encoding="utf-8",
            )
            (root / "src" / "Newtonsoft.Json" / "JToken.cs").write_text(
                "namespace Newtonsoft.Json.Linq;\n"
                "public class JToken { }\n",
                encoding="utf-8",
            )
            (root / "src" / "Newtonsoft.Json" / "JsonConvert.cs").write_text(
                "namespace Newtonsoft.Json;\n"
                "public static class JsonConvert { }\n",
                encoding="utf-8",
            )
            result = extract(root, {"glob": "src/**/*.cs"}, {})

        pkg_id = "package:csharp:newtonsoft.json"
        self.assertIn(pkg_id, result.nodes)
        outgoing = [
            e for e in result.edges
            if e["from"] == pkg_id and e["type"] == "contains"
            and e["to"].startswith("file:")
        ]
        self.assertGreater(len(outgoing), 0)
        # Both source-declared (Newtonsoft.Json) and descendant
        # (Newtonsoft.Json.Linq) files must anchor under the csproj root.
        anchored = {e["to"] for e in outgoing}
        self.assertIn("file:src/Newtonsoft.Json/JsonConvert", anchored)
        self.assertIn("file:src/Newtonsoft.Json/JToken", anchored)

    def test_csproj_root_namespace_does_not_anchor_unrelated_files(
        self,
    ) -> None:
        """The csproj-derived root must NOT pull in files outside the
        project directory tree, and must NOT pull in files whose source
        namespace is unrelated to the root (no false-positive containment).
        """
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src" / "Newtonsoft.Json").mkdir(parents=True)
            (root / "src" / "Newtonsoft.Json" / "Newtonsoft.Json.csproj").write_text(
                "<Project Sdk=\"Microsoft.NET.Sdk\">\n"
                "  <PropertyGroup>\n"
                "    <RootNamespace>Newtonsoft.Json</RootNamespace>\n"
                "  </PropertyGroup>\n"
                "</Project>\n",
                encoding="utf-8",
            )
            (root / "src" / "Newtonsoft.Json" / "JsonConvert.cs").write_text(
                "namespace Newtonsoft.Json;\n"
                "public static class JsonConvert { }\n",
                encoding="utf-8",
            )
            # A sibling project under src/Other with a completely
            # different namespace -- must NOT be anchored under
            # package:csharp:newtonsoft.json.
            (root / "src" / "Other").mkdir(parents=True)
            (root / "src" / "Other" / "Other.csproj").write_text(
                "<Project Sdk=\"Microsoft.NET.Sdk\">\n"
                "  <PropertyGroup>\n"
                "    <RootNamespace>Other.Lib</RootNamespace>\n"
                "  </PropertyGroup>\n"
                "</Project>\n",
                encoding="utf-8",
            )
            (root / "src" / "Other" / "Thing.cs").write_text(
                "namespace Other.Lib;\n"
                "public class Thing { }\n",
                encoding="utf-8",
            )
            result = extract(root, {"glob": "src/**/*.cs"}, {})

        pkg_id = "package:csharp:newtonsoft.json"
        anchored = {
            e["to"] for e in result.edges
            if e["from"] == pkg_id and e["type"] == "contains"
        }
        self.assertIn("file:src/Newtonsoft.Json/JsonConvert", anchored)
        self.assertNotIn("file:src/Other/Thing", anchored)

    def test_csproj_root_namespace_idempotent_with_source_declaration(
        self,
    ) -> None:
        """If a source file already declared the root namespace, the
        csproj-derived branch must not produce a SECOND node and must not
        duplicate ``contains`` edges. The same canonical id wins; props
        are stable across runs."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src" / "Newtonsoft.Json").mkdir(parents=True)
            (root / "src" / "Newtonsoft.Json" / "Newtonsoft.Json.csproj").write_text(
                "<Project Sdk=\"Microsoft.NET.Sdk\">\n"
                "  <PropertyGroup>\n"
                "    <RootNamespace>Newtonsoft.Json</RootNamespace>\n"
                "  </PropertyGroup>\n"
                "</Project>\n",
                encoding="utf-8",
            )
            (root / "src" / "Newtonsoft.Json" / "JsonConvert.cs").write_text(
                "namespace Newtonsoft.Json;\n"
                "public static class JsonConvert { }\n",
                encoding="utf-8",
            )
            result = extract(root, {"glob": "src/**/*.cs"}, {})

        pkg_id = "package:csharp:newtonsoft.json"
        self.assertIn(pkg_id, result.nodes)
        outgoing = [
            e for e in result.edges
            if e["from"] == pkg_id and e["type"] == "contains"
            and e["to"] == "file:src/Newtonsoft.Json/JsonConvert"
        ]
        # Exactly one contains edge to the one file -- no duplicates.
        self.assertEqual(len(outgoing), 1)

    def test_csproj_root_namespace_case_collapses_with_source_variant(
        self,
    ) -> None:
        """If the csproj declares ``<RootNamespace>NEWTONSOFT.JSON</RootNamespace>``
        and a source file declares ``namespace Newtonsoft.Json;`` the two
        producers must collapse to the single canonical
        ``package:csharp:newtonsoft.json`` id (ADR 0041 Layer 1).

        Regression note: the closed duplicate vowh flagged
        case-collision shadow warnings against existing case-preserved
        aliases for the same namespace; this asserts the package-id
        case-fold continues to absorb csproj-only producers."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src" / "Newtonsoft.Json").mkdir(parents=True)
            (root / "src" / "Newtonsoft.Json" / "Newtonsoft.Json.csproj").write_text(
                "<Project Sdk=\"Microsoft.NET.Sdk\">\n"
                "  <PropertyGroup>\n"
                "    <RootNamespace>NEWTONSOFT.JSON</RootNamespace>\n"
                "  </PropertyGroup>\n"
                "</Project>\n",
                encoding="utf-8",
            )
            (root / "src" / "Newtonsoft.Json" / "JsonConvert.cs").write_text(
                "namespace Newtonsoft.Json;\n"
                "public static class JsonConvert { }\n",
                encoding="utf-8",
            )
            result = extract(root, {"glob": "src/**/*.cs"}, {})

        package_ids = {
            nid for nid in result.nodes
            if nid.startswith("package:csharp:newtonsoft.json")
            and nid != "package:csharp:newtonsoft.json.linq"
        }
        # Only one canonical id; no shadow alias.
        self.assertEqual(package_ids, {"package:csharp:newtonsoft.json"})

    def test_csproj_with_no_explicit_root_falls_back_to_project_stem(
        self,
    ) -> None:
        """If a csproj declares neither ``<RootNamespace>`` nor
        ``<AssemblyName>``, the strategy must still mint a producer node
        keyed by the project-file stem -- mirroring
        ``_csharp_origin.load_project_namespace_roots`` so the producer and
        consumer sides of ADR 0042 stay in sync."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src" / "MyLib").mkdir(parents=True)
            (root / "src" / "MyLib" / "MyLib.csproj").write_text(
                "<Project Sdk=\"Microsoft.NET.Sdk\">\n"
                "  <PropertyGroup>\n"
                "    <TargetFramework>net8.0</TargetFramework>\n"
                "  </PropertyGroup>\n"
                "</Project>\n",
                encoding="utf-8",
            )
            (root / "src" / "MyLib" / "Helper.cs").write_text(
                "namespace MyLib.Internal;\n"
                "public class Helper { }\n",
                encoding="utf-8",
            )
            result = extract(root, {"glob": "src/**/*.cs"}, {})

        self.assertIn("package:csharp:mylib", result.nodes)
        node = result.nodes["package:csharp:mylib"]
        self.assertEqual(node["props"]["name"], "MyLib")

    def test_csproj_root_with_no_cs_files_emits_nothing(self) -> None:
        """If the csproj has no ``.cs`` files under it (no anchorable
        target), the strategy must NOT mint a dangling producer node.
        The ADR 0060 invariant (every package node has >=1 outgoing
        ``contains`` edge) must hold even on the csproj branch."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src" / "Empty").mkdir(parents=True)
            (root / "src" / "Empty" / "Empty.csproj").write_text(
                "<Project Sdk=\"Microsoft.NET.Sdk\">\n"
                "  <PropertyGroup>\n"
                "    <RootNamespace>Empty.Lib</RootNamespace>\n"
                "  </PropertyGroup>\n"
                "</Project>\n",
                encoding="utf-8",
            )
            result = extract(root, {"glob": "src/**/*.cs"}, {})

        # No .cs files at all -> no producer nodes, no edges.
        self.assertNotIn("package:csharp:empty.lib", result.nodes)
        self.assertEqual(result.nodes, {})
        self.assertEqual(result.edges, [])


if __name__ == "__main__":
    unittest.main()
