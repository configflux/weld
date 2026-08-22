"""Tests for the test_peer discovery strategy.

The strategy walks ``weld/tests/*_test.py`` and emits one ``file`` node
per test module, plus a ``tests`` edge to the production peer when one
can be located. The intent is to surface test modules to ``wd query``
so a query for a domain term like ``telemetry test`` returns the test
files alongside their production siblings, instead of empty results.

Per ADR 0046 (multi-language test-peer edges) the strategy now
dispatches by file extension to per-language resolvers. The original
Python tests in this file cover the dispatcher's Python path; the
multi-language test classes below cover Go, TS/JS, Java, C#, and Rust.
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

    def test_test_node_id_uses_canonical_full_path(self) -> None:
        # ADR 0041 § Layer 1: file ids are the full repo-relative POSIX
        # path without extension, routed through ``_node_ids.file_id``.
        self.assertEqual(
            _test_node_id(Path("weld/tests/weld_telemetry_cli_test.py")),
            "file:weld/tests/weld_telemetry_cli_test",
        )

    def test_peer_node_id_drops_test_suffix(self) -> None:
        # weld_telemetry_cli_test.py -> weld_telemetry_cli, peer module
        # lives at weld/weld_telemetry_cli.py, modeled as
        # ``file:weld/weld_telemetry_cli`` by python_module._make_node_id
        # under the canonical ADR-0041 full-path file-id contract.
        self.assertEqual(
            _peer_node_id(Path("weld/tests/weld_telemetry_cli_test.py")),
            "file:weld/weld_telemetry_cli",
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
        # bd uc43: a test file one directory DOWN, which the single-level
        # ``weld/tests/*_test.py`` glob missed entirely.
        _touch(
            self.root / "weld" / "tests" / "bench" / "weld_public_bench_test.py",
            "import unittest\n",
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, glob: str = "weld/tests/*_test.py") -> StrategyResult:
        source = {
            "glob": glob,
            "type": "file",
            "strategy": "test_peer",
        }
        return extract(self.root, source, {})

    def test_recursive_glob_reaches_a_nested_test_package(self) -> None:
        """bd uc43: ``**`` must cover ``weld/tests/bench/``, not just its parent.

        The reported symptom was an asymmetry rather than a plain absence:
        ``wd context`` resolved a file -> test-target edge for ``weld/tests/*``
        and answered "node not found" for ``weld/tests/bench/*``, which is
        harder to trust than a lookup that never worked, because nothing tells
        the caller which case they are in. The cause was this glob: 23 bench
        test files had no ``file`` node at all, so the ADR 0111 BUILD-srcs
        referrer edges had nothing to attach to. ``.weld/discover.yaml`` now
        configures the recursive form, which ADR 0112's single resolver honours
        (bd t06t: single-directory strategies used to ignore ``**`` silently).
        """
        single = self._run()
        self.assertNotIn(
            "file:weld/tests/bench/weld_public_bench_test",
            single.nodes,
            msg="precondition: the single-level glob is what missed the "
                "nested package -- if this starts passing, the ** entry below "
                "is no longer what closes bd uc43",
        )
        recursive = self._run("weld/tests/**/*_test.py")
        self.assertIn(
            "file:weld/tests/bench/weld_public_bench_test", recursive.nodes
        )
        self.assertIn(
            "file:weld/tests/weld_telemetry_cli_test",
            recursive.nodes,
            msg="the recursive glob must still cover the parent directory",
        )

    def test_emits_one_node_per_test_file(self) -> None:
        result = self._run()
        self.assertIn("file:weld/tests/weld_telemetry_cli_test", result.nodes)
        self.assertIn("file:weld/tests/weld_orphan_only_test", result.nodes)
        self.assertNotIn(
            "file:weld/tests/telemetry_test_helpers",
            result.nodes,
            msg="helper modules without _test.py suffix must be skipped",
        )

    def test_node_carries_test_role_and_kind(self) -> None:
        result = self._run()
        node = result.nodes["file:weld/tests/weld_telemetry_cli_test"]
        self.assertEqual(node["type"], "file")
        props = node["props"]
        self.assertEqual(props["roles"], ["test"])
        self.assertEqual(props["kind"], "test")
        self.assertEqual(props["authority"], "derived")
        self.assertEqual(props["confidence"], "definite")
        self.assertEqual(props["source_strategy"], "test_peer")

    def test_every_emitted_test_node_tags_origin_project(self) -> None:
        # ADR 0042: every file node must carry ``props.origin``. The
        # test_peer strategy only matches files inside the project's
        # configured test globs (excludes prune third-party trees), so
        # every emission is unambiguously ``origin="project"``. Asserts
        # on every node in the result rather than a single sample so a
        # future regression that drops the tag on, say, the orphan path
        # cannot slip past this gate.
        result = self._run()
        self.assertTrue(result.nodes, "fixture must emit at least one node")
        for nid, node in result.nodes.items():
            with self.subTest(node_id=nid):
                self.assertEqual(node["props"].get("origin"), "project")

    def test_node_records_legacy_id_alias(self) -> None:
        # ADR 0041 migration: the previous ``file:tests/<stem>`` shape is
        # preserved on ``aliases`` for one minor version so external
        # consumers (MCP transcripts, sidecar caches) keep resolving.
        result = self._run()
        node = result.nodes["file:weld/tests/weld_telemetry_cli_test"]
        self.assertIn(
            "file:tests/weld_telemetry_cli_test",
            node["props"]["aliases"],
        )

    def test_node_label_and_file_carry_telemetry_token(self) -> None:
        # The whole point of the strategy: tokens like 'telemetry' and
        # 'test' must be reachable via the query index.
        result = self._run()
        node = result.nodes["file:weld/tests/weld_telemetry_cli_test"]
        self.assertIn("telemetry", node["label"].lower())
        self.assertIn("test", node["label"].lower())
        self.assertIn("telemetry", node["props"]["file"].lower())
        self.assertIn("test", node["props"]["file"].lower())

    def test_emits_tests_edge_to_existing_peer(self) -> None:
        result = self._run()
        edges = [
            e for e in result.edges
            if e["from"] == "file:weld/tests/weld_telemetry_cli_test"
        ]
        self.assertEqual(len(edges), 1)
        edge = edges[0]
        self.assertEqual(edge["type"], "tests")
        self.assertEqual(edge["to"], "file:weld/weld_telemetry_cli")
        self.assertEqual(edge["props"]["confidence"], "inferred")
        self.assertEqual(edge["props"]["source_strategy"], "test_peer")

    def test_no_edge_when_peer_missing(self) -> None:
        result = self._run()
        edges_from_orphan = [
            e for e in result.edges
            if e["from"] == "file:weld/tests/weld_orphan_only_test"
        ]
        self.assertEqual(edges_from_orphan, [])

    def test_falls_back_to_unprefixed_peer(self) -> None:
        # weld_telemetry_writer_test.py -> peer telemetry_writer.py
        # (test file carries the ``weld_`` prefix, production does not).
        result = self._run()
        edges = [
            e for e in result.edges
            if e["from"] == "file:weld/tests/weld_telemetry_writer_test"
        ]
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["to"], "file:weld/telemetry_writer")
        self.assertEqual(edges[0]["type"], "tests")

    def test_falls_back_to_underscore_filename_peer(self) -> None:
        # internal_helper_test.py -> peer _internal_helper.py
        # python_module ids private modules as ``file:weld/_internal_helper``.
        result = self._run()
        edges = [
            e for e in result.edges
            if e["from"] == "file:weld/tests/internal_helper_test"
        ]
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["to"], "file:weld/_internal_helper")
        self.assertEqual(edges[0]["type"], "tests")


