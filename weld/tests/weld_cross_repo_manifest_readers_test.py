"""One reader per ecosystem: what each manifest family contributes.

``package_graph`` joins a dependency declared in one child repo to the sibling
that *produces* that package. Which names a manifest contributes -- produced,
consumed, or both -- is decided by a per-ecosystem registry of readers in
:mod:`weld.cross_repo._manifest_readers` (ADR 0141 D2), and this file holds
each registry entry's own contract. The *boundary* question -- which files a
reader may be handed at all -- belongs to
:mod:`weld.cross_repo._package_manifest_scan` and to the sibling
``weld_cross_repo_manifest_scan_boundary_test``.

Field-eval v0.25.0 finding M4 (bd lcq0c.4) is what a registry entry with no
producer half costs: ``.csproj`` contributed ``<PackageReference>`` and nothing
else, so no C#-only library could ever be joined *to*, and the evaluator's
34-repo .NET workspace surfaced three of twelve consuming repos while reading
as correct. The guard against a repeat is structural rather than a comment:
:meth:`RegistryCoverageTest.test_every_registered_ecosystem_has_a_case`
compares the registry against the case table below, so a reader cannot join it
without declaring here what it produces, and
:meth:`RegistryCoverageTest.test_no_ecosystem_is_consumer_only` forbids
declaring that as nothing.

Every assertion reads the ``(produced, consumed)`` pair back from
``scan_child_manifests`` -- the entry point the resolver itself calls -- rather
than from a reader invoked directly (ADR 0139 mechanism 1): dispatch is half of
what a registry does, and a test that steps over it cannot see a reader wired
to the wrong filename.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import NamedTuple

from weld.cross_repo._manifest_readers import MANIFEST_READERS
from weld.cross_repo._package_manifest_scan import scan_child_manifests

_CSPROJ = """\
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0</TargetFramework>
{properties}  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="Acme.Platform.Order.Schema" Version="1.0.0" />
  </ItemGroup>
</Project>
"""


def _csproj(**properties: str) -> str:
    """An SDK-style project file carrying *properties* and one reference."""
    return _CSPROJ.format(
        properties="".join(
            f"    <{tag}>{value}</{tag}>\n" for tag, value in properties.items()
        )
    )


def _package_json(**fields: object) -> str:
    """A ``package.json`` carrying exactly *fields*, in declaration order."""
    return json.dumps(fields, indent=2) + "\n"


class _Case(NamedTuple):
    """One ecosystem's minimal manifest, and the names it declares."""

    files: dict[str, str]
    produced: set[str]
    consumed: set[str]


#: One entry per registry ecosystem. Each manifest declares a producer name and
#: (where the format has a dependency section) a consumer name, so the pair
#: read back covers both halves of what that ecosystem contributes.
_ECOSYSTEM_CASES: dict[str, _Case] = {
    "python": _Case(
        {
            "pyproject.toml": (
                '[project]\nname = "order-schema"\n'
                'dependencies = ["billing-schema>=1.0", "requests"]\n'
            )
        },
        {"order-schema"},
        {"billing-schema", "requests"},
    ),
    "go": _Case(
        {
            "go.mod": (
                "module github.com/acme/order-schema\n\ngo 1.21\n\n"
                "require github.com/acme/billing-schema v1.0.0\n"
            )
        },
        {"github.com/acme/order-schema"},
        {"github.com/acme/billing-schema"},
    ),
    "msbuild": _Case(
        {"src/Billing.csproj": _csproj(PackageId="Acme.Platform.Billing.Schema")},
        {"Acme.Platform.Billing.Schema"},
        {"Acme.Platform.Order.Schema"},
    ),
    # A ``.proto`` declares only what it publishes: the generated-code package
    # name its consumers spell, minus the API-version tail.
    "protobuf": _Case(
        {
            "proto/event.proto": (
                'syntax = "proto3";\n\npackage acme.platform.order.schema.v1;\n'
            )
        },
        {"acme.platform.order.schema"},
        set(),
    ),
    # npm publishes under the manifest's ``name`` and consumes its *runtime*
    # ``dependencies``. The dev half sits in the same file and is deliberately
    # not an edge (ADR 0142 D5), which is why this case carries one.
    "npm": _Case(
        {
            "package.json": _package_json(
                name="@acme/ui-kit",
                version="1.2.0",
                dependencies={"@acme/tokens": "^2.0.0"},
                devDependencies={"vitest": "2.1.4"},
            )
        },
        {"@acme/ui-kit"},
        {"@acme/tokens"},
    ),
}


