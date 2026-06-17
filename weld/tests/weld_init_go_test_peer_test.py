"""Acceptance tests for the Go test_peer scaffold in ``wd init``.

``weld/init.py`` pairs every Python test glob with both a
``python_module`` and a ``test_peer`` source entry (ADR 0046). The
tree-sitter languages historically got only a ``tree_sitter`` source
entry, so a stock ``wd init`` on a Go module never scaffolded the
``test_peer`` entry that surfaces ``foo_test.go`` and emits the
``tests`` edge to its same-directory ``foo.go`` peer. The
``_test_peer_go`` resolver already works (it is registered in
``weld.strategies.test_peer._RESOLVERS_BY_SUFFIX``); only the init
scaffold was missing the pairing.

This is the deferred Go sibling of the Rust scaffold pinned by
``weld_init_rust_test_peer_test.py``. Unlike Rust's Cargo ``tests/``
directory, the Go convention is ``foo_test.go`` beside ``foo.go`` in
the same directory, so the scaffolded glob is ``**/*_test.go``.

These tests pin the parity fix:

1. ``wd init`` on a Go module emits a ``test_peer`` source entry for
   the Go test glob ``**/*_test.go``.
2. That entry lands in the ``tests`` artifact-class section, mirroring
   the Python pairing.
3. The ``tree_sitter`` Go ``**/*.go`` entry is still emitted.
4. The scaffolded glob actually drives the resolver: feeding it through
   ``test_peer.extract`` against the bundled Go fixture emits the
   ``tests`` edge.
5. A non-Go fixture does not emit a Go ``test_peer`` entry.

Mirrors the per-language pattern set by ``weld_init_rust_test_peer_test.py``.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld._yaml import parse_yaml
from weld.init import init as init_run
from weld.strategies.test_peer import extract as test_peer_extract

_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_GO_TEST_GLOB = "**/*_test.go"


def _init_fixture(fixture_dir: Path) -> tuple[dict, str]:
    """Run ``wd init`` on *fixture_dir*; return parsed + raw discover.yaml."""
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / ".weld" / "discover.yaml"
        success = init_run(fixture_dir, out, force=True)
        assert success, f"wd init failed for {fixture_dir}"
        raw = out.read_text(encoding="utf-8")
        return parse_yaml(raw), raw


def _test_peer_sources(data: dict) -> list[dict]:
    """Return all ``strategy: test_peer`` source entries."""
    return [s for s in data.get("sources", [])
            if s.get("strategy") == "test_peer"]


class GoTestPeerScaffoldTest(unittest.TestCase):
    """``wd init`` on a Go module wires a ``test_peer`` Go glob."""

    def setUp(self) -> None:
        self._fixture = _FIXTURES / "tier1" / "go" / "sample_go"
        if not self._fixture.is_dir():
            self.skipTest(f"go tier1 fixture missing: {self._fixture}")

    def test_emits_test_peer_go_glob(self) -> None:
        data, _raw = _init_fixture(self._fixture)
        peers = _test_peer_sources(data)
        globs = [s.get("glob") for s in peers]
        self.assertIn(
            _GO_TEST_GLOB, globs,
            f"expected a test_peer entry for {_GO_TEST_GLOB!r}; got {globs}",
        )

    def test_test_peer_go_entry_is_file_node(self) -> None:
        data, _raw = _init_fixture(self._fixture)
        go_peers = [s for s in _test_peer_sources(data)
                    if s.get("glob") == _GO_TEST_GLOB]
        self.assertTrue(go_peers, "no go test_peer entry emitted")
        for src in go_peers:
            self.assertEqual(
                src.get("type"), "file",
                f"go test_peer source not type=file: {src}",
            )

    def test_test_peer_entry_under_tests_section(self) -> None:
        # Parity with the Python pairing: the entry belongs to the
        # ``tests`` artifact-class bucket, not ``code``. Section headers
        # are YAML comments, so assert on the raw text ordering: the
        # ``tests`` header must precede the glob.
        _data, raw = _init_fixture(self._fixture)
        tests_hdr = raw.find("===== tests =====")
        glob_at = raw.find(f'"{_GO_TEST_GLOB}"')
        self.assertNotEqual(tests_hdr, -1, "no tests section header emitted")
        self.assertNotEqual(glob_at, -1, "go test glob not in discover.yaml")
        self.assertLess(
            tests_hdr, glob_at,
            "go test_peer glob is not under the tests section header",
        )

    def test_tree_sitter_go_source_still_emitted(self) -> None:
        # The parity fix must not displace the tree_sitter entry that
        # emits the Go symbol/definition nodes.
        data, _raw = _init_fixture(self._fixture)
        ts_go = [s for s in data.get("sources", [])
                 if s.get("strategy") == "tree_sitter"
                 and s.get("language") == "go"]
        self.assertTrue(ts_go, "tree_sitter go source dropped")
        self.assertTrue(
            any(s.get("glob") == "**/*.go" for s in ts_go),
            f"expected tree_sitter **/*.go; got {[s.get('glob') for s in ts_go]}",
        )

    def test_scaffolded_glob_emits_tests_edge(self) -> None:
        # End-to-end: the glob the scaffold writes, fed through the real
        # resolver against the bundled fixture, must emit the
        # foo_test.go -[tests]-> foo.go edge.
        result = test_peer_extract(
            self._fixture,
            {"glob": _GO_TEST_GLOB, "type": "file", "strategy": "test_peer"},
            {},
        )
        tests_edges = [e for e in result.edges if e.get("type") == "tests"]
        self.assertTrue(
            tests_edges,
            "scaffolded go test_peer glob emitted no tests edge",
        )
        froms = {e["from"] for e in tests_edges}
        tos = {e["to"] for e in tests_edges}
        self.assertTrue(
            any(f.endswith("geometry/geometry_test") for f in froms),
            f"expected a tests-edge from geometry_test; got {sorted(froms)}",
        )
        self.assertTrue(
            any(t.endswith("geometry/geometry") for t in tos),
            f"expected a tests-edge to geometry; got {sorted(tos)}",
        )


class NonGoFixtureIsolationTest(unittest.TestCase):
    """Non-Go fixtures must NOT emit a Go ``test_peer`` source."""

    def test_csharp_project_has_no_go_test_peer(self) -> None:
        data, _raw = _init_fixture(_FIXTURES / "csharp_project")
        go_peers = [s for s in _test_peer_sources(data)
                    if s.get("glob") == _GO_TEST_GLOB]
        self.assertFalse(
            go_peers,
            f"csharp_project must not emit a go test_peer entry; got {go_peers}",
        )


if __name__ == "__main__":
    unittest.main()
