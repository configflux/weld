"""Integration test: SDK-style C# csproj discovery.

Regression fixture for the eShopOnWeb under-extract bug:
dotnet-architecture/eShopOnWeb is an ASP.NET Core SDK-style project
with ``<Project Sdk="Microsoft.NET.Sdk.Web">`` and *no* explicit
``<Compile Include>`` entries. The fixture under
``weld/tests/fixtures/csharp_sdk_style/`` mirrors that shape with the
smallest surface needed to exercise the SDK implicit-glob path
without vendoring the full eShopOnWeb tree:

* one SDK-style csproj with no Compile directives
* one Program.cs (top-level Main + WebApplication.CreateBuilder so
  the startup-source detector fires)
* one ASP.NET controller (Microsoft.AspNetCore.Mvc + ``[ApiController]``
  + ``[HttpGet]`` + ``[HttpPost]``)
* one EF Core DbContext + DbSet entity

These tests assert the *discovery output shape*: every .cs file gets
a file: node, the csproj claims every file (SDK implicit glob),
controllers + routes emit the expected nodes, and the DbContext +
entity surface through csharp_efcore. The fixture is the regression
guard for the SDK-style codepath -- if the implicit-glob resolver
silently drops files in future, this test fails before a real corpus
does.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

# Ensure the in-source weld package is on sys.path when run outside Bazel.
_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.strategies._csharp_project_files import (  # noqa: E402
    is_sdk_style,
    resolve_owned_files,
)
from weld.strategies.csharp_aspnet_routes import (  # noqa: E402
    extract as routes_extract,
)
from weld.strategies.csharp_efcore import extract as efcore_extract  # noqa: E402
from weld.strategies.csharp_project import extract as csproj_extract  # noqa: E402
import xml.etree.ElementTree as ET  # noqa: E402


def _fixture_root() -> Path:
    """Return the SDK-style fixture path, preferring Bazel runfiles."""
    here = Path(__file__).resolve().parent
    candidate = here / "fixtures" / "csharp_sdk_style"
    if candidate.is_dir():
        return candidate
    runfiles = os.environ.get("RUNFILES_DIR")
    if runfiles:
        rf_candidate = (
            Path(runfiles)
            / "_main"
            / "weld"
            / "tests"
            / "fixtures"
            / "csharp_sdk_style"
        )
        if rf_candidate.is_dir():
            return rf_candidate
    return candidate


class SdkStyleCsprojDetectionTest(unittest.TestCase):
    """The fixture's csproj is recognised as SDK-style."""

    def setUp(self) -> None:
        self.root = _fixture_root()
        if not self.root.is_dir():
            self.skipTest(f"Fixture not reachable at {self.root}")
        self.csproj = self.root / "src" / "WebApp" / "WebApp.csproj"

    def test_is_sdk_style_returns_true(self) -> None:
        # ADR 0056 addendum: ``Project Sdk="..."`` => SDK-style; the
        # implicit ``**/*.cs`` glob applies. The fixture has zero
        # ``<Compile Include>`` directives, so a regression that
        # mis-detected the SDK shape would discover zero .cs files.
        xml = ET.parse(self.csproj).getroot()
        self.assertTrue(is_sdk_style(xml))

    def test_implicit_glob_claims_every_cs_file(self) -> None:
        # The fixture ships exactly three .cs files: Program.cs,
        # ProductsController.cs, AppDbContext.cs. The SDK implicit
        # ``**/*.cs`` glob (under the csproj directory) must claim
        # all three even though the csproj has no explicit Compile
        # directives.
        xml = ET.parse(self.csproj).getroot()
        owned = resolve_owned_files(self.csproj, xml, self.root)
        relative = sorted(
            str(p.relative_to(self.root).as_posix()) for p in owned
        )
        self.assertIn("src/WebApp/Program.cs", relative)
        self.assertIn("src/WebApp/Controllers/ProductsController.cs", relative)
        self.assertIn("src/WebApp/Data/AppDbContext.cs", relative)
        # Exactly the three fixture files; no over-claim.
        self.assertEqual(len(relative), 3)


