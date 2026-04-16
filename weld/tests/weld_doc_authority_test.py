"""Tests for authoritative doc, policy, runbook, gate, and verification surface modeling.

Verifies that:
- The contract defines DOC_KIND_VALUES vocabulary
- doc_kind is a recognized optional node prop
- The markdown strategy sets doc_kind from source config
- Different doc kinds get appropriate authority and roles
- Gate nodes use the 'gate' node type
- The 'governs' edge type is available for policy relationships
- validate_node accepts and validates doc_kind values
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

class DocKindContractTest(unittest.TestCase):
    """Contract defines the doc_kind vocabulary and validates it."""

    def test_doc_kind_values_defined(self) -> None:
        from weld.contract import DOC_KIND_VALUES
        self.assertIsInstance(DOC_KIND_VALUES, frozenset)
        # Must include all the key doc kinds
        for kind in ("adr", "policy", "runbook", "guide", "gate", "verification"):
            self.assertIn(kind, DOC_KIND_VALUES)

    def test_doc_kind_in_optional_props(self) -> None:
        from weld.contract import NODE_OPTIONAL_PROPS
        self.assertIn("doc_kind", NODE_OPTIONAL_PROPS)

    def test_validate_node_accepts_valid_doc_kind(self) -> None:
        from weld.contract import validate_node
        node = {
            "type": "doc",
            "label": "Test ADR",
            "props": {
                "file": "docs/adrs/0001-test.md",
                "doc_kind": "adr",
                "authority": "canonical",
                "confidence": "definite",
            },
        }
        errors = validate_node("doc:adr/0001-test", node)
        self.assertEqual(errors, [])

    def test_validate_node_rejects_invalid_doc_kind(self) -> None:
        from weld.contract import validate_node
        node = {
            "type": "doc",
            "label": "Test",
            "props": {
                "file": "docs/test.md",
                "doc_kind": "nonsense",
            },
        }
        errors = validate_node("doc:test", node)
        self.assertTrue(
            any("doc_kind" in str(e) for e in errors),
            f"expected doc_kind validation error, got: {errors}",
        )

    def test_governs_edge_type_is_valid(self) -> None:
        from weld.contract import VALID_EDGE_TYPES
        self.assertIn("governs", VALID_EDGE_TYPES)

    def test_gate_node_type_is_valid(self) -> None:
        from weld.contract import VALID_NODE_TYPES
        self.assertIn("gate", VALID_NODE_TYPES)

class MarkdownDocKindTest(unittest.TestCase):
    """Markdown strategy sets doc_kind from source config."""

    def test_adr_doc_kind_from_source(self) -> None:
        from weld.strategies.markdown import extract
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            adrs = root / "docs" / "adrs"
            adrs.mkdir(parents=True)
            (adrs / "0001-test.md").write_text("# ADR 1\nDecision text.")
            result = extract(root, {
                "glob": "docs/adrs/*.md",
                "id_prefix": "doc:adr",
                "doc_kind": "adr",
            }, {})
            self.assertTrue(result.nodes, "should produce at least one node")
            for nid, node in result.nodes.items():
                self.assertEqual(node["props"]["doc_kind"], "adr")
                self.assertEqual(node["props"]["authority"], "canonical")

    def test_runbook_doc_kind_from_source(self) -> None:
        from weld.strategies.markdown import extract
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            runbooks = root / "docs" / "runbooks"
            runbooks.mkdir(parents=True)
            (runbooks / "deploy.md").write_text("# Deploy\nSteps.")
            result = extract(root, {
                "glob": "docs/runbooks/*.md",
                "id_prefix": "doc:runbook",
                "doc_kind": "runbook",
            }, {})
            self.assertTrue(result.nodes)
            for nid, node in result.nodes.items():
                self.assertEqual(node["props"]["doc_kind"], "runbook")
                self.assertEqual(node["props"]["authority"], "canonical")

    def test_policy_doc_kind_from_source(self) -> None:
        from weld.strategies.markdown import extract
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            docs = root / "docs"
            docs.mkdir()
            (docs / "codex-rules.md").write_text("# Policy\nRules.")
            result = extract(root, {
                "glob": "docs/*.md",
                "id_prefix": "doc:policy",
                "doc_kind": "policy",
            }, {})
            self.assertTrue(result.nodes)
            for nid, node in result.nodes.items():
                self.assertEqual(node["props"]["doc_kind"], "policy")
                self.assertEqual(node["props"]["authority"], "canonical")

    def test_guide_doc_kind_from_source(self) -> None:
        from weld.strategies.markdown import extract
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            docs = root / "docs"
            docs.mkdir()
            (docs / "overview.md").write_text("# Overview\nContent.")
            result = extract(root, {
                "glob": "docs/*.md",
                "id_prefix": "doc:guide",
                "doc_kind": "guide",
            }, {})
            self.assertTrue(result.nodes)
            for nid, node in result.nodes.items():
                self.assertEqual(node["props"]["doc_kind"], "guide")
                # guides are supporting, not authoritative
                self.assertEqual(node["props"]["authority"], "derived")

    def test_missing_doc_kind_defaults_to_guide(self) -> None:
        """When no doc_kind in source, fall back to 'guide' from id_prefix heuristic."""
        from weld.strategies.markdown import extract
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            docs = root / "docs"
            docs.mkdir()
            (docs / "misc.md").write_text("# Misc\nContent.")
            result = extract(root, {
                "glob": "docs/*.md",
                "id_prefix": "doc:guide",
            }, {})
            self.assertTrue(result.nodes)
            for nid, node in result.nodes.items():
                self.assertIn("doc_kind", node["props"])

    def test_doc_nodes_pass_contract_validation(self) -> None:
        from weld.contract import validate_node
        from weld.strategies.markdown import extract
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            adrs = root / "docs" / "adrs"
            adrs.mkdir(parents=True)
            (adrs / "0001-test.md").write_text("# ADR 1\nDecision.")
            result = extract(root, {
                "glob": "docs/adrs/*.md",
                "id_prefix": "doc:adr",
                "doc_kind": "adr",
            }, {})
            for nid, node in result.nodes.items():
                errors = validate_node(nid, node)
                self.assertEqual(errors, [], f"validation errors for {nid}: {errors}")

class GateNodeTest(unittest.TestCase):
    """Gate verification surfaces use the 'gate' node type."""

    def test_gate_node_from_topology(self) -> None:
        from weld.discover import discover
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            weld_dir = root / ".weld"
            weld_dir.mkdir()
            # Create a gate script so path check passes
            (root / "local-task-gate").write_text("#!/bin/bash\nexit 0")
            (weld_dir / "discover.yaml").write_text(textwrap.dedent("""\
                sources: []
                topology:
                  nodes:
                    - id: "gate:local-task-gate"
                      type: gate
                      label: Local Task Gate
                      props:
                        file: "local-task-gate"
                        doc_kind: "gate"
                  edges: []
            """))
            graph = discover(root)
            node = graph["nodes"].get("gate:local-task-gate")
            self.assertIsNotNone(node, "gate node should exist")
            self.assertEqual(node["type"], "gate")
            self.assertEqual(node["props"].get("doc_kind"), "gate")
            self.assertEqual(node["props"].get("authority"), "manual")

    def test_gate_node_passes_validation(self) -> None:
        from weld.contract import validate_node
        node = {
            "type": "gate",
            "label": "Local Task Gate",
            "props": {
                "file": "local-task-gate",
                "doc_kind": "gate",
                "source_strategy": "topology",
                "authority": "manual",
                "confidence": "definite",
            },
        }
        errors = validate_node("gate:local-task-gate", node)
        self.assertEqual(errors, [])

class GovernsEdgeTest(unittest.TestCase):
    """The 'governs' edge type connects policy/gate nodes to implementations."""

    def test_governs_edge_in_topology(self) -> None:
        from weld.discover import discover
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            weld_dir = root / ".weld"
            weld_dir.mkdir()
            (weld_dir / "discover.yaml").write_text(textwrap.dedent("""\
                sources: []
                topology:
                  nodes:
                    - id: "doc:policy/codex-rules"
                      type: doc
                      label: Codex Rules
                      props:
                        doc_kind: "policy"
                    - id: "service:api"
                      type: service
                      label: API
                      props: {}
                  edges:
                    - from: "doc:policy/codex-rules"
                      to: "service:api"
                      type: governs
            """))
            graph = discover(root)
            governs_edges = [
                e for e in graph["edges"] if e["type"] == "governs"
            ]
            self.assertEqual(len(governs_edges), 1)
            self.assertEqual(governs_edges[0]["from"], "doc:policy/codex-rules")
            self.assertEqual(governs_edges[0]["to"], "service:api")

class DocKindAuthorityMappingTest(unittest.TestCase):
    """Different doc kinds get appropriate authority levels."""

    def test_adr_is_canonical(self) -> None:
        from weld.strategies.markdown import extract
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            adrs = root / "docs" / "adrs"
            adrs.mkdir(parents=True)
            (adrs / "0001-test.md").write_text("# ADR\nDecision.")
            result = extract(root, {
                "glob": "docs/adrs/*.md",
                "id_prefix": "doc:adr",
                "doc_kind": "adr",
            }, {})
            for nid, node in result.nodes.items():
                self.assertEqual(node["props"]["authority"], "canonical")

    def test_policy_is_canonical(self) -> None:
        from weld.strategies.markdown import extract
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            docs = root / "docs"
            docs.mkdir()
            (docs / "rules.md").write_text("# Rules\nContent.")
            result = extract(root, {
                "glob": "docs/*.md",
                "id_prefix": "doc:policy",
                "doc_kind": "policy",
            }, {})
            for nid, node in result.nodes.items():
                self.assertEqual(node["props"]["authority"], "canonical")

    def test_runbook_is_canonical(self) -> None:
        from weld.strategies.markdown import extract
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            runbooks = root / "docs" / "runbooks"
            runbooks.mkdir(parents=True)
            (runbooks / "ops.md").write_text("# Ops\nSteps.")
            result = extract(root, {
                "glob": "docs/runbooks/*.md",
                "id_prefix": "doc:runbook",
                "doc_kind": "runbook",
            }, {})
            for nid, node in result.nodes.items():
                self.assertEqual(node["props"]["authority"], "canonical")

    def test_guide_is_derived(self) -> None:
        from weld.strategies.markdown import extract
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            docs = root / "docs"
            docs.mkdir()
            (docs / "howto.md").write_text("# How-to\nContent.")
            result = extract(root, {
                "glob": "docs/*.md",
                "id_prefix": "doc:guide",
                "doc_kind": "guide",
            }, {})
            for nid, node in result.nodes.items():
                self.assertEqual(node["props"]["authority"], "derived")

if __name__ == "__main__":
    unittest.main()
