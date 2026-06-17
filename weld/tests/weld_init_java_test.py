"""Acceptance tests for the Java tree-sitter wiring in ``wd init``.

``weld/init.py``'s ``_TREE_SITTER_LANGUAGES`` map lists Java so that
``init_detect`` recognising ``.java`` and the tree-sitter binding
listing ``java`` (see
``weld/strategies/_ts_definitions.T1_DEFINITION_LANGUAGES``) flow
through to ``weld.init`` emitting a ``tree_sitter`` glob. Without
this entry, ``wd init`` on a Maven project produces a discover.yaml
with no Java source and ``wd discover`` yields zero symbols.

Mirrors the per-language pattern set by ``weld_init_csharp_test.py``
and ``weld_init_cpp_test.py``: each tree-sitter language gets a small
acceptance test file scoped to that language's auto-detection
contract.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


from weld._yaml import parse_yaml  # noqa: E402
from weld.init import init as init_run  # noqa: E402

_FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _init_fixture_at(fixture_dir: Path) -> dict:
    """Run wd init on *fixture_dir* and return the parsed discover.yaml."""
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / ".weld" / "discover.yaml"
        success = init_run(fixture_dir, out, force=True)
        assert success, f"wd init failed for {fixture_dir}"
        return parse_yaml(out.read_text(encoding="utf-8"))


class JavaInitAcceptanceTest(unittest.TestCase):
    """``wd init`` on a Maven project wires a tree_sitter Java glob."""

    def setUp(self) -> None:
        self._fixture = _FIXTURES / "tier1" / "java" / "sample_java"
        if not self._fixture.is_dir():
            self.skipTest(f"java tier1 fixture missing: {self._fixture}")

    def test_emits_tree_sitter_java_source(self) -> None:
        data = _init_fixture_at(self._fixture)
        java_sources = [s for s in data.get("sources", [])
                        if s.get("strategy") == "tree_sitter"
                        and s.get("language") == "java"]
        self.assertTrue(java_sources, "no tree_sitter java source emitted")
        self.assertTrue(
            any(s.get("glob") == "**/*.java" for s in java_sources),
            f"expected **/*.java; got {[s.get('glob') for s in java_sources]}",
        )

    def test_emits_file_node_type(self) -> None:
        data = _init_fixture_at(self._fixture)
        java_sources = [s for s in data.get("sources", [])
                        if s.get("strategy") == "tree_sitter"
                        and s.get("language") == "java"]
        for src in java_sources:
            self.assertEqual(src.get("type"), "file",
                             f"java tree_sitter source not type=file: {src}")


class NonJavaFixtureIsolationTest(unittest.TestCase):
    """Non-Java fixtures must NOT emit a Java tree_sitter source."""

    def test_csharp_project_has_no_java_source(self) -> None:
        data = _init_fixture_at(_FIXTURES / "csharp_project")
        java_sources = [s for s in data.get("sources", [])
                        if s.get("strategy") == "tree_sitter"
                        and s.get("language") == "java"]
        self.assertFalse(java_sources,
                         f"csharp_project should not emit java sources; got {java_sources}")


if __name__ == "__main__":
    unittest.main()
