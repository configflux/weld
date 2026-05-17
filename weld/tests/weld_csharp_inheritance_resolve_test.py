"""Tests for the C# external-base FQN resolver.

The ShareX dogfood pass (2026-05-15) showed five distinct
``symbol:csharp:<consumingNamespace>.Form`` placeholders for the same
``System.Windows.Forms.Form`` external type. Each consuming namespace
minted its own placeholder, breaking cross-namespace reachability
("who inherits from Form?") and polluting search results.

The fix (see :mod:`weld.strategies._csharp_inheritance_resolve`):

- When a bare base name does not resolve to a project file AND the file
  declares exactly one external/stdlib ``using`` directive, the
  resolver uses that namespace as the FQN prefix
  (``symbol:csharp:<using-ns>.<Bare>``).
- When multiple external/stdlib usings are ambiguous, fall back to a
  deterministic ``symbol:csharp:_external:<Bare>`` single-node bucket.
- When no external/stdlib usings are visible, keep the legacy
  consuming-namespace fallback for back-compat with same-namespace
  partial-class fixtures.

These tests pin both the unit-level resolution path
(:func:`emit_base_edges` with explicit imports) and the end-to-end
discovery path (multi-namespace fixture with the same external base).
"""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from weld.strategies._csharp_inheritance import emit_base_edges


class EmitBaseEdgesWithUsingsTest(unittest.TestCase):
    """Unit-level resolution from the ``imports`` parameter."""

    def test_bare_base_with_single_external_using_resolves_to_using_namespace(
        self,
    ) -> None:
        """Two files in different consuming namespaces that both
        ``using System.Windows.Forms;`` and inherit from ``Form`` must
        converge on ``symbol:csharp:System.Windows.Forms.Form``."""
        nodes: dict = {}
        edges: list = []
        emit_base_edges(
            nodes,
            edges,
            file_node_id="file:src/HelpersLib/Foo",
            namespace="ShareX.HelpersLib",
            derived_class="Foo",
            base_name="Form",
            source_strategy="tree_sitter",
            project_file_index={},
            imports=["System.Windows.Forms"],
        )
        emit_base_edges(
            nodes,
            edges,
            file_node_id="file:src/HistoryLib/Bar",
            namespace="ShareX.HistoryLib.Forms",
            derived_class="Bar",
            base_name="Form",
            source_strategy="tree_sitter",
            project_file_index={},
            imports=["System.Windows.Forms"],
        )
        canonical = "symbol:csharp:System.Windows.Forms.Form"
        self.assertIn(canonical, nodes)
        # No per-consuming-namespace fan-out -- the bug we are fixing.
        self.assertNotIn("symbol:csharp:ShareX.HelpersLib.Form", nodes)
        self.assertNotIn("symbol:csharp:ShareX.HistoryLib.Forms.Form", nodes)
        self.assertEqual(edges[0]["to"], canonical)
        self.assertEqual(edges[1]["to"], canonical)

    def test_bare_base_with_multiple_external_usings_falls_back_to_bucket(
        self,
    ) -> None:
        """Ambiguous bare bases land in a deterministic
        ``_external:<Bare>`` single-node bucket.

        When more than one external/stdlib ``using`` could plausibly
        own the bare name and there is no project-file match, the
        resolver cannot pick a unique FQN. The placeholder must still
        collapse to ONE node per bare name (not per consuming
        namespace) so the graph stays clean.
        """
        nodes: dict = {}
        edges: list = []
        emit_base_edges(
            nodes,
            edges,
            file_node_id="file:src/Alpha/Foo",
            namespace="App.Alpha",
            derived_class="Foo",
            base_name="EventArgs",
            source_strategy="tree_sitter",
            project_file_index={},
            imports=["System", "System.ComponentModel"],
        )
        emit_base_edges(
            nodes,
            edges,
            file_node_id="file:src/Beta/Bar",
            namespace="App.Beta",
            derived_class="Bar",
            base_name="EventArgs",
            source_strategy="tree_sitter",
            project_file_index={},
            imports=["System", "System.ComponentModel"],
        )
        bucket = "symbol:csharp:_external:EventArgs"
        self.assertIn(bucket, nodes)
        self.assertEqual(nodes[bucket]["props"]["origin"], "external")
        self.assertEqual(nodes[bucket]["props"]["kind"], "base_reference")
        # No per-namespace fan-out: neither consuming namespace minted a
        # private placeholder.
        self.assertNotIn("symbol:csharp:App.Alpha.EventArgs", nodes)
        self.assertNotIn("symbol:csharp:App.Beta.EventArgs", nodes)
        self.assertEqual(edges[0]["to"], bucket)
        self.assertEqual(edges[1]["to"], bucket)

    def test_bare_base_without_external_usings_keeps_consuming_namespace(
        self,
    ) -> None:
        """Back-compat: when the file declares no external/stdlib
        usings, a bare base attaches to the consuming namespace.

        Preserves the existing partial-class test fixtures (and the
        common in-project pattern where the base lives in the same
        namespace and the using would be redundant).
        """
        nodes: dict = {}
        edges: list = []
        emit_base_edges(
            nodes,
            edges,
            file_node_id="file:src/Foo",
            namespace="Sample.Api",
            derived_class="Foo",
            base_name="Bar",
            source_strategy="tree_sitter",
            project_file_index={},
            imports=[],
        )
        self.assertIn("symbol:csharp:Sample.Api.Bar", nodes)
        self.assertEqual(edges[0]["to"], "symbol:csharp:Sample.Api.Bar")

    def test_dotted_base_keeps_declared_prefix_regardless_of_usings(
        self,
    ) -> None:
        """Dotted bases (``System.IDisposable``) keep their declared FQN
        even when the file has external usings -- no risk of fan-out
        because the name is already canonical.
        """
        nodes: dict = {}
        edges: list = []
        emit_base_edges(
            nodes,
            edges,
            file_node_id="file:src/Foo",
            namespace="App.Alpha",
            derived_class="Foo",
            base_name="System.IDisposable",
            source_strategy="tree_sitter",
            project_file_index={},
            imports=["System.Windows.Forms"],
        )
        self.assertIn("symbol:csharp:System.IDisposable", nodes)
        # No accidental consuming-namespace bleed.
        self.assertNotIn("symbol:csharp:App.Alpha.IDisposable", nodes)


