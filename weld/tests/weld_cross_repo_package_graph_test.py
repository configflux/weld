"""Tests for the :mod:`weld.cross_repo.package_graph` resolver.

The ``package_graph`` resolver joins *manifest-declared* package
dependencies -- C# ``<PackageReference>``, Python ``pyproject.toml``
``[project].dependencies``, and ``go.mod`` ``require`` -- in a consuming
child repository to the sibling child that *produces* that package. This
is the "schema library consumed via a package dependency" polyrepo shape
that neither ``service_graph`` (URL host matching) nor ``channel_binding``
(topic matching) covers (field-eval v0.23.1, Finding 06 "Related").

Unlike ``package_import_resolver`` -- which joins *import evidence*
(``imports_from`` from ``using`` / ``import`` statements) to
``type=package`` producer nodes inside a child graph -- this resolver
reads the *manifests on disk* via ``ResolverContext.workspace_root`` and
``workspaces.yaml`` child paths (the same disk-reading pattern as
``compose_topology``) and emits an edge to the producing *repo node*.

The two assertions that pin the field-eval fixture are in
:class:`FixtureShapeTest`: a C# consumer and a Python consumer of the
same schema library both resolve to the producing repo.
"""

from __future__ import annotations

import os
import tempfile
import unittest

from weld.cross_repo import (
    CrossRepoEdge,
    ResolverContext,
    get_resolver,
    resolver_names,
    run_resolvers,
)
from weld.cross_repo.package_graph import PackageGraphResolver


def _repo_node(child: str) -> str:
    """Return the repo-node id the resolver emits edges between.

    Hand-spelled rather than built with ``repo_node_id``: this is the wire
    format ``federation_root`` mints and every reader resolves against (ADR
    0137 ss1), so the expectation has to be independent of the helper under
    test. The namespaced ``<child>\\x1frepo:<child>`` this replaced was in
    neither id space, and because the test built it the same wrong way the
    resolver did, nine green assertions described a graph whose every edge
    dangled.
    """
    return f"repo:{child}"


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def _context(root: str, children: dict[str, object]) -> ResolverContext:
    """Build a context whose children mapping declares only present repos.

    The resolver reads manifests from disk, so ``children`` only needs to
    carry the *set of present child names* (values are unused by the
    resolver's manifest scan); we pass ``None`` graphs to make that
    explicit.
    """
    return ResolverContext(
        workspace_root=root,
        cross_repo_strategies=["package_graph"],
        children=children,
        child_hashes={name: "" for name in children},
    )


def _workspaces_yaml(children: list[tuple[str, str]]) -> str:
    lines = ["version: 1", "children:"]
    for name, path in children:
        lines.append(f"  - name: {name}")
        lines.append(f"    path: {path}")
    lines.append("cross_repo_strategies: [package_graph]")
    return "\n".join(lines) + "\n"


class RegistrationTest(unittest.TestCase):
    def test_registered_under_package_graph_name(self) -> None:
        self.assertIn("package_graph", resolver_names())
        # ``get_resolver`` returns the registered *class*, not an instance.
        self.assertIs(get_resolver("package_graph"), PackageGraphResolver)


class PythonDependencyTest(unittest.TestCase):
    def test_pyproject_dependency_joins_to_pyproject_producer(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            _write(
                os.path.join(root, ".weld", "workspaces.yaml"),
                _workspaces_yaml(
                    [("schema", "libs/schema"), ("svc", "services/svc")]
                ),
            )
            _write(
                os.path.join(root, "libs/schema/pyproject.toml"),
                '[project]\nname = "order-schema"\nversion = "1.0.0"\n',
            )
            _write(
                os.path.join(root, "services/svc/pyproject.toml"),
                '[project]\nname = "svc"\n'
                'dependencies = ["order-schema>=1.0.0", "requests"]\n',
            )
            ctx = _context(root, {"schema": None, "svc": None})
            edges = PackageGraphResolver().resolve(ctx)
        self.assertEqual(
            [(e.from_id, e.to_id, e.type) for e in edges],
            [(_repo_node("svc"), _repo_node("schema"), "cross_repo:depends_on")],
        )
        # The unmatched "requests" dependency (no sibling produces it) is
        # silently dropped, not fabricated into an edge.
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0].props["package"], "order-schema")
        self.assertEqual(edges[0].props["source_strategy"], "package_graph")


