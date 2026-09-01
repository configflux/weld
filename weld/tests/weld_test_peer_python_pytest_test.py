"""Pytest ``test_*.py`` convention tests for the Python test_peer helper.

Split out from ``weld_test_peer_strategy_test.py`` so that file stays
under the 400-line source cap. Covers the pytest default
``python_files = test_*.py`` branch, which is the mirror of the
legacy ``*_test.py`` (Bazel / Go-style) suffix that the original
strategy already handled.

Acceptance criteria covered here:

1. is_test_file('test_foo.py') returns True
2. is_test_file('foo_test.py') still returns True
3. is_test_file('test.py') returns False
4. resolve_peer recognises both directions

The legacy suffix classes remain in
``weld_test_peer_strategy_test.py``; the multilang dispatcher tests
remain in ``weld_test_peer_multilang_test.py``.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld.strategies import _test_peer_python
from weld.strategies._helpers import StrategyResult
from weld.strategies.test_peer import extract


def _touch(path: Path, content: str = "") -> None:
    """Create *path* with *content*, creating parents as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestIsTestFilePytestConvention(unittest.TestCase):
    """is_test_file must recognise pytest ``test_*.py`` filenames.

    Pytest's default discovery (``python_files = test_*.py``) is used
    by the majority of Python projects (black, flask, httpx, poetry,
    plus most pinned Tier-1 corpora). Without this branch the
    framework_strategies criterion check reports ``fail`` for every
    pytest-configured corpus even though every other binding criterion
    passes.
    """  # test-hygiene: allow uncited-pin -- "pinned corpora" is an adjective

    def test_pytest_prefix_recognised(self) -> None:
        # The new branch: leading ``test_`` plus at least one extra char.
        self.assertTrue(
            _test_peer_python.is_test_file(Path("tests/test_foo.py")),
        )

    def test_legacy_suffix_still_recognised(self) -> None:
        # No regression on the original ``*_test.py`` Bazel convention.
        self.assertTrue(
            _test_peer_python.is_test_file(Path("weld/tests/foo_test.py")),
        )

    def test_bare_test_module_not_recognised(self) -> None:
        # ``test.py`` is ambiguous -- conservative is False so a stray
        # ``test.py`` helper does not produce a stray test node and
        # therefore no spurious ``tests`` edge.
        self.assertFalse(
            _test_peer_python.is_test_file(Path("foo/test.py")),
        )

    def test_leading_underscore_test_not_recognised(self) -> None:
        # ``_test.py`` is the bare suffix on its own -- not a test
        # module. Also covered by the existing legacy branch but
        # restated here so the pytest extension does not regress it.
        self.assertFalse(
            _test_peer_python.is_test_file(Path("foo/_test.py")),
        )

    def test_pytest_prefix_with_empty_base_not_recognised(self) -> None:
        # ``test_.py`` has a leading ``test_`` but no name after --
        # ambiguous, treated the same as bare ``test.py``.
        self.assertFalse(
            _test_peer_python.is_test_file(Path("foo/test_.py")),
        )

    def test_non_py_extension_not_recognised(self) -> None:
        # The strategy is python-specific; ``.txt`` matches the prefix
        # textually but must not produce a test node.
        self.assertFalse(
            _test_peer_python.is_test_file(Path("foo/test_foo.txt")),
        )


class TestCandidatePeerStemsPytestConvention(unittest.TestCase):
    """candidate_peer_stems must convert ``test_foo`` -> ``foo``.

    The mirror of the existing Go-style ``foo_test`` -> ``foo``
    mapping. Without this, even if is_test_file accepts the new
    convention, the strategy emits a test node without a peer edge so
    the criterion 3 check for ``test_peer`` output (file nodes with
    kind='test' from source_strategy='test_peer') still records the
    same number of pytest-emitting tests -- but the new prefix shape
    benefits from the peer edge so the criterion's diagnostic is
    accurate.
    """

    def test_pytest_prefix_stem_strips_to_base(self) -> None:
        self.assertEqual(
            _test_peer_python.candidate_peer_stems("test_foo"),
            ["foo"],
        )

    def test_legacy_suffix_stem_still_strips_to_base(self) -> None:
        # Original behaviour preserved for ``foo_test`` -> ``foo``.
        self.assertEqual(
            _test_peer_python.candidate_peer_stems("foo_test"),
            ["foo"],
        )

    def test_weld_prefix_still_strips_under_legacy_suffix(self) -> None:
        # The existing ``weld_<area>_test`` -> ``weld_<area>`` plus
        # ``<area>`` fallback chain must remain intact.
        self.assertEqual(
            _test_peer_python.candidate_peer_stems("weld_foo_test"),
            ["weld_foo", "foo"],
        )

    def test_bare_test_stem_returns_empty(self) -> None:
        # Defensive: ``test`` alone is ambiguous (matches neither
        # convention with a real name) and must yield no candidates.
        self.assertEqual(
            _test_peer_python.candidate_peer_stems("test"),
            [],
        )

    def test_bare_underscore_test_stem_returns_empty(self) -> None:
        self.assertEqual(
            _test_peer_python.candidate_peer_stems("_test"),
            [],
        )

    def test_pytest_prefix_with_empty_base_returns_empty(self) -> None:
        # ``test_`` with no trailing name has no peer to map to.
        self.assertEqual(
            _test_peer_python.candidate_peer_stems("test_"),
            [],
        )