class TestSummaryExtraction(unittest.TestCase):
    """bd ikof: a Python test file's own docstring becomes ``props.summary``.

    Before this, ``test_peer`` minted a node from the path alone -- a test's
    own stated purpose (as opposed to whatever its filename happens to
    spell) was invisible to the query index, the one channel ADR 0114 wired
    for every ``python_module``-discovered file but never extended here.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _touch(
            self.root / "weld" / "tests" / "documented_test.py",
            '"""Incremental refresh is byte-equivalent to a full discover.\n'
            "\n"
            'More prose that must NOT be included -- only the opening\n'
            'paragraph is a summary.\n"""\n'
            "import unittest\n",
        )
        _touch(
            self.root / "weld" / "tests" / "undocumented_test.py",
            "import unittest\n",
        )
        _touch(
            self.root / "weld" / "tests" / "broken_syntax_test.py",
            "def broken(:\n",
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

    def test_docstring_opening_paragraph_becomes_summary(self) -> None:
        result = self._run()
        node = result.nodes["file:weld/tests/documented_test"]
        self.assertEqual(
            node["props"]["summary"],
            "Incremental refresh is byte-equivalent to a full discover.",
        )

    def test_summary_key_is_always_present_even_when_empty(self) -> None:
        # ADR 0114's contract: the key is always present so a consumer never
        # has to branch on whether it exists.
        result = self._run()
        node = result.nodes["file:weld/tests/undocumented_test"]
        self.assertEqual(node["props"]["summary"], "")

    def test_unparseable_file_still_emits_a_node_with_empty_summary(self) -> None:
        # A syntax error must cost this one channel, not the node itself --
        # the file is still a discoverable test module.
        result = self._run()
        node = result.nodes["file:weld/tests/broken_syntax_test"]
        self.assertEqual(node["props"]["summary"], "")


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