class CSharpDependencyTest(unittest.TestCase):
    def test_packagereference_joins_via_proto_derived_producer(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            _write(
                os.path.join(root, ".weld", "workspaces.yaml"),
                _workspaces_yaml(
                    [("schema", "libs/schema"), ("gw", "services/gw")]
                ),
            )
            # Producer: proto package -> produces Acme.Platform.Order.Schema
            _write(
                os.path.join(root, "libs/schema/proto/event.proto"),
                "syntax = \"proto3\";\npackage acme.platform.order.schema.v1;\n",
            )
            # Consumer: csproj PackageReference (case differs from proto)
            _write(
                os.path.join(root, "services/gw/Gateway.csproj"),
                "<Project>\n <ItemGroup>\n"
                '  <PackageReference Include="Acme.Platform.Order.Schema" '
                'Version="1.0.0" />\n'
                " </ItemGroup>\n</Project>\n",
            )
            ctx = _context(root, {"schema": None, "gw": None})
            edges = PackageGraphResolver().resolve(ctx)
        self.assertEqual(
            [(e.from_id, e.to_id) for e in edges],
            [(_repo_node("gw"), _repo_node("schema"))],
        )


class MsbuildProducerTest(unittest.TestCase):
    """A .NET producer joins: field-eval v0.25.0 M4, bd lcq0c.4, ADR 0141 D2.

    The proto case above is the *other* producer-declaration style; here both
    ends are MSBuild, which is the shape that emitted nothing at all -- the
    library's package name lives only in its project file, so the resolver
    read a consumer with no sibling to point at.
    """

    def _resolve(self, producer_project: str, **properties: str) -> list:
        """Resolve a two-child workspace whose producer is *producer_project*.

        *properties* are written into that project's ``<PropertyGroup>``, so
        the cases differ only in what the producing project declares.
        """
        declared = "".join(
            f"  <{tag}>{value}</{tag}>\n" for tag, value in properties.items()
        )
        with tempfile.TemporaryDirectory() as root:
            _write(
                os.path.join(root, ".weld", "workspaces.yaml"),
                _workspaces_yaml(
                    [("billing", "libs/billing"), ("gw", "services/gw")]
                ),
            )
            _write(
                os.path.join(root, "libs/billing", producer_project),
                '<Project Sdk="Microsoft.NET.Sdk">\n <PropertyGroup>\n'
                + declared
                + " </PropertyGroup>\n</Project>\n",
            )
            # Consumer: the reference is spelled exactly as the producer
            # declares it, in the case a .NET consumer would use.
            _write(
                os.path.join(root, "services/gw/Gateway.csproj"),
                "<Project>\n <ItemGroup>\n"
                '  <PackageReference Include="Acme.Platform.Billing.Schema" '
                'Version="1.0.0" />\n'
                " </ItemGroup>\n</Project>\n",
            )
            ctx = _context(root, {"billing": None, "gw": None})
            return PackageGraphResolver().resolve(ctx)

    def test_package_id_producer_joins_a_packagereference_consumer(self) -> None:
        edges = self._resolve(
            "src/Billing.csproj", PackageId="Acme.Platform.Billing.Schema"
        )
        self.assertEqual(
            [(e.from_id, e.to_id, e.props["package"]) for e in edges],
            [
                (
                    _repo_node("gw"),
                    _repo_node("billing"),
                    "Acme.Platform.Billing.Schema",
                )
            ],
        )

    def test_a_producer_named_only_by_its_project_filename_joins(self) -> None:
        """No ``<PackageId>``: the filename is NuGet's default package name."""
        edges = self._resolve("src/Acme.Platform.Billing.Schema.csproj")
        self.assertEqual(
            [(e.from_id, e.to_id) for e in edges],
            [(_repo_node("gw"), _repo_node("billing"))],
        )

    def test_an_unpackable_project_produces_no_join(self) -> None:
        """``IsPackable=false`` says the filename default does not apply."""
        edges = self._resolve(
            "src/Acme.Platform.Billing.Schema.csproj", IsPackable="false"
        )
        self.assertEqual(edges, [])


class GoDependencyTest(unittest.TestCase):
    def test_gomod_require_joins_to_gomod_module_producer(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            _write(
                os.path.join(root, ".weld", "workspaces.yaml"),
                _workspaces_yaml(
                    [("schema", "libs/schema"), ("svc", "services/svc")]
                ),
            )
            _write(
                os.path.join(root, "libs/schema/go.mod"),
                "module github.com/acme/order-schema\n\ngo 1.21\n",
            )
            _write(
                os.path.join(root, "services/svc/go.mod"),
                "module github.com/acme/svc\n\ngo 1.21\n\n"
                "require (\n"
                "\tgithub.com/acme/order-schema v1.0.0\n"
                "\tgithub.com/pkg/errors v0.9.1\n"
                ")\n",
            )
            ctx = _context(root, {"schema": None, "svc": None})
            edges = PackageGraphResolver().resolve(ctx)
        self.assertEqual(
            [(e.from_id, e.to_id) for e in edges],
            [(_repo_node("svc"), _repo_node("schema"))],
        )


class GoManifestParseTest(unittest.TestCase):
    def test_mixed_single_and_block_require_no_phantom_paren(self) -> None:
        from weld.cross_repo._package_manifest_scan import scan_child_manifests

        with tempfile.TemporaryDirectory() as root:
            _write(
                os.path.join(root, "go.mod"),
                "module example.com/m\n\ngo 1.21\n\n"
                "require example.com/single v1.0.0\n\n"
                "require (\n"
                "\texample.com/a v1.0.0\n"
                "\texample.com/b v2.0.0 // indirect\n"
                ")\n",
            )
            produced, consumed = scan_child_manifests(root)
        self.assertEqual(produced, {"example.com/m"})
        # The block opener "(" must never be captured as a module path,
        # and the "// indirect" comment must be stripped.
        self.assertEqual(
            consumed,
            {"example.com/single", "example.com/a", "example.com/b"},
        )


class NoSelfEdgeTest(unittest.TestCase):
    def test_consumer_that_declares_own_produced_name_gets_no_self_edge(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as root:
            _write(
                os.path.join(root, ".weld", "workspaces.yaml"),
                _workspaces_yaml([("solo", "libs/solo")]),
            )
            # A repo that both declares and depends on its own name must
            # not manufacture a self-edge.
            _write(
                os.path.join(root, "libs/solo/pyproject.toml"),
                '[project]\nname = "solo"\ndependencies = ["solo"]\n',
            )
            ctx = _context(root, {"solo": None})
            edges = PackageGraphResolver().resolve(ctx)
        self.assertEqual(edges, [])


class MissingChildIgnoredTest(unittest.TestCase):
    def test_dependency_on_absent_child_produces_no_edge(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            _write(
                os.path.join(root, ".weld", "workspaces.yaml"),
                _workspaces_yaml([("svc", "services/svc")]),
            )
            _write(
                os.path.join(root, "services/svc/pyproject.toml"),
                '[project]\nname = "svc"\ndependencies = ["order-schema"]\n',
            )
            # No producer child declares order-schema -> no edge, no error.
            ctx = _context(root, {"svc": None})
            edges = PackageGraphResolver().resolve(ctx)
        self.assertEqual(edges, [])


class NoWorkspacesYamlTest(unittest.TestCase):
    def test_missing_workspaces_yaml_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            ctx = _context(root, {"a": None})
            self.assertEqual(PackageGraphResolver().resolve(ctx), [])


class DeterminismTest(unittest.TestCase):
    def test_output_is_byte_stable_across_runs(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            _write(
                os.path.join(root, ".weld", "workspaces.yaml"),
                _workspaces_yaml(
                    [
                        ("schema", "libs/schema"),
                        ("a", "services/a"),
                        ("b", "services/b"),
                    ]
                ),
            )
            _write(
                os.path.join(root, "libs/schema/pyproject.toml"),
                '[project]\nname = "order-schema"\n',
            )
            for svc in ("a", "b"):
                _write(
                    os.path.join(root, f"services/{svc}/pyproject.toml"),
                    f'[project]\nname = "{svc}"\n'
                    'dependencies = ["order-schema"]\n',
                )
            ctx = _context(root, {"schema": None, "a": None, "b": None})
            first = PackageGraphResolver().resolve(ctx)
            second = PackageGraphResolver().resolve(ctx)
        self.assertEqual(
            [e.to_dict() for e in first],
            [e.to_dict() for e in second],
        )
        # Sorted by (from, to): a before b.
        self.assertEqual(
            [e.from_id for e in first],
            [_repo_node("a"), _repo_node("b")],
        )


class OrchestratorTest(unittest.TestCase):
    def test_run_resolvers_wires_package_graph_by_name(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            _write(
                os.path.join(root, ".weld", "workspaces.yaml"),
                _workspaces_yaml(
                    [("schema", "libs/schema"), ("svc", "services/svc")]
                ),
            )
            _write(
                os.path.join(root, "libs/schema/pyproject.toml"),
                '[project]\nname = "order-schema"\n',
            )
            _write(
                os.path.join(root, "services/svc/pyproject.toml"),
                '[project]\nname = "svc"\ndependencies = ["order-schema"]\n',
            )
            ctx = _context(root, {"schema": None, "svc": None})
            # The orchestrator reads the strategy list off the context.
            edges = run_resolvers(ctx)
        self.assertTrue(
            any(
                isinstance(e, CrossRepoEdge)
                and e.type == "cross_repo:depends_on"
                for e in edges
            )
        )


if __name__ == "__main__":
    unittest.main()
