"""Project-local strategy capability manifests (ADR 0087).

A ``.weld/strategies/`` strategy registers capabilities via a **declarative
manifest** -- an inline ``capabilities:`` block on its ``discover.yaml`` source
entry, or a sibling ``.weld/strategies/<stem>.yaml`` file -- honored by the
capability matrix WITHOUT an in-tree ``STRATEGY_CAPABILITIES`` entry. These
tests are the project-local counterpart to
``weld_capabilities_test.test_expected_strategies_match_disk``, which stays
scoped to bundled strategies. They assert the trust-boundary invariants:
reading a manifest never imports project-local code (safe-mode-permissible),
the evidence rule still gates false declarations, bundled entries are never
overridden, and a crafted strategy name cannot traverse the filesystem.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld._capabilities_local import (
    capability_from_manifest,
    load_local_capabilities,
)
from weld._capabilities_registry import STRATEGY_CAPABILITIES
from weld.capabilities import compute_capabilities


def _make_repo(
    discover_yaml: str, sibling: dict[str, str] | None = None,
) -> Path:
    """Build a temp repo with ``.weld/discover.yaml`` and sibling manifests.

    ``compute_capabilities`` takes graph data as an argument, so no
    ``graph.json`` is written -- only the config surface the capability path
    actually reads (``discover.yaml`` and ``.weld/strategies/``).
    """
    root = Path(tempfile.mkdtemp())
    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    (weld_dir / "discover.yaml").write_text(discover_yaml, encoding="utf-8")
    for name, body in (sibling or {}).items():
        sdir = weld_dir / "strategies"
        sdir.mkdir(parents=True, exist_ok=True)
        (sdir / name).write_text(body, encoding="utf-8")
    return root


def _graph(nodes: dict[str, dict]) -> dict:
    return {"meta": {}, "nodes": nodes, "edges": []}


class ProjectLocalCapabilitiesTest(unittest.TestCase):
    def test_inline_block_honored_without_registry_entry(self) -> None:
        self.assertNotIn("foo_lang", STRATEGY_CAPABILITIES)
        dy = (
            "sources:\n"
            "  - glob: 'src/**/*.foo'\n"
            "    type: file\n"
            "    strategy: foo_lang\n"
            "    capabilities:\n"
            "      language: foolang\n"
            "      evidence: [file, symbols]\n"
            "      file_extensions: ['.foo']\n"
        )
        nodes = {
            "file:src/a.foo": {"type": "file", "props": {"file": "src/a.foo"}},
        }
        root = _make_repo(dy)
        result = compute_capabilities(_graph(nodes), root)
        self.assertIn("foolang", result["languages"])
        row = result["languages"]["foolang"]
        self.assertTrue(row["file"])
        self.assertTrue(row["symbols"])

    def test_sibling_manifest_honored(self) -> None:
        dy = (
            "sources:\n"
            "  - glob: 'q/**/*.q'\n"
            "    type: file\n"
            "    strategy: q_strat\n"
        )
        sibling = {
            "q_strat.yaml": (
                "framework: qframework\n"
                "evidence: [nodes_emitted]\n"
                "file_extensions: ['.q']\n"
            ),
        }
        nodes = {"file:q/x.q": {"type": "file", "props": {"file": "q/x.q"}}}
        root = _make_repo(dy, sibling)
        result = compute_capabilities(_graph(nodes), root)
        self.assertIn("qframework", result["frameworks"])
        self.assertTrue(result["frameworks"]["qframework"]["nodes_emitted"])

    def test_sibling_capabilities_wrapper_honored(self) -> None:
        dy = (
            "sources:\n"
            "  - glob: '*.q'\n"
            "    type: file\n"
            "    strategy: q_strat\n"
        )
        sibling = {
            "q_strat.yaml": (
                "capabilities:\n"
                "  framework: qframework\n"
                "  evidence: [nodes_emitted]\n"
                "  file_extensions: ['.q']\n"
            ),
        }
        nodes = {"file:x.q": {"type": "file", "props": {"file": "x.q"}}}
        root = _make_repo(dy, sibling)
        result = compute_capabilities(_graph(nodes), root)
        self.assertTrue(result["frameworks"]["qframework"]["nodes_emitted"])

    def test_evidence_rule_gates_false_declaration(self) -> None:
        # Declared capability but NO matching graph file -> row present, every
        # flag False. A repo cannot spoof support by declaration alone.
        dy = (
            "sources:\n"
            "  - glob: 'src/**/*.foo'\n"
            "    type: file\n"
            "    strategy: foo_lang\n"
            "    capabilities:\n"
            "      language: foolang\n"
            "      evidence: [file, symbols]\n"
            "      file_extensions: ['.foo']\n"
        )
        root = _make_repo(dy)
        result = compute_capabilities(_graph({}), root)
        self.assertIn("foolang", result["languages"])
        self.assertFalse(any(result["languages"]["foolang"].values()))

    def test_bundled_registry_not_overridden(self) -> None:
        # An inline block on a BUNDLED stem cannot override it: the bogus
        # language is ignored and python behaves exactly as before.
        dy = (
            "sources:\n"
            "  - glob: '*.py'\n"
            "    type: file\n"
            "    strategy: python_module\n"
            "    capabilities:\n"
            "      language: bogus_local_lang\n"
            "      evidence: [file]\n"
        )
        nodes = {"file:a.py": {"type": "file", "props": {"file": "a.py"}}}
        root = _make_repo(dy)
        result = compute_capabilities(_graph(nodes), root)
        self.assertNotIn("bogus_local_lang", result["languages"])
        self.assertTrue(result["languages"]["python"]["file"])

    def test_unwired_sibling_manifest_not_surfaced(self) -> None:
        # A sibling manifest for a strategy NOT wired in discover.yaml is
        # ignored: only wired stems can contribute rows.
        dy = (
            "sources:\n"
            "  - glob: '*.py'\n"
            "    type: file\n"
            "    strategy: python_module\n"
        )
        sibling = {
            "ghost.yaml": "framework: ghostfw\nevidence: [nodes_emitted]\n",
        }
        root = _make_repo(dy, sibling)
        result = compute_capabilities(_graph({}), root)
        self.assertNotIn("ghostfw", result["frameworks"])

    def test_inline_block_wins_over_sibling(self) -> None:
        dy = (
            "sources:\n"
            "  - glob: '*.q'\n"
            "    type: file\n"
            "    strategy: q_strat\n"
            "    capabilities:\n"
            "      framework: inline_fw\n"
            "      evidence: [nodes_emitted]\n"
            "      file_extensions: ['.q']\n"
        )
        sibling = {
            "q_strat.yaml": (
                "framework: sibling_fw\n"
                "evidence: [nodes_emitted]\n"
                "file_extensions: ['.q']\n"
            ),
        }
        nodes = {"file:x.q": {"type": "file", "props": {"file": "x.q"}}}
        root = _make_repo(dy, sibling)
        result = compute_capabilities(_graph(nodes), root)
        self.assertIn("inline_fw", result["frameworks"])
        self.assertNotIn("sibling_fw", result["frameworks"])

    def test_safe_mode_does_not_import_project_local_code(self) -> None:
        # The capability path reads YAML only; it must NEVER import the
        # project-local strategy module. A .py that raises at import time
        # alongside a manifest proves the matrix is computed without exec.
        dy = (
            "sources:\n"
            "  - glob: 'src/**/*.foo'\n"
            "    type: file\n"
            "    strategy: foo_lang\n"
            "    capabilities:\n"
            "      language: foolang\n"
            "      evidence: [file]\n"
            "      file_extensions: ['.foo']\n"
        )
        sibling = {
            "foo_lang.py": (
                "raise RuntimeError('project-local code must not be imported')\n"
            ),
        }
        nodes = {
            "file:src/a.foo": {"type": "file", "props": {"file": "src/a.foo"}},
        }
        root = _make_repo(dy, sibling)
        # Must not raise, and must honor the declared capability.
        result = compute_capabilities(_graph(nodes), root)
        self.assertTrue(result["languages"]["foolang"]["file"])

    def test_path_traversal_strategy_name_rejected(self) -> None:
        # A crafted ``strategy:`` name must never be turned into a filesystem
        # path or honored, even with a well-formed capabilities block.
        dy = (
            "sources:\n"
            "  - glob: '*'\n"
            "    type: file\n"
            "    strategy: '../../evil'\n"
            "    capabilities:\n"
            "      framework: evilfw\n"
            "      evidence: [nodes_emitted]\n"
        )
        root = _make_repo(dy)
        self.assertEqual(load_local_capabilities(root), {})
        result = compute_capabilities(_graph({}), root)
        self.assertNotIn("evilfw", result["frameworks"])

    def test_capability_from_manifest_rejects_non_declarations(self) -> None:
        self.assertIsNone(capability_from_manifest(None))
        self.assertIsNone(capability_from_manifest({}))
        self.assertIsNone(capability_from_manifest("string"))
        self.assertIsNone(capability_from_manifest({"file_extensions": [".x"]}))
        # A declaration with no file signature is dropped (ADR 0087 point 4):
        # the evidence rule cannot gate it, so it could spoof support on an
        # empty graph. A signature makes it honorable.
        self.assertIsNone(capability_from_manifest({"language": "x"}))
        self.assertIsNotNone(
            capability_from_manifest({"language": "x", "file_extensions": [".x"]})
        )
        self.assertIsNotNone(
            capability_from_manifest({"language": "x", "file_basenames": ["X"]})
        )

    def test_signatureless_declaration_cannot_spoof_on_empty_graph(self) -> None:
        # A wired NEW stem declaring a framework with NO file signature must
        # NOT flip a flag true on an empty graph. Without a signature the
        # evidence rule cannot gate it, so the declaration is dropped entirely
        # and the framework never appears (ADR 0087 point 4 anti-spoof).
        dy = (
            "sources:\n"
            "  - glob: 'src/**/*'\n"
            "    type: file\n"
            "    strategy: spoof_strat\n"
            "    capabilities:\n"
            "      framework: spoof_fw\n"
            "      evidence: [file, nodes_emitted]\n"
        )
        root = _make_repo(dy)
        self.assertEqual(load_local_capabilities(root), {})
        result = compute_capabilities(_graph({}), root)
        self.assertNotIn("spoof_fw", result["frameworks"])

    def test_multi_language_manifest_declares_all(self) -> None:
        dy = (
            "sources:\n"
            "  - glob: 'src/**/*.ab'\n"
            "    type: file\n"
            "    strategy: ab_lang\n"
            "    capabilities:\n"
            "      languages: [alang, blang]\n"
            "      evidence: [file]\n"
            "      file_extensions: ['.ab']\n"
        )
        nodes = {"file:src/x.ab": {"type": "file", "props": {"file": "src/x.ab"}}}
        root = _make_repo(dy)
        result = compute_capabilities(_graph(nodes), root)
        self.assertTrue(result["languages"]["alang"]["file"])
        self.assertTrue(result["languages"]["blang"]["file"])


if __name__ == "__main__":
    unittest.main()
