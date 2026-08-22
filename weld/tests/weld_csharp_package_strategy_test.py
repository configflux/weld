"""Regression: ``csharp_package`` strategy emits package -> file edges.

Closes the C# half of the gap that ``python_package`` already covers
(ADR 0041 § Layer 3 ``file-anchor-symmetry``). After the
``v0.19.1+f886ea6`` change the shared tree-sitter strategy mints
per-method ``symbol:cs:*`` nodes for every C# file (see
``_TREE_SITTER_EMIT_CALLS``). Those file anchors gain outgoing
``contains`` edges and immediately violate Layer 3 because no upstream
strategy emits ``package:csharp:* -> contains -> file:*``. ADR 0060
introduces ``csharp_package`` to anchor each C# file under the namespace
it declares.

The tests target the contract documented in ADR 0060:

- file-scoped ``namespace Foo.Bar.Baz;`` -> ``package:csharp:foo.bar.baz``
- block-scoped ``namespace Foo { ... }`` -> same shape
- multiple files in the same namespace coalesce on the same package node
- files with no detectable namespace are skipped (ADR 0060 explicitly
  scopes to namespaced sources; un-namespaced files remain on the
  Layer 3 entrypoint allow-list path)
- edges carry ``source_strategy=csharp_package`` and ``confidence=definite``
- repeated extracts produce byte-identical output (ADR 0012 § 3)
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


from weld.contract import validate_node  # noqa: E402
from weld.strategies.csharp_package import extract  # noqa: E402


def _make_tree(td: Path) -> None:
    """Build a fixture tree with file-scoped, block-scoped, and orphan files."""
    (td / "src" / "Sample.Web").mkdir(parents=True)
    (td / "src" / "Sample.Web" / "OrdersController.cs").write_text(
        "namespace Sample.Web.Controllers;\n"
        "public class OrdersController { }\n",
        encoding="utf-8",
    )
    (td / "src" / "Sample.Web" / "Program.cs").write_text(
        "namespace Sample.Web\n"
        "{\n"
        "    public static class Program { }\n"
        "}\n",
        encoding="utf-8",
    )
    (td / "src" / "Sample.Dal" / "Entities").mkdir(parents=True)
    (td / "src" / "Sample.Dal" / "Entities" / "Order.cs").write_text(
        "namespace Sample.Dal.Entities;\n"
        "public class Order { }\n",
        encoding="utf-8",
    )
    (td / "src" / "Sample.Dal" / "Entities" / "Customer.cs").write_text(
        "namespace Sample.Dal.Entities;\n"
        "public class Customer { }\n",
        encoding="utf-8",
    )
    (td / "no_namespace_dir").mkdir()
    (td / "no_namespace_dir" / "TopLevel.cs").write_text(
        "using System;\n"
        "public static class TopLevelHelper { }\n",
        encoding="utf-8",
    )


class CsharpPackageStrategyTest(unittest.TestCase):
    """``csharp_package.extract`` must mint namespace-keyed package anchors."""

    def test_file_scoped_namespace_node_emitted(self) -> None:
        """A file-scoped ``namespace X.Y.Z;`` becomes ``package:csharp:x.y.z``."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_tree(root)
            result = extract(root, {"glob": "src/**/*.cs"}, {})
        self.assertIn("package:csharp:sample.web.controllers", result.nodes)
        node = result.nodes["package:csharp:sample.web.controllers"]
        self.assertEqual(node["type"], "package")
        self.assertEqual(node["props"]["language"], "csharp")
        self.assertEqual(node["props"]["name"], "Sample.Web.Controllers")
        self.assertEqual(node["props"]["source_strategy"], "csharp_package")

    def test_block_scoped_namespace_node_emitted(self) -> None:
        """A block-scoped ``namespace X { ... }`` produces the same node shape."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_tree(root)
            result = extract(root, {"glob": "src/**/*.cs"}, {})
        self.assertIn("package:csharp:sample.web", result.nodes)
        node = result.nodes["package:csharp:sample.web"]
        self.assertEqual(node["props"]["name"], "Sample.Web")

    def test_multiple_files_share_one_package_node(self) -> None:
        """Two files in ``Sample.Dal.Entities`` coalesce on a single package node
        and each file gets its own ``contains`` edge (so anchors are symmetric)."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_tree(root)
            result = extract(root, {"glob": "src/**/*.cs"}, {})
        pkg_id = "package:csharp:sample.dal.entities"
        self.assertIn(pkg_id, result.nodes)
        targets = {
            e["to"] for e in result.edges
            if e["from"] == pkg_id and e["type"] == "contains"
        }
        # File IDs preserve the on-disk case of the path (vjxi.6); the
        # ``package:*`` ID continues to case-fold per ADR 0060.
        self.assertIn("file:src/Sample.Dal/Entities/Order", targets)
        self.assertIn("file:src/Sample.Dal/Entities/Customer", targets)

    def test_files_without_namespace_are_skipped(self) -> None:
        """A file with no ``namespace`` declaration must not produce any node
        or edge -- the existing entrypoint allow-list (ADR 0041 Rule 2) covers
        the legitimate top-level-statement case."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_tree(root)
            result = extract(root, {"glob": "no_namespace_dir/*.cs"}, {})
        self.assertEqual(result.nodes, {})
        self.assertEqual(result.edges, [])

    def test_contains_edge_metadata(self) -> None:
        """Every emitted edge must be a ``contains`` edge tagged with
        ``source_strategy=csharp_package`` and ``confidence=definite`` so
        ADR 0041 invariant attribution and the ADR 0050 confidence audit
        both classify it correctly."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_tree(root)
            result = extract(root, {"glob": "src/**/*.cs"}, {})
        self.assertGreater(len(result.edges), 0)
        for e in result.edges:
            self.assertEqual(e["type"], "contains")
            self.assertEqual(e["props"]["source_strategy"], "csharp_package")
            self.assertEqual(e["props"]["confidence"], "definite")

    def test_every_package_has_at_least_one_outgoing_contains(self) -> None:
        """ADR 0060 acceptance: every emitted ``package:csharp:<ns>`` node has
        at least one outbound ``contains -> file:*`` edge."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_tree(root)
            result = extract(root, {"glob": "src/**/*.cs"}, {})
        package_ids = {nid for nid in result.nodes if nid.startswith("package:csharp:")}
        self.assertGreater(len(package_ids), 0)
        for pid in package_ids:
            outgoing = [
                e for e in result.edges
                if e["from"] == pid and e["type"] == "contains"
                and e["to"].startswith("file:")
            ]
            self.assertGreater(
                len(outgoing), 0,
                f"package node {pid} must have at least one contains->file edge",
            )

    def test_origin_is_project(self) -> None:
        """Project namespaces (workspace-rooted) classify as ``origin=project``
        for the same reason ``python_package`` does -- the strategy only ever
        sees globs *inside* the workspace, so the discovered namespaces are
        first-party by construction. ADR 0042 require ``props.origin`` to be
        set explicitly; the legacy ``classify_node`` fallback must never
        run for a freshly-discovered graph."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_tree(root)
            result = extract(root, {"glob": "src/**/*.cs"}, {})
        for nid, node in result.nodes.items():
            if not nid.startswith("package:csharp:"):
                continue
            self.assertEqual(node["props"]["origin"], "project")

    def test_emitted_nodes_satisfy_the_contract(self) -> None:
        """Every node this strategy emits must pass ``validate_node``.

        Mirrors the ``python_package`` guard: this strategy stamps
        ``roles: ["package"]``, and while that value was absent from the
        contract vocabulary, discovering any C# repo produced a graph the
        product's own ``wd validate`` rejected.
        """
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_tree(root)
            result = extract(root, {"glob": "src/**/*.cs"}, {})
        self.assertTrue(result.nodes, "fixture must emit at least one node")
        for node_id, node in result.nodes.items():
            self.assertEqual(
                validate_node(node_id, node), [],
                f"{node_id} violates the node contract",
            )

    def test_determinism_repeated_runs_identical(self) -> None:
        """Two extract() calls on the same tree must produce byte-identical
        node and edge lists -- ADR 0012 § 3 graph determinism."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _make_tree(root)
            r1 = extract(root, {"glob": "src/**/*.cs"}, {})
            r2 = extract(root, {"glob": "src/**/*.cs"}, {})
        self.assertEqual(r1.nodes, r2.nodes)
        self.assertEqual(r1.edges, r2.edges)
        self.assertEqual(r1.discovered_from, r2.discovered_from)

    def test_empty_match_returns_empty(self) -> None:
        """A glob that matches nothing must return empty results, not raise."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            result = extract(root, {"glob": "nonexistent/*.cs"}, {})
        self.assertEqual(result.nodes, {})
        self.assertEqual(result.edges, [])

    def test_missing_glob_returns_empty(self) -> None:
        """A source with no ``glob`` is a no-op rather than a crash."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            result = extract(root, {}, {})
        self.assertEqual(result.nodes, {})
        self.assertEqual(result.edges, [])

    def test_unreadable_file_does_not_crash(self) -> None:
        """A glob match that fails to read (e.g. permission, encoding) must
        be treated as no-namespace rather than aborting the whole run."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "bad").mkdir()
            # Write invalid UTF-8: should still be readable with errors="ignore".
            (root / "bad" / "broken.cs").write_bytes(b"\xff\xfe\x00\x00garbage")
            result = extract(root, {"glob": "bad/*.cs"}, {})
        # No namespace -> no nodes, no crash.
        self.assertEqual(result.nodes, {})
        self.assertEqual(result.edges, [])

    def test_case_variant_namespaces_collapse_to_single_node(self) -> None:
        """Two C# files declaring the same namespace in different casing
        must coalesce on a single ``package:csharp:<lowercased>`` node.

        Regression: a discovery dogfood pass on a real C# repo surfaced
        ~130 case-variant ``package:csharp:*`` pairs. C# namespaces are
        case-INSENSITIVE (the BCL treats ``System`` and ``system`` as the
        same namespace), so the canonical-id contract (ADR 0041 Layer 1)
        lowercases them. This is the package-id complement of the
        symbol-id case decision: that decision preserves case for
        ``symbol:*`` IDs because most languages (including C#) treat
        ``SIZE`` and ``Size`` as legitimately distinct members of the same
        enclosing type, but namespaces are different.
        """
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src").mkdir()
            (root / "src" / "Upper.cs").write_text(
                "namespace System.Foo;\n"
                "public class Upper { }\n",
                encoding="utf-8",
            )
            (root / "src" / "Lower.cs").write_text(
                "namespace system.foo;\n"
                "public class Lower { }\n",
                encoding="utf-8",
            )
            result = extract(root, {"glob": "src/**/*.cs"}, {})

        package_ids = {nid for nid in result.nodes if nid.startswith("package:csharp:")}
        self.assertEqual(
            package_ids,
            {"package:csharp:system.foo"},
            "case-variant C# namespaces must collapse to a single canonical "
            "package node",
        )
        # Both files must be anchored under the single canonical node.
        targets = {
            e["to"] for e in result.edges
            if e["from"] == "package:csharp:system.foo"
            and e["type"] == "contains"
        }
        # File IDs preserve case (vjxi.6). The package ID is still
        # collapsed (ADR 0060). The on-disk file names ``Upper.cs`` and
        # ``Lower.cs`` mint distinct file IDs anchored on the single
        # canonical package node.
        self.assertIn("file:src/Upper", targets)
        self.assertIn("file:src/Lower", targets)


if __name__ == "__main__":
    unittest.main()