class MultiNamespaceFanOutRegressionTest(unittest.TestCase):
    """End-to-end regression for the external-base FQN fan-out bug."""

    def test_two_files_in_different_namespaces_share_one_form_node(
        self,
    ) -> None:
        """Two files in different namespaces both inherit ``Form`` via
        ``using System.Windows.Forms;`` -- must mint exactly ONE
        base_reference node, with both inherits edges targeting it.

        This exercises the full discovery pipeline (record at
        per-file enrich time, then resolve in the post-pass).
        """
        from weld.strategies import tree_sitter

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "src"
            src.mkdir()
            helpers_lib = src / "HelpersLib"
            helpers_lib.mkdir()
            history_lib = src / "HistoryLib"
            history_lib.mkdir()
            (helpers_lib / "MyForm.cs").write_text(
                textwrap.dedent("""\
                    using System.Windows.Forms;

                    namespace ShareX.HelpersLib;

                    public class MyForm : Form {
                    }
                """),
                encoding="utf-8",
            )
            (history_lib / "HistoryForm.cs").write_text(
                textwrap.dedent("""\
                    using System.Windows.Forms;

                    namespace ShareX.HistoryLib.Forms;

                    public class HistoryForm : Form {
                    }
                """),
                encoding="utf-8",
            )

            symbols_by_file = {
                "MyForm.cs": {
                    "exports": ["MyForm"],
                    "classes": ["MyForm"],
                    "imports": ["System.Windows.Forms"],
                    "namespaces": ["ShareX.HelpersLib"],
                },
                "HistoryForm.cs": {
                    "exports": ["HistoryForm"],
                    "classes": ["HistoryForm"],
                    "imports": ["System.Windows.Forms"],
                    "namespaces": ["ShareX.HistoryLib.Forms"],
                },
            }

            def parse_side_effect(fpath, language, queries, **_kw):
                return symbols_by_file[Path(fpath).name]

            with mock.patch.object(tree_sitter, "TREE_SITTER_AVAILABLE", True), \
                 mock.patch.object(
                     tree_sitter,
                     "_parse_file_symbols",
                     side_effect=parse_side_effect,
                 ):
                result = tree_sitter.extract(
                    root,
                    {"glob": "**/*.cs", "language": "csharp"},
                    {},
                )

        form_refs = [
            nid for nid, node in result.nodes.items()
            if node.get("type") == "symbol"
            and node.get("props", {}).get("name") == "Form"
            and node.get("props", {}).get("kind") == "base_reference"
        ]
        # Acceptance: exactly one canonical placeholder. Per-namespace
        # fan-out would surface as two placeholders here.
        self.assertEqual(
            len(form_refs), 1,
            f"expected single Form base_reference node, got {form_refs!r}",
        )
        canonical = form_refs[0]
        self.assertEqual(canonical, "symbol:csharp:System.Windows.Forms.Form")

        inherits = [e for e in result.edges if e["type"] == "inherits"]
        form_inherits = [e for e in inherits if e["to"] == canonical]
        self.assertEqual(
            len(form_inherits), 2,
            f"expected two inherits edges targeting the canonical Form "
            f"node, got {form_inherits!r}",
        )
        sources = {e["from"] for e in form_inherits}
        self.assertEqual(
            sources,
            {
                "symbol:csharp:src.HelpersLib.MyForm:MyForm",
                "symbol:csharp:src.HistoryLib.HistoryForm:HistoryForm",
            },
        )
        # The legacy per-namespace placeholders must NOT exist.
        self.assertNotIn("symbol:csharp:ShareX.HelpersLib.Form", result.nodes)
        self.assertNotIn(
            "symbol:csharp:ShareX.HistoryLib.Forms.Form", result.nodes,
        )


if __name__ == "__main__":
    unittest.main()
