"""Directory-form ``exclude:`` coverage for the python_callgraph strategy.

Regression coverage for bd 3abf. ``python_callgraph`` used to resolve its
glob with *no* excludes at all::

    matched, dirs = _resolve_glob(root, pattern)

and relied solely on the per-file ``should_skip(py, excludes, root=root)``
in its parse loop. ``should_skip`` delegates to ``matches_exclude``, which
tests the file path itself with no ancestor-directory check -- so a
directory-form pattern (``pkg/tests``, ``fixtures``) never matches
``pkg/tests/foo.py`` and every file under an excluded directory was parsed
and emitted anyway. Configuring the real repo that way produced ~10.4k
spurious symbol nodes from ``weld/tests`` and ``fixtures``.

Its declared pair partner ``python_module`` passes excludes into
``walk_glob``, which prunes matching *directories* before descending, so
the two halves of the pair resolved different file sets while the
strategy-pair-consistency lint still read as clean. (The reason recorded
here originally -- that the lint compares emitted *file*-node sets and
python_callgraph emits *symbol* nodes -- was a guess and was wrong: the
lint re-derived both members' sets from ``discover.yaml``, so identical
config compared equal whatever the strategies did. bd sf36 added the
emitted half that closes it; see ``weld_strategy_pair_emission_test``.)

This file pins ``python_callgraph``'s own exclude contract. The *pair*
half of the invariant -- that both strategies resolve the identical file
set under a directory-form exclude -- lives with the other pair
assertions in ``weld_python_strategy_pair_test.py``.
"""

from __future__ import annotations

import shutil
import tempfile
import textwrap
import unittest
from pathlib import Path

from weld.strategies import python_callgraph
from weld.strategies._incremental_hint import (
    INCREMENTAL_HINT_KEY,
    IncrementalHint,
)

_GLOB = "pkg/**/*.py"

# Repo-relative fixture paths. Every file defines exactly one function,
# so "was this file parsed?" is answerable from the emitted symbol nodes.
_PROD = "pkg/prod.py"
_TEST_MATERIAL = "pkg/tests/helper_test.py"
_FIXTURE = "pkg/bench/fixtures/sim.py"


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")


class PythonCallgraphExcludeTest(unittest.TestCase):
    """``exclude:`` must prune subtrees, not only exact file paths."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        _write(
            self.root / _PROD,
            """
            def prod_fn():
                return 1
            """,
        )
        _write(
            self.root / _TEST_MATERIAL,
            """
            def helper_fn():
                return 2
            """,
        )
        _write(
            self.root / _FIXTURE,
            """
            def sim_fn():
                return 3
            """,
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    # -- helpers ---------------------------------------------------------

    def _source(self, excludes: list[str] | None) -> dict:
        source: dict = {"glob": _GLOB}
        if excludes is not None:
            source["exclude"] = excludes
        return source

    def _callgraph(self, excludes: list[str] | None):
        return python_callgraph.extract(self.root, self._source(excludes), {})

    def _callgraph_files(self, excludes: list[str] | None) -> set[str]:
        """Files python_callgraph actually parsed, read off its symbols."""
        result = self._callgraph(excludes)
        return {
            node["props"]["file"]
            for node in result.nodes.values()
            if node.get("props", {}).get("file")
        }

    # -- tests -----------------------------------------------------------

    def test_no_excludes_parses_every_matched_file(self) -> None:
        """Control: without excludes the whole glob is still parsed."""
        self.assertEqual(
            self._callgraph_files(None), {_PROD, _TEST_MATERIAL, _FIXTURE}
        )

    def test_segmented_directory_form_prunes_subtree(self) -> None:
        """``pkg/tests`` must exclude everything under ``pkg/tests/``."""
        files = self._callgraph_files(["pkg/tests"])
        self.assertNotIn(_TEST_MATERIAL, files)
        self.assertIn(_PROD, files)

    def test_bare_directory_name_prunes_at_any_depth(self) -> None:
        """``fixtures`` must exclude a ``fixtures/`` dir nested any depth down."""
        files = self._callgraph_files(["fixtures"])
        self.assertNotIn(_FIXTURE, files)
        self.assertIn(_PROD, files)

    def test_excluded_module_emits_no_symbol(self) -> None:
        """No symbol node survives for a directory-excluded module."""
        result = self._callgraph(["pkg/tests"])
        self.assertNotIn("symbol:py:pkg.tests.helper_test:helper_fn", result.nodes)
        self.assertIn("symbol:py:pkg.prod:prod_fn", result.nodes)

    def test_subtree_form_still_honoured(self) -> None:
        """The ``<dir>/**`` form keeps working (it always did)."""
        files = self._callgraph_files(["pkg/tests/**", "**/fixtures/**"])
        self.assertEqual(files, {_PROD})

    def test_discovered_from_omits_excluded_dirs(self) -> None:
        """Nothing under an excluded directory may be advertised as discovered.

        Provenance is per-file since bd od2a, so the claim is now checked
        file by file rather than by the absence of one directory entry --
        which is the stronger reading of the same rule. ``pkg/`` is no
        longer expected at all: a directory entry degenerates to ``"./"``
        at the repo root, the marker that makes every path in the
        repository count as tracked source.
        """
        result = self._callgraph(["pkg/tests"])
        self.assertTrue(result.discovered_from)
        for rel in result.discovered_from:
            self.assertFalse(
                rel.startswith("pkg/tests/"),
                f"{rel} is under an excluded directory",
            )
        self.assertIn("pkg/prod.py", result.discovered_from)

    def test_multiple_directory_forms_compose(self) -> None:
        """Several directory-form patterns in one list all take effect."""
        self.assertEqual(
            self._callgraph_files(["pkg/tests", "fixtures"]), {_PROD}
        )

    def test_incremental_dirty_scoping_inherits_the_exclude(self) -> None:
        """ADR 0074's dirty subset is carved out of the *excluded* set.

        ``dirty_matched`` narrows ``matched``, and ``matched`` is what
        ``_resolve_glob`` returns -- so an excluded file handed to the
        strategy as dirty must still not be parsed. Pinning it here keeps
        the incremental path from becoming a second, unfiltered entry
        point into the same glob.
        """
        hint = IncrementalHint(
            dirty_files=frozenset({_PROD, _TEST_MATERIAL, _FIXTURE}),
            prior_nodes={},
        )
        result = python_callgraph.extract(
            self.root,
            self._source(["pkg/tests", "fixtures"]),
            {INCREMENTAL_HINT_KEY: hint},
        )
        files = {
            node["props"]["file"]
            for node in result.nodes.values()
            if node.get("props", {}).get("file")
        }
        self.assertEqual(files, {_PROD})


if __name__ == "__main__":
    unittest.main()