def _scan(files: dict[str, str]) -> tuple[set[str], set[str]]:
    """Write *files* into a throwaway child repo and scan it.

    Not a git repository, so the scan takes the excluded-directory fallback --
    the route with no filtering relevant to these manifests, which keeps each
    case about the reader rather than about the boundary.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "child"
        for rel, body in files.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        return scan_child_manifests(str(root))


class RegistryCoverageTest(unittest.TestCase):
    """The registry and this file's case table describe the same ecosystems."""

    def test_every_registered_ecosystem_has_a_case(self) -> None:
        self.assertEqual(
            {reader.ecosystem for reader in MANIFEST_READERS},
            set(_ECOSYSTEM_CASES),
            "a manifest reader joined the registry without a case here -- "
            "declare what it produces and consumes (ADR 0141 D2)",
        )

    def test_no_ecosystem_is_consumer_only(self) -> None:
        """The M4 shape itself: a reader that never yields a producer name.

        Without this, the coverage check above is satisfiable by declaring an
        empty produced set -- which is exactly the state ``.csproj`` shipped in
        (bd lcq0c.4).
        """
        for ecosystem, case in sorted(_ECOSYSTEM_CASES.items()):
            self.assertTrue(
                case.produced,
                f"{ecosystem} declares no produced name; a manifest family "
                "weld cannot read a producer from can never be joined to",
            )


class EcosystemReaderTest(unittest.TestCase):
    def test_each_ecosystem_yields_the_names_its_manifest_declares(self) -> None:
        for ecosystem, case in sorted(_ECOSYSTEM_CASES.items()):
            with self.subTest(ecosystem=ecosystem):
                produced, consumed = _scan(case.files)
                self.assertEqual(produced, case.produced)
                self.assertEqual(consumed, case.consumed)


class MsbuildProducerTest(unittest.TestCase):
    """Where an MSBuild project's published name comes from (ADR 0141 D2).

    ``<PackageId>`` when the project states one, else the project filename --
    NuGet's own default for ``dotnet pack``, and the only other place the name
    exists.
    """

    def test_package_id_is_the_produced_name(self) -> None:
        produced, _consumed = _scan(
            {"src/Billing.csproj": _csproj(PackageId="Acme.Platform.Billing.Schema")}
        )
        self.assertEqual(produced, {"Acme.Platform.Billing.Schema"})

    def test_absent_package_id_falls_back_to_the_project_filename(self) -> None:
        produced, consumed = _scan(
            {"src/Acme.Platform.Billing.Schema.csproj": _csproj()}
        )
        self.assertEqual(produced, {"Acme.Platform.Billing.Schema"})
        self.assertEqual(consumed, {"Acme.Platform.Order.Schema"})

    def test_an_unexpanded_property_reference_falls_back_to_the_filename(
        self,
    ) -> None:
        """``<PackageId>$(AssemblyName)</PackageId>`` is not a package name.

        MSBuild properties are not evaluated here, and emitting the literal
        would lose the edge the default -- which is what ``$(AssemblyName)``
        resolves to in this project -- would have formed.
        """
        produced, _consumed = _scan(
            {"src/Billing.csproj": _csproj(PackageId="$(AssemblyName)")}
        )
        self.assertEqual(produced, {"Billing"})

    def test_is_packable_false_produces_nothing_and_still_consumes(self) -> None:
        """A project that says it publishes no package is not a producer.

        The filename fallback is a default, and this is the one property that
        declares the default wrong: an application or test project is not the
        producer of a package named after its own project file.
        """
        produced, consumed = _scan(
            {"src/OrderGateway.csproj": _csproj(IsPackable="false")}
        )
        self.assertEqual(produced, set())
        self.assertEqual(consumed, {"Acme.Platform.Order.Schema"})

    def test_is_packable_false_overrides_an_explicit_package_id(self) -> None:
        produced, _consumed = _scan(
            {
                "src/Billing.csproj": _csproj(
                    PackageId="Acme.Platform.Billing.Schema", IsPackable="False"
                )
            }
        )
        self.assertEqual(produced, set())

    def test_every_project_file_contributes_its_own_name(self) -> None:
        """A repo publishing two packages produces both.

        One repo, one package is a convention, not a rule; the scan is per
        manifest and the produced set is a union.
        """
        produced, _consumed = _scan(
            {
                "src/Billing/Billing.csproj": _csproj(PackageId="Acme.Billing"),
                "src/Invoicing/Invoicing.csproj": _csproj(),
            }
        )
        self.assertEqual(produced, {"Acme.Billing", "Invoicing"})

    def test_an_unparseable_project_file_contributes_nothing(self) -> None:
        """One bad manifest must not sink the scan -- or fabricate a producer.

        The filename fallback is tempting here and is wrong: a file weld could
        not read is not evidence that this repo publishes a package by that
        name.
        """
        produced, consumed = _scan({"src/Broken.csproj": "<Project><oops>\n"})
        self.assertEqual((produced, consumed), (set(), set()))