class TestResolvePeerPytestConvention(unittest.TestCase):
    """resolve_peer must locate peers for ``test_foo.py`` files.

    Mirrors the existing ``test_falls_back_to_unprefixed_peer`` /
    ``test_falls_back_to_underscore_filename_peer`` legacy tests but
    exercises the new pytest prefix branch. The peer search continues
    to use grandparent-directory placement (``test_foo.py`` in
    ``project/tests/`` looks for ``project/foo.py``) so the resolver
    behaves the same as the legacy convention does for Bazel-style
    repos.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        # pytest-style: project/foo.py with project/tests/test_foo.py
        _touch(self.root / "project" / "foo.py", "x = 1\n")
        _touch(
            self.root / "project" / "tests" / "test_foo.py",
            "import pytest\n",
        )
        # pytest-style with private peer: project/_helper.py paired with
        # project/tests/test_helper.py (mirror of the legacy
        # ``_internal_helper.py`` <-> ``internal_helper_test.py`` case).
        _touch(self.root / "project" / "_helper.py", "x = 1\n")
        _touch(
            self.root / "project" / "tests" / "test_helper.py",
            "import pytest\n",
        )
        # pytest-style with no peer on disk -- must yield no edge.
        _touch(
            self.root / "project" / "tests" / "test_orphan.py",
            "import pytest\n",
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self) -> StrategyResult:
        source = {
            "glob": "project/tests/test_*.py",
            "type": "file",
            "strategy": "test_peer",
        }
        return extract(self.root, source, {})

    def test_pytest_prefix_emits_node(self) -> None:
        result = self._run()
        self.assertIn(
            "file:project/tests/test_foo", result.nodes,
        )
        node = result.nodes["file:project/tests/test_foo"]
        self.assertEqual(node["props"]["kind"], "test")
        self.assertEqual(node["props"]["roles"], ["test"])
        self.assertEqual(node["props"]["source_strategy"], "test_peer")

    def test_pytest_prefix_emits_edge_to_peer(self) -> None:
        result = self._run()
        edges = [
            e for e in result.edges
            if e["from"] == "file:project/tests/test_foo"
        ]
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["to"], "file:project/foo")
        self.assertEqual(edges[0]["type"], "tests")
        self.assertEqual(edges[0]["props"]["confidence"], "inferred")
        self.assertEqual(edges[0]["props"]["source_strategy"], "test_peer")

    def test_pytest_prefix_falls_back_to_underscore_peer(self) -> None:
        # test_helper.py -> _helper.py (the production module is
        # private; matches the underscore filename fallback).
        result = self._run()
        edges = [
            e for e in result.edges
            if e["from"] == "file:project/tests/test_helper"
        ]
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["to"], "file:project/_helper")
        self.assertEqual(edges[0]["type"], "tests")

    def test_pytest_prefix_no_edge_when_peer_missing(self) -> None:
        result = self._run()
        edges = [
            e for e in result.edges
            if e["from"] == "file:project/tests/test_orphan"
        ]
        self.assertEqual(edges, [])


class TestFirstCandidatePeerIdPytestConvention(unittest.TestCase):
    """first_candidate_peer_id must mirror is_test_file recognition.

    The provenance-only helper must return a peer id for the new
    pytest prefix shape, otherwise downstream callers that ask for
    the canonical peer id before disk lookup get ``None`` for any
    test_*.py file.
    """

    def test_pytest_prefix_returns_canonical_peer_id(self) -> None:
        peer_id = _test_peer_python.first_candidate_peer_id(
            Path("project/tests/test_foo.py"),
        )
        self.assertEqual(peer_id, "file:project/foo")

    def test_legacy_suffix_returns_canonical_peer_id(self) -> None:
        peer_id = _test_peer_python.first_candidate_peer_id(
            Path("project/tests/foo_test.py"),
        )
        self.assertEqual(peer_id, "file:project/foo")

    def test_bare_test_module_returns_none(self) -> None:
        self.assertIsNone(
            _test_peer_python.first_candidate_peer_id(
                Path("project/tests/test.py"),
            ),
        )


class TestResolvePeerSrcLayout(unittest.TestCase):
    """resolve_peer must find peers in ``src/<pkg>/<base>.py`` layout.

    The pinned Tier-1 corpora (black, flask, poetry) put tests at
    ``tests/test_x.py`` and the production peer at ``src/<pkg>/x.py``.
    Without this branch the strategy emits zero ``tests`` edges, so
    framework_strategies criterion 3 fails.
    """  # test-hygiene: allow uncited-pin -- "pinned corpora" is an adjective

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _touch(self.root / "src" / "flask" / "signals.py", "x = 1\n")
        _touch(self.root / "tests" / "test_signals.py", "import pytest\n")
        _touch(self.root / "src" / "poetry" / "factory.py", "x = 1\n")
        _touch(self.root / "tests" / "test_factory.py", "import pytest\n")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_src_layout_resolves_first_package(self) -> None:
        self.assertEqual(
            _test_peer_python.resolve_peer(
                self.root, Path("tests/test_signals.py"),
            ),
            ("file:src/flask/signals", "src/flask/signals.py"),
        )

    def test_src_layout_resolves_second_package(self) -> None:
        self.assertEqual(
            _test_peer_python.resolve_peer(
                self.root, Path("tests/test_factory.py"),
            ),
            ("file:src/poetry/factory", "src/poetry/factory.py"),
        )

    def test_src_layout_no_peer_when_stem_missing(self) -> None:
        self.assertIsNone(
            _test_peer_python.resolve_peer(
                self.root, Path("tests/test_missing.py"),
            ),
        )


class TestResolvePeerPackageFlatLayout(unittest.TestCase):
    """resolve_peer must find peers in ``<pkg>/<base>.py`` flat layout.

    httpx is the canonical pinned corpus using this shape: production
    package sits at ``httpx/`` directly under root, tests at
    ``tests/test_x.py`` (or ``tests/<sub>/test_x.py``).
    """  # test-hygiene: allow uncited-pin -- "pinned corpus" is an adjective

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _touch(self.root / "httpx" / "api.py", "x = 1\n")
        _touch(self.root / "tests" / "test_api.py", "import pytest\n")
        _touch(self.root / "httpx" / "_auth.py", "x = 1\n")
        _touch(
            self.root / "tests" / "client" / "test_auth.py",
            "import pytest\n",
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_top_level_package_resolves(self) -> None:
        self.assertEqual(
            _test_peer_python.resolve_peer(
                self.root, Path("tests/test_api.py"),
            ),
            ("file:httpx/api", "httpx/api.py"),
        )

    def test_nested_test_dir_resolves_via_underscore_fallback(self) -> None:
        self.assertEqual(
            _test_peer_python.resolve_peer(
                self.root, Path("tests/client/test_auth.py"),
            ),
            ("file:httpx/_auth", "httpx/_auth.py"),
        )

    def test_grandparent_wins_when_both_match(self) -> None:
        # Ambiguity guard: grandparent (``httpx/lib.py``) must win
        # over the package-flat scan (``./lib.py``) so the legacy
        # Bazel layout stays stable.
        _touch(self.root / "httpx" / "lib.py", "x = 1\n")
        _touch(
            self.root / "httpx" / "tests" / "test_lib.py",
            "import pytest\n",
        )
        _touch(self.root / "lib.py", "y = 1\n")
        self.assertEqual(
            _test_peer_python.resolve_peer(
                self.root, Path("httpx/tests/test_lib.py"),
            ),
            ("file:httpx/lib", "httpx/lib.py"),
        )


class TestResolvePeerLayoutSkips(unittest.TestCase):
    """Non-production directories must not match the package-flat scan.

    Pinned corpora have ``docs/`` / ``.cache/`` / hidden roots that
    the resolver must skip; otherwise the scan would pair
    ``tests/test_foo.py`` with ``docs/foo.py`` (the wrong peer).
    """  # test-hygiene: allow uncited-pin -- "pinned corpora" is an adjective

    def test_docs_and_hidden_dirs_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _touch(root / "docs" / "foo.py", "x = 1\n")
            _touch(root / ".cache" / "foo.py", "x = 1\n")
            _touch(root / "tests" / "test_foo.py", "import pytest\n")
            self.assertIsNone(
                _test_peer_python.resolve_peer(
                    root, Path("tests/test_foo.py"),
                ),
            )


if __name__ == "__main__":
    unittest.main()
