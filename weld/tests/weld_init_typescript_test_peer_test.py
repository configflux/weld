"""Acceptance tests for the TypeScript test_peer scaffold in ``wd init``.

``weld/init.py`` pairs every Python test glob with both a
``python_module`` and a ``test_peer`` source entry (ADR 0046). The
tree-sitter languages historically got only a ``tree_sitter`` source
entry, so a stock ``wd init`` on a TypeScript project never scaffolded
the ``test_peer`` entry that surfaces ``foo.test.ts`` and emits the
``tests`` edge to its same-directory ``foo.ts`` peer. The
``_test_peer_ts`` resolver already works (it is registered in
``weld.strategies.test_peer._RESOLVERS_BY_SUFFIX`` for ``.ts`` /
``.tsx`` / ``.js`` / ``.jsx``); only the init scaffold was missing the
pairing.

This is the deferred TypeScript sibling of the Rust scaffold pinned by
``weld_init_rust_test_peer_test.py``. TypeScript has no single test
directory; the conventions ``_test_peer_ts.is_test_file`` recognizes
are ``*.test.{ts,...}``, ``*.spec.{ts,...}``, and ``__tests__/`` files.
The scaffold therefore emits one entry per shape -- but only shapes
this resolver actually pairs, so a stock init never writes a glob the
resolver would ignore.

These tests pin the parity fix:

1. ``wd init`` on a TS project emits a ``test_peer`` source entry for
   the standard test glob ``**/*.test.ts``.
2. Every scaffolded TS ``test_peer`` glob is one of the shapes the
   ``_test_peer_ts`` resolver recognizes (no glob the resolver ignores).
3. The TS ``test_peer`` entries land in the ``tests`` artifact-class
   section, mirroring the Python pairing.
4. The ``tree_sitter`` TypeScript entry is still emitted, and its glob still
   claims every ``.ts`` file in the fixture (the claim, not its spelling --
   ADR 0142 D1 made that glob the ``{ts,tsx}`` dialect family).
5. The scaffolded standard glob actually drives the resolver: feeding
   it through ``test_peer.extract`` against the bundled TS fixture emits
   the ``tests`` edge.
6. A non-TS fixture does not emit a TS ``test_peer`` entry.

Mirrors the per-language pattern set by ``weld_init_rust_test_peer_test.py``.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld._yaml import parse_yaml
from weld.init import init as init_run
from weld.strategies._glob_resolve import resolve_glob
from weld.strategies._test_peer_ts import is_test_file as ts_is_test_file
from weld.strategies.test_peer import extract as test_peer_extract

_FIXTURES = Path(__file__).resolve().parent / "fixtures"
#: The standard Jest/Vitest convention; the only shape present in the
#: bundled TS fixture, so the only one with an end-to-end edge assertion.
_TS_STANDARD_TEST_GLOB = "**/*.test.ts"


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


def _is_ts_glob(glob: str) -> bool:
    """Return True iff *glob* targets a TS/JS source extension."""
    return glob.endswith((".ts", ".tsx", ".js", ".jsx"))


def _sample_filename_for(glob: str) -> str:
    """Map a scaffolded TS glob to a representative relative test path.

    Used to verify the scaffold never emits a glob the resolver
    ignores: each scaffolded glob is reduced to a concrete filename
    (``**/*.test.ts`` -> ``mod.test.ts``, ``**/__tests__/*.ts`` ->
    ``__tests__/mod.ts``) and fed through ``_test_peer_ts.is_test_file``.
    """
    tail = glob.rsplit("/", 1)[-1]  # e.g. "*.test.ts" or "*.ts"
    concrete = tail.replace("*", "mod")
    if "__tests__" in glob:
        return f"pkg/__tests__/{concrete}"
    return f"pkg/{concrete}"


class TypeScriptTestPeerScaffoldTest(unittest.TestCase):
    """``wd init`` on a TS project wires ``test_peer`` TS globs."""

    def setUp(self) -> None:
        self._fixture = _FIXTURES / "tier1" / "typescript" / "sample_typescript"
        if not self._fixture.is_dir():
            self.skipTest(f"typescript tier1 fixture missing: {self._fixture}")

    def test_emits_standard_test_peer_ts_glob(self) -> None:
        data, _raw = _init_fixture(self._fixture)
        peers = _test_peer_sources(data)
        globs = [s.get("glob") for s in peers]
        self.assertIn(
            _TS_STANDARD_TEST_GLOB, globs,
            f"expected a test_peer entry for {_TS_STANDARD_TEST_GLOB!r}; "
            f"got {globs}",
        )

    def test_every_scaffolded_ts_glob_is_resolver_recognized(self) -> None:
        # Guard against scaffolding a glob the resolver would ignore:
        # every TS test_peer glob this init writes must reduce to a
        # filename that ``_test_peer_ts.is_test_file`` accepts.
        data, _raw = _init_fixture(self._fixture)
        ts_globs = [s.get("glob") for s in _test_peer_sources(data)
                    if _is_ts_glob(s.get("glob", ""))]
        self.assertTrue(ts_globs, "no TS test_peer globs emitted")
        for glob in ts_globs:
            rel = Path(_sample_filename_for(glob))
            self.assertTrue(
                ts_is_test_file(rel),
                f"scaffolded TS glob {glob!r} reduces to {rel} which the "
                f"_test_peer_ts resolver does not recognize as a test file",
            )

    def test_ts_test_peer_entries_are_file_nodes(self) -> None:
        data, _raw = _init_fixture(self._fixture)
        ts_peers = [s for s in _test_peer_sources(data)
                    if _is_ts_glob(s.get("glob", ""))]
        self.assertTrue(ts_peers, "no TS test_peer entry emitted")
        for src in ts_peers:
            self.assertEqual(
                src.get("type"), "file",
                f"TS test_peer source not type=file: {src}",
            )

    def test_standard_test_peer_entry_under_tests_section(self) -> None:
        # Parity with the Python pairing: the entry belongs to the
        # ``tests`` artifact-class bucket, not ``code``. Section headers
        # are YAML comments, so assert on the raw text ordering.
        _data, raw = _init_fixture(self._fixture)
        tests_hdr = raw.find("===== tests =====")
        glob_at = raw.find(f'"{_TS_STANDARD_TEST_GLOB}"')
        self.assertNotEqual(tests_hdr, -1, "no tests section header emitted")
        self.assertNotEqual(glob_at, -1, "TS test glob not in discover.yaml")
        self.assertLess(
            tests_hdr, glob_at,
            "TS test_peer glob is not under the tests section header",
        )

    def test_tree_sitter_typescript_source_still_emitted(self) -> None:
        # The parity fix must not displace the tree_sitter entry that
        # emits the TS symbol/definition nodes.
        #
        # Asserted by *resolving* the emitted globs against the fixture rather
        # than by matching a literal ``**/*.ts``: the spelling is not the
        # claim. ADR 0142 D1 widened the entry to the dialect family
        # ``**/*.{ts,tsx}`` because a per-extension glob left every ``.tsx``
        # file init had just counted unclaimed, and a test pinned to the old
        # string would have called that widening a regression while a genuine
        # narrowing -- say back to ``src/*.ts`` -- slipped past it.
        data, _raw = _init_fixture(self._fixture)
        ts_src = [s for s in data.get("sources", [])
                  if s.get("strategy") == "tree_sitter"
                  and s.get("language") == "typescript"]
        self.assertTrue(ts_src, "tree_sitter typescript source dropped")
        claimed = {
            path.relative_to(self._fixture).as_posix()
            for src in ts_src
            for path in resolve_glob(
                self._fixture, str(src.get("glob", "")), src.get("exclude") or [],
            )
        }
        expected = {
            path.relative_to(self._fixture).as_posix()
            for path in self._fixture.rglob("*.ts")
        }
        self.assertTrue(expected, "the TS fixture has no .ts files to claim")
        self.assertEqual(
            sorted(expected - claimed), [],
            "the tree_sitter typescript entry claims none of these fixture "
            f"sources; its globs are {[s.get('glob') for s in ts_src]}",
        )

    def test_scaffolded_glob_emits_tests_edge(self) -> None:
        # End-to-end: the standard glob the scaffold writes, fed through
        # the real resolver against the bundled fixture, must emit the
        # foo.test.ts -[tests]-> foo.ts edge.
        result = test_peer_extract(
            self._fixture,
            {"glob": _TS_STANDARD_TEST_GLOB, "type": "file",
             "strategy": "test_peer"},
            {},
        )
        tests_edges = [e for e in result.edges if e.get("type") == "tests"]
        self.assertTrue(
            tests_edges,
            "scaffolded TS test_peer glob emitted no tests edge",
        )
        froms = {e["from"] for e in tests_edges}
        tos = {e["to"] for e in tests_edges}
        self.assertTrue(
            any(f.endswith("src/geometry.test") for f in froms),
            f"expected a tests-edge from geometry.test; got {sorted(froms)}",
        )
        self.assertTrue(
            any(t.endswith("src/geometry") for t in tos),
            f"expected a tests-edge to geometry; got {sorted(tos)}",
        )


class NonTypeScriptFixtureIsolationTest(unittest.TestCase):
    """Non-TS fixtures must NOT emit a TS ``test_peer`` source."""

    def test_csharp_project_has_no_ts_test_peer(self) -> None:
        data, _raw = _init_fixture(_FIXTURES / "csharp_project")
        ts_peers = [s for s in _test_peer_sources(data)
                    if _is_ts_glob(s.get("glob", ""))]
        self.assertFalse(
            ts_peers,
            f"csharp_project must not emit a TS test_peer entry; got {ts_peers}",
        )


if __name__ == "__main__":
    unittest.main()