class NpmProducerTest(unittest.TestCase):
    """What a ``package.json`` declares, and what it deliberately does not.

    ADR 0142 D5: the ``name`` produces, runtime ``dependencies`` consume, and
    workspace-internal names are not cross-repo facts.
    """

    def test_dev_peer_and_optional_dependencies_are_not_consumed(self) -> None:
        """Only the runtime section is a dependency on a sibling repo.

        A dev dependency is a build-time tool; a peer or optional one is a
        contract with whoever installs *this* package rather than something
        this repo pulls in. An edge from any of the three would assert a
        run-time dependency the manifest does not declare. ADR 0142 D5 decides
        the dev half; peer and optional are held to the same line here so that
        widening it stays a decision rather than a drift.
        """
        produced, consumed = _scan({
            "package.json": _package_json(
                name="@acme/storefront",
                dependencies={"@acme/ui-kit": "^1.2.0"},
                devDependencies={"@acme/test-utils": "^1.0.0"},
                peerDependencies={"react": "^18.0.0"},
                optionalDependencies={"@acme/telemetry": "^1.0.0"},
            )
        })
        self.assertEqual(produced, {"@acme/storefront"})
        self.assertEqual(consumed, {"@acme/ui-kit"})

    def test_a_private_package_produces_nothing_and_still_consumes(self) -> None:
        """``"private": true`` is npm's own "this is never published".

        Every workspace root and most applications carry it, and their names
        exist to label a directory -- no sibling repo can depend on one, so
        crediting it as a producer is the fabricated-edge shape of finding N2
        under a different filename. The npm analogue of the
        ``<IsPackable>false</IsPackable>`` guard above.
        """
        produced, consumed = _scan({
            "package.json": _package_json(
                name="acme-web-platform",
                private=True,
                workspaces=["packages/*"],
                dependencies={"@acme/ui-kit": "^1.2.0"},
            )
        })
        self.assertEqual(produced, set())
        self.assertEqual(consumed, {"@acme/ui-kit"})

    def test_a_workspace_internal_dependency_stays_inside_one_repo(self) -> None:
        """A member depending on a sibling member is not a cross-repo fact.

        One repo publishing several packages is what npm workspaces are for,
        so the produced set is a union over the members. Both names then come
        back from the *same* child, which is the precondition of the
        resolver's no-self-edge rule (``package_graph`` emits nothing when
        producer and consumer are one child --
        ``weld_cross_repo_package_graph_test``). That the join closes inside
        the repo is what makes it somebody else's fact, not this resolver's.
        """
        produced, consumed = _scan({
            "package.json": _package_json(
                name="acme-web-platform", private=True, workspaces=["*/*"]
            ),
            "packages/ui-kit/package.json": _package_json(name="@acme/ui-kit"),
            "apps/web/package.json": _package_json(
                name="@acme/web", dependencies={"@acme/ui-kit": "^1.2.0"}
            ),
        })
        self.assertEqual(produced, {"@acme/ui-kit", "@acme/web"})
        self.assertEqual(consumed, {"@acme/ui-kit"})
        self.assertLessEqual(
            consumed, produced, "the join closes inside this one repo"
        )

    def test_a_manifest_weld_cannot_read_contributes_nothing(self) -> None:
        """Unreadable, un-JSON, or JSON of the wrong shape: nothing either way.

        ``[]`` and ``"@acme/ui-kit"`` are valid JSON and not manifests, and a
        ``name`` that is not a string is not a package name. A repo weld scans
        is somebody else's; nothing here may assume it is sane.
        """
        bodies = (
            '{"name": "@acme/ui-kit",\n',
            "[]",
            "null",
            '"@acme/ui-kit"',
            '{"name": 42, "dependencies": ["@acme/ui-kit"]}',
            '{"name": "   ", "dependencies": {"": "^1.0.0"}}',
        )
        for body in bodies:
            with self.subTest(body=body):
                self.assertEqual(_scan({"package.json": body}), (set(), set()))


class PathologicalManifestTest(unittest.TestCase):
    """The module's contract under input nobody typed by hand.

    A child repo is somebody else's tree, and the scan promises that a
    manifest it cannot make sense of contributes nothing *rather than
    raising*. Nesting is where that promise is thinnest: ``json`` and
    ``tomllib`` both recurse per level and raise ``RecursionError``, which is
    no ``ValueError`` and so walks straight through a decode-error guard --
    and not as one bad manifest either, since the resolver framework catches
    per *resolver*: one file like this silently deletes every cross-repo edge
    in the workspace.
    """

    #: An order of magnitude past CPython's default recursion limit, so the
    #: case does not quietly stop reproducing if that limit is raised a little.
    _DEPTH = 20_000

    def test_deep_nesting_contributes_nothing_rather_than_raising(self) -> None:
        nest = "[" * self._DEPTH + "]" * self._DEPTH
        for name, body in (
            ("package.json", nest),
            ("pyproject.toml", f"a = {nest}"),
        ):
            with self.subTest(manifest=name):
                self.assertEqual(_scan({name: body}), (set(), set()))


if __name__ == "__main__":
    unittest.main()
