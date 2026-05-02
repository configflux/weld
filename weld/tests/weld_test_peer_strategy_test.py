"""Tests for the test_peer discovery strategy.

The strategy walks ``weld/tests/*_test.py`` and emits one ``file`` node
per test module, plus a ``tests`` edge to the production peer when one
can be located. The intent is to surface test modules to ``wd query``
so a query for a domain term like ``telemetry test`` returns the test
files alongside their production siblings, instead of empty results.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld.strategies._helpers import StrategyResult
from weld.strategies.test_peer import (
    _peer_node_id,
    _test_node_id,
    extract,
)


def _touch(path: Path, content: str = "") -> None:
    """Create *path* with *content*, creating parents as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestNodeIdHelpers(unittest.TestCase):
    """Stable, collision-free ids are required for deterministic graphs."""

    def test_test_node_id_uses_file_prefix_and_stem(self) -> None:
        self.assertEqual(
            _test_node_id(Path("weld/tests/weld_telemetry_cli_test.py")),
            "file:tests/weld_telemetry_cli_test",
        )

    def test_peer_node_id_drops_test_suffix(self) -> None:
        # weld_telemetry_cli_test.py -> weld_telemetry_cli, peer module
        # lives at weld/weld_telemetry_cli.py, modeled as
        # ``file:weld_telemetry_cli`` by python_module._make_node_id with
        # no id_prefix.
        self.assertEqual(
            _peer_node_id(Path("weld/tests/weld_telemetry_cli_test.py")),
            "file:weld_telemetry_cli",
        )

    def test_peer_node_id_returns_none_when_stem_lacks_test_suffix(self) -> None:
        # telemetry_test_helpers.py is a helper, not a *_test.py module.
        self.assertIsNone(
            _peer_node_id(Path("weld/tests/telemetry_test_helpers.py")),
        )


class TestExtractEmitsNodes(unittest.TestCase):
    """Strategy must emit nodes for every weld/tests/*_test.py file."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        # Stand-in production module + its test peer.
        _touch(self.root / "weld" / "weld_telemetry_cli.py", "x = 1\n")
        _touch(
            self.root / "weld" / "tests" / "weld_telemetry_cli_test.py",
            "import unittest\n",
        )
        # A second test where the production module drops the ``weld_``
        # prefix that the test name carries; resolution must fall back
        # to the unprefixed stem.
        _touch(self.root / "weld" / "telemetry_writer.py", "x = 1\n")
        _touch(
            self.root / "weld" / "tests" / "weld_telemetry_writer_test.py",
            "import unittest\n",
        )
        # A third test whose production peer is a private module
        # (leading underscore in the filename); resolution must try
        # ``_<stem>.py`` as a filename variant.
        _touch(self.root / "weld" / "_internal_helper.py", "x = 1\n")
        _touch(
            self.root / "weld" / "tests" / "internal_helper_test.py",
            "import unittest\n",
        )
        # A test file with no production peer.
        _touch(
            self.root / "weld" / "tests" / "weld_orphan_only_test.py",
            "import unittest\n",
        )
        # A helper that should NOT be picked up (no _test suffix).
        _touch(
            self.root / "weld" / "tests" / "telemetry_test_helpers.py",
            "x = 1\n",
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self) -> StrategyResult:
        source = {
            "glob": "weld/tests/*_test.py",
            "type": "file",
            "strategy": "test_peer",
        }
        return extract(self.root, source, {})

    def test_emits_one_node_per_test_file(self) -> None:
        result = self._run()
        self.assertIn("file:tests/weld_telemetry_cli_test", result.nodes)
        self.assertIn("file:tests/weld_orphan_only_test", result.nodes)
        self.assertNotIn(
            "file:tests/telemetry_test_helpers",
            result.nodes,
            msg="helper modules without _test.py suffix must be skipped",
        )

    def test_node_carries_test_role_and_kind(self) -> None:
        result = self._run()
        node = result.nodes["file:tests/weld_telemetry_cli_test"]
        self.assertEqual(node["type"], "file")
        props = node["props"]
        self.assertEqual(props["roles"], ["test"])
        self.assertEqual(props["kind"], "test")
        self.assertEqual(props["authority"], "derived")
        self.assertEqual(props["confidence"], "definite")
        self.assertEqual(props["source_strategy"], "test_peer")

    def test_node_label_and_file_carry_telemetry_token(self) -> None:
        # The whole point of the strategy: tokens like 'telemetry' and
        # 'test' must be reachable via the query index.
        result = self._run()
        node = result.nodes["file:tests/weld_telemetry_cli_test"]
        self.assertIn("telemetry", node["label"].lower())
        self.assertIn("test", node["label"].lower())
        self.assertIn("telemetry", node["props"]["file"].lower())
        self.assertIn("test", node["props"]["file"].lower())

    def test_emits_tests_edge_to_existing_peer(self) -> None:
        result = self._run()
        edges = [
            e for e in result.edges
            if e["from"] == "file:tests/weld_telemetry_cli_test"
        ]
        self.assertEqual(len(edges), 1)
        edge = edges[0]
        self.assertEqual(edge["type"], "tests")
        self.assertEqual(edge["to"], "file:weld_telemetry_cli")
        self.assertEqual(edge["props"]["confidence"], "inferred")
        self.assertEqual(edge["props"]["source_strategy"], "test_peer")

    def test_no_edge_when_peer_missing(self) -> None:
        result = self._run()
        edges_from_orphan = [
            e for e in result.edges
            if e["from"] == "file:tests/weld_orphan_only_test"
        ]
        self.assertEqual(edges_from_orphan, [])

    def test_falls_back_to_unprefixed_peer(self) -> None:
        # weld_telemetry_writer_test.py -> peer telemetry_writer.py
        # (test file carries the ``weld_`` prefix, production does not).
        result = self._run()
        edges = [
            e for e in result.edges
            if e["from"] == "file:tests/weld_telemetry_writer_test"
        ]
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["to"], "file:telemetry_writer")
        self.assertEqual(edges[0]["type"], "tests")

    def test_falls_back_to_underscore_filename_peer(self) -> None:
        # internal_helper_test.py -> peer _internal_helper.py
        # python_module ids private modules as ``file:_internal_helper``.
        result = self._run()
        edges = [
            e for e in result.edges
            if e["from"] == "file:tests/internal_helper_test"
        ]
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["to"], "file:_internal_helper")
        self.assertEqual(edges[0]["type"], "tests")


class TestExtractGracefulOnEmpty(unittest.TestCase):
    """Missing or empty test directory must not raise."""

    def test_empty_root_returns_empty_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = {
                "glob": "weld/tests/*_test.py",
                "type": "file",
                "strategy": "test_peer",
            }
            result = extract(root, source, {})
            self.assertEqual(result.nodes, {})
            self.assertEqual(result.edges, [])


if __name__ == "__main__":
    unittest.main()