class SdkStyleProjectContainsEdgesTest(unittest.TestCase):
    """``csharp_project`` emits ``contains`` edges to every implicit file."""

    def setUp(self) -> None:
        self.root = _fixture_root()
        if not self.root.is_dir():
            self.skipTest(f"Fixture not reachable at {self.root}")

    def test_csproj_contains_every_implicit_file(self) -> None:
        # End-to-end: the strategy must emit one csproj://WebApp ->
        # contains -> file:<rel_path> edge for every .cs file in the
        # implicit glob. Mirrors the eShopOnWeb shape (eShop's Web
        # csproj declares zero <Compile Include> but the SDK glob
        # captures every Controllers/, Data/, Services/ file).
        result = csproj_extract(self.root, {"glob": "**/*.csproj"}, {})
        contains = {
            (e["from"], e["to"]) for e in result.edges
            if e["type"] == "contains"
        }
        # All three files are owned by csproj://WebApp.
        self.assertIn(
            ("csproj://WebApp", "file:src/WebApp/Program"),
            contains,
        )
        self.assertIn(
            ("csproj://WebApp", "file:src/WebApp/Controllers/ProductsController"),
            contains,
        )
        self.assertIn(
            ("csproj://WebApp", "file:src/WebApp/Data/AppDbContext"),
            contains,
        )

    def test_csproj_node_emitted_with_target_framework(self) -> None:
        # SDK-style csproj sets ``<TargetFramework>net8.0</TargetFramework>``;
        # the strategy must surface it on the csproj node so downstream
        # consumers (lint, federation) can filter by framework.
        result = csproj_extract(self.root, {"glob": "**/*.csproj"}, {})
        self.assertIn("csproj://WebApp", result.nodes)
        props = result.nodes["csproj://WebApp"]["props"]
        self.assertEqual(props["targetframework"], "net8.0")
        self.assertEqual(props["rootnamespace"], "SdkFixture.WebApp")


class SdkStyleAspNetRoutesTest(unittest.TestCase):
    """The fixture's controller + attributes yield route nodes."""

    def setUp(self) -> None:
        self.root = _fixture_root()
        if not self.root.is_dir():
            self.skipTest(f"Fixture not reachable at {self.root}")
        self.result = routes_extract(self.root, {"glob": "**/*.cs"}, {})

    def test_controller_symbol_emitted(self) -> None:
        # ``[ApiController]`` + ``[Route("api/[controller]")]`` on
        # ``ProductsController`` mints a single controller symbol with
        # the canonical id shape.
        controller_id = (
            "symbol:csharp:SdkFixture.WebApp.Controllers.ProductsController"
        )
        self.assertIn(controller_id, self.result.nodes)
        props = self.result.nodes[controller_id]["props"]
        self.assertEqual(props["kind"], "controller")

    def test_http_get_and_post_routes_emitted(self) -> None:
        # ``[HttpGet("{id}")]`` -> GET /api/products/{id}
        # ``[HttpPost]``        -> POST /api/products
        self.assertIn("route:GET:/api/products/{id}", self.result.nodes)
        self.assertIn("route:POST:/api/products", self.result.nodes)


class SdkStyleEfCoreTest(unittest.TestCase):
    """The fixture's DbContext + DbSet yield an entity node."""

    def setUp(self) -> None:
        self.root = _fixture_root()
        if not self.root.is_dir():
            self.skipTest(f"Fixture not reachable at {self.root}")
        self.result = efcore_extract(self.root, {"glob": "**/*.cs"}, {})

    def test_dbcontext_symbol_emitted(self) -> None:
        # The strategy keys DbContext discovery on ``: DbContext``.
        dbcontext_id = "symbol:csharp:SdkFixture.WebApp.Data.AppDbContext"
        self.assertIn(dbcontext_id, self.result.nodes)
        props = self.result.nodes[dbcontext_id]["props"]
        self.assertEqual(props["kind"], "dbcontext")

    def test_product_entity_emitted(self) -> None:
        # ``DbSet<Product> Products`` declares ``Product`` as an
        # entity; csharp_efcore mints an ``entity:`` node and a
        # ``contains`` edge from the DbContext symbol.
        self.assertIn("entity:Product", self.result.nodes)
        contains = [
            e for e in self.result.edges
            if e["type"] == "contains" and e["to"] == "entity:Product"
        ]
        self.assertTrue(
            contains,
            "expected at least one contains edge from DbContext to entity:Product",
        )
        # The from side is the DbContext symbol.
        self.assertEqual(
            contains[0]["from"],
            "symbol:csharp:SdkFixture.WebApp.Data.AppDbContext",
        )


if __name__ == "__main__":
    unittest.main()
