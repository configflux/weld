"""Acceptance tests for the Rust test_peer scaffold in ``wd init``.

``weld/init.py`` pairs every Python test glob with both a
``python_module`` and a ``test_peer`` source entry (ADR 0046). The
tree-sitter languages historically got only a ``tree_sitter`` source
entry, so a stock ``wd init`` on a Cargo crate never scaffolded the
``test_peer`` entry that surfaces ``tests/<name>.rs`` and emits the
``tests`` edge to ``src/<name>.rs``. The ``_test_peer_rust`` resolver
already works (it is registered in
``weld.strategies.test_peer._RESOLVERS_BY_SUFFIX``); only the init
scaffold was missing the pairing.

These tests pin the parity fix:

1. ``wd init`` on a Rust crate emits a ``test_peer`` source entry for
   the Cargo integration-test glob ``**/tests/*.rs``.
2. That entry lands in the ``tests`` artifact-class section, mirroring
   the Python pairing.
3. The ``tree_sitter`` Rust ``**/*.rs`` entry is still emitted.
4. The scaffolded glob actually drives the resolver: feeding it through
   ``test_peer.extract`` against the bundled Rust fixture emits the
   ``tests`` edge.
5. A non-Rust fixture does not emit a Rust ``test_peer`` entry.

Mirrors the per-language pattern set by ``weld_init_java_test.py``.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld._yaml import parse_yaml
from weld.init import init as init_run
from weld.strategies.test_peer import extract as test_peer_extract

_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_RUST_TEST_GLOB = "**/tests/*.rs"


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


class RustTestPeerScaffoldTest(unittest.TestCase):
    """``wd init`` on a Cargo crate wires a ``test_peer`` Rust glob."""

    def setUp(self) -> None:
        self._fixture = _FIXTURES / "tier1" / "rust" / "sample_rust"
        if not self._fixture.is_dir():
            self.skipTest(f"rust tier1 fixture missing: {self._fixture}")

    def test_emits_test_peer_rust_glob(self) -> None:
        data, _raw = _init_fixture(self._fixture)
        peers = _test_peer_sources(data)
        globs = [s.get("glob") for s in peers]
        self.assertIn(
            _RUST_TEST_GLOB, globs,
            f"expected a test_peer entry for {_RUST_TEST_GLOB!r}; got {globs}",
        )

    def test_test_peer_rust_entry_is_file_node(self) -> None:
        data, _raw = _init_fixture(self._fixture)
        rust_peers = [s for s in _test_peer_sources(data)
                      if s.get("glob") == _RUST_TEST_GLOB]
        self.assertTrue(rust_peers, "no rust test_peer entry emitted")
        for src in rust_peers:
            self.assertEqual(
                src.get("type"), "file",
                f"rust test_peer source not type=file: {src}",
            )

    def test_test_peer_entry_under_tests_section(self) -> None:
        # Parity with the Python pairing: the entry belongs to the
        # ``tests`` artifact-class bucket, not ``code``. Section headers
        # are YAML comments, so assert on the raw text ordering: the
        # ``tests`` header must precede the glob and the next class
        # header (``build``) must follow it.
        _data, raw = _init_fixture(self._fixture)
        tests_hdr = raw.find("===== tests =====")
        glob_at = raw.find(f'"{_RUST_TEST_GLOB}"')
        self.assertNotEqual(tests_hdr, -1, "no tests section header emitted")
        self.assertNotEqual(glob_at, -1, "rust test glob not in discover.yaml")
        self.assertLess(
            tests_hdr, glob_at,
            "rust test_peer glob is not under the tests section header",
        )

    def test_tree_sitter_rust_source_still_emitted(self) -> None:
        # The parity fix must not displace the tree_sitter entry that
        # emits the Rust symbol/definition nodes.
        data, _raw = _init_fixture(self._fixture)
        ts_rust = [s for s in data.get("sources", [])
                   if s.get("strategy") == "tree_sitter"
                   and s.get("language") == "rust"]
        self.assertTrue(ts_rust, "tree_sitter rust source dropped")
        self.assertTrue(
            any(s.get("glob") == "**/*.rs" for s in ts_rust),
            f"expected tree_sitter **/*.rs; got {[s.get('glob') for s in ts_rust]}",
        )

    def test_scaffolded_glob_emits_tests_edge(self) -> None:
        # End-to-end: the glob the scaffold writes, fed through the
        # real resolver against the bundled fixture, must emit the
        # tests/<name>.rs -[tests]-> src/<name>.rs edge.
        result = test_peer_extract(
            self._fixture,
            {"glob": _RUST_TEST_GLOB, "type": "file", "strategy": "test_peer"},
            {},
        )
        tests_edges = [e for e in result.edges if e.get("type") == "tests"]
        self.assertTrue(
            tests_edges,
            "scaffolded rust test_peer glob emitted no tests edge",
        )
        froms = {e["from"] for e in tests_edges}
        tos = {e["to"] for e in tests_edges}
        self.assertTrue(
            any(f.endswith("tests/geometry") for f in froms),
            f"expected a tests-edge from tests/geometry; got {sorted(froms)}",
        )
        self.assertTrue(
            any(t.endswith("src/geometry") for t in tos),
            f"expected a tests-edge to src/geometry; got {sorted(tos)}",
        )


class NonRustFixtureIsolationTest(unittest.TestCase):
    """Non-Rust fixtures must NOT emit a Rust ``test_peer`` source."""

    def test_csharp_project_has_no_rust_test_peer(self) -> None:
        data, _raw = _init_fixture(_FIXTURES / "csharp_project")
        rust_peers = [s for s in _test_peer_sources(data)
                      if s.get("glob") == _RUST_TEST_GLOB]
        self.assertFalse(
            rust_peers,
            f"csharp_project must not emit a rust test_peer entry; got {rust_peers}",
        )


if __name__ == "__main__":
    unittest.main()
