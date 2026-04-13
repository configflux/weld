"""Acceptance tests for external_json ingestion — fragment merge behavior.

Validates that external graph fragments merge correctly into an existing
graph, preserving existing nodes while adding new ones, and that the
discover pipeline handles multiple external_json sources in a single run.

"""

from __future__ import annotations

import json
import stat
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from cortex.contract import validate_fragment  # noqa: E402
from cortex.discover import _run_external_json, discover  # noqa: E402
from cortex.graph import Graph  # noqa: E402

def _write_adapter(tmpdir: Path, name: str, fragment: dict) -> str:
    """Write a Python script that outputs a JSON fragment to stdout."""
    script = tmpdir / name
    script.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import json, sys
        json.dump({json.dumps(fragment)}, sys.stdout)
    """), encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)

# -- Reusable fragments ------------------------------------------------------

_FRAGMENT_A = {
    "nodes": {
        "tool:linter": {
            "type": "tool",
            "label": "Custom Linter",
            "props": {"source_strategy": "external_json", "authority": "external"},
        },
        "service:monitor": {
            "type": "service",
            "label": "Monitor Service",
            "props": {"source_strategy": "external_json", "authority": "derived"},
        },
    },
    "edges": [
        {"from": "tool:linter", "to": "service:monitor",
         "type": "invokes", "props": {}},
    ],
    "discovered_from": ["tools/linter.py"],
}

_FRAGMENT_B = {
    "nodes": {
        "service:cache": {
            "type": "service",
            "label": "Cache Layer",
            "props": {"source_strategy": "external_json", "authority": "canonical"},
        },
    },
    "edges": [
        {"from": "service:monitor", "to": "service:cache",
         "type": "depends_on", "props": {}},
    ],
    "discovered_from": ["tools/cache-adapter.py"],
}

class ExternalJsonMergeTest(unittest.TestCase):
    """Fragments merge correctly into an existing graph via Graph.merge_import."""

    def test_merge_adds_new_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            g = Graph(Path(td))
            g.load()
            g.add_node("service:existing", "service", "Existing", {"authority": "canonical"})
            result = g.merge_import(_FRAGMENT_A)
            self.assertEqual(result["added_nodes"], 2)
            self.assertIsNotNone(g.get_node("tool:linter"))
            self.assertIsNotNone(g.get_node("service:monitor"))

    def test_merge_preserves_existing_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            g = Graph(Path(td))
            g.load()
            g.add_node("service:existing", "service", "Existing", {"authority": "canonical"})
            g.merge_import(_FRAGMENT_A)
            existing = g.get_node("service:existing")
            self.assertIsNotNone(existing)
            self.assertEqual(existing["label"], "Existing")

    def test_merge_adds_edges(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            g = Graph(Path(td))
            g.load()
            result = g.merge_import(_FRAGMENT_A)
            self.assertEqual(result["added_edges"], 1)

    def test_merge_deduplicates_edges(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            g = Graph(Path(td))
            g.load()
            g.merge_import(_FRAGMENT_A)
            result = g.merge_import(_FRAGMENT_A)
            self.assertEqual(result["added_edges"], 0)

    def test_sequential_merges_accumulate(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            g = Graph(Path(td))
            g.load()
            g.merge_import(_FRAGMENT_A)
            g.merge_import(_FRAGMENT_B)
            self.assertIsNotNone(g.get_node("tool:linter"))
            self.assertIsNotNone(g.get_node("service:monitor"))
            self.assertIsNotNone(g.get_node("service:cache"))
            stats = g.stats()
            self.assertEqual(stats["total_nodes"], 3)
            self.assertEqual(stats["total_edges"], 2)

    def test_merge_overwrites_existing_node_on_collision(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            g = Graph(Path(td))
            g.load()
            g.add_node("tool:linter", "tool", "Old Label", {"old": True})
            g.merge_import(_FRAGMENT_A)
            node = g.get_node("tool:linter")
            self.assertEqual(node["label"], "Custom Linter")

class ExternalJsonFragmentValidationTest(unittest.TestCase):
    """Fragments pass the contract validator before merge."""

    def test_valid_fragment_passes_contract(self) -> None:
        errs = validate_fragment(_FRAGMENT_A, source_label="test:A")
        self.assertEqual(errs, [], f"Unexpected errors: {errs}")

    def test_multi_source_fragments_all_pass_contract(self) -> None:
        for label, frag in [("A", _FRAGMENT_A), ("B", _FRAGMENT_B)]:
            errs = validate_fragment(
                frag, source_label=f"test:{label}",
                allow_dangling_edges=True,
            )
            self.assertEqual(errs, [], f"Fragment {label} errors: {errs}")

class ExternalJsonDiscoverIntegrationTest(unittest.TestCase):
    """End-to-end: discover pipeline with multiple external_json sources."""

    def test_discover_merges_two_external_sources(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)
            cmd_a = _write_adapter(tmpdir, "adapter_a.py", _FRAGMENT_A)
            cmd_b = _write_adapter(tmpdir, "adapter_b.py", _FRAGMENT_B)

            cortex_dir = tmpdir / ".cortex"
            cortex_dir.mkdir()
            (cortex_dir / "discover.yaml").write_text(textwrap.dedent(f"""\
                sources:
                  - strategy: external_json
                    command: "{cmd_a}"
                  - strategy: external_json
                    command: "{cmd_b}"
                topology:
                  nodes: []
                  edges: []
            """), encoding="utf-8")

            result = discover(tmpdir)
            nodes = result.get("nodes", {})
            self.assertIn("tool:linter", nodes)
            self.assertIn("service:monitor", nodes)
            self.assertIn("service:cache", nodes)

    def test_discover_skips_failing_source_keeps_good_ones(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)
            cmd_good = _write_adapter(tmpdir, "good.py", _FRAGMENT_A)

            bad_script = tmpdir / "bad.py"
            bad_script.write_text(textwrap.dedent("""\
                #!/usr/bin/env python3
                import sys
                sys.exit(1)
            """), encoding="utf-8")
            bad_script.chmod(bad_script.stat().st_mode | stat.S_IEXEC)

            cortex_dir = tmpdir / ".cortex"
            cortex_dir.mkdir()
            (cortex_dir / "discover.yaml").write_text(textwrap.dedent(f"""\
                sources:
                  - strategy: external_json
                    command: "{bad_script}"
                  - strategy: external_json
                    command: "{cmd_good}"
                topology:
                  nodes: []
                  edges: []
            """), encoding="utf-8")

            result = discover(tmpdir)
            nodes = result.get("nodes", {})
            self.assertIn("tool:linter", nodes,
                          "Good source should still produce nodes")

    def test_discover_result_is_valid_graph(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)
            cmd_a = _write_adapter(tmpdir, "adapter.py", _FRAGMENT_A)

            cortex_dir = tmpdir / ".cortex"
            cortex_dir.mkdir()
            (cortex_dir / "discover.yaml").write_text(textwrap.dedent(f"""\
                sources:
                  - strategy: external_json
                    command: "{cmd_a}"
                topology:
                  nodes: []
                  edges: []
            """), encoding="utf-8")

            result = discover(tmpdir)
            self.assertIn("nodes", result)
            self.assertIn("edges", result)
            self.assertIn("meta", result)
            self.assertIn("version", result["meta"])

class ExternalJsonAdapterE2ETest(unittest.TestCase):
    """The adapter produces results that survive round-trip through Graph."""

    def test_adapter_output_survives_graph_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)
            cmd = _write_adapter(tmpdir, "adapter.py", _FRAGMENT_A)
            source = {"strategy": "external_json", "command": cmd}
            result = _run_external_json(tmpdir, source)

            g = Graph(tmpdir)
            g.load()
            g.merge_import({"nodes": result.nodes, "edges": result.edges})
            g.save()

            g2 = Graph(tmpdir)
            g2.load()
            self.assertIsNotNone(g2.get_node("tool:linter"))
            self.assertIsNotNone(g2.get_node("service:monitor"))
            self.assertEqual(g2.stats()["total_edges"], 1)

if __name__ == "__main__":
    unittest.main()
