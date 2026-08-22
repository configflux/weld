"""Regression test: ``.weld/discover.yaml`` covers every weld Python package.

Replaces the narrower ``discover_yaml_viz_globs_test``, which pinned that
one subpackage (``weld/viz/*.py``) carried the canonical Python trio. That
test could only ever catch the removal of the entry it named, so the same
dogfood gap it was written to close reopened five more times: weld/bench,
weld/bench/adapters, weld/bench_tasks, weld/cross_repo and weld/providers
were each invisible to the connected structure, and structural queries for
their symbols did not answer "absent" -- they answered with confidently
wrong neighbours from unrelated subsystems.

The config now resolves ``weld/**/*.py`` recursively, so this test pins the
property rather than an enumeration: **every Python file under ``weld/``
that is not test material must be resolved by all three canonical
strategies**. A new subpackage is covered the moment it lands; narrowing
the config, or excluding a product directory, fails here.

Scope is decided with :func:`weld._staleness_coverage.in_scope_files` -- the
product's own "would discovery resolve this path?" matcher (ADR 0101),
which applies each entry's ``exclude`` list with the same semantics
``walk_glob`` applies during descent. Using it here means the config is
asserted through the same code that decides coverage staleness, and needs
no git or discovery run.

The exclude *form* is pinned separately. All three strategies now resolve
their glob through ``walk_glob`` and honour the directory form equally
(bd 3abf fixed python_callgraph, which used to resolve its glob with no
excludes at all), but only the subtree form also matches the file *path*
directly -- which is what matchers that test a path list with no descent
to prune need. Keeping the config in the subtree form means it stays
correct under every matcher, not just the pruning ones.

The repo's ``.weld/discover.yaml`` is internal state and is absent from the
published source tree, so the suite skips cleanly when it is not present.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from weld._staleness_coverage import in_scope_files
from weld._yaml import parse_yaml
from weld.glob_match import matches_exclude

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DISCOVER_YAML = _REPO_ROOT / ".weld" / "discover.yaml"

# python_module emits the ``file:`` anchors, python_callgraph the
# ``symbol:`` nodes and calls edges, python_package the
# ``package:python:*`` node plus contains edges into those anchors.
# A subpackage missing any one of the three is only partly queryable.
_TRIO = ("python_module", "python_callgraph", "python_package")

# Test material, excluded from the trio by ``.weld/discover.yaml``:
#   weld/tests -- owned by the ``test_peer`` entry, which emits the
#                 ``tests`` edge to each production peer.
#   fixtures   -- simulated foreign projects under weld/bench and
#                 weld/bench_tasks; not weld's own source.
_TEST_TREE = "weld/tests"
_FIXTURE_DIR = "fixtures"

# Subpackages named in the originating report. Redundant with the
# whole-tree assertion below, but names the regression so a failure
# points straight at the gap class instead of a set diff.
_REPORTED_INVISIBLE = (
    "weld/bench/bench_cli.py",
    "weld/bench/adapters/weld.py",
    "weld/bench_tasks/tasks.py",
    "weld/cross_repo/service_graph.py",
    "weld/providers/anthropic.py",
)


def _sources() -> list[dict]:
    """Parse the repo's checked-in ``.weld/discover.yaml`` source entries."""
    data = parse_yaml(_DISCOVER_YAML.read_text(encoding="utf-8"))
    sources = data.get("sources") or []
    return [entry for entry in sources if isinstance(entry, dict)]


def _entries_for(strategy: str) -> list[dict]:
    return [e for e in _sources() if e.get("strategy") == strategy]


def _weld_scoped_entries() -> list[dict]:
    """Trio entries whose glob targets the ``weld/`` source tree."""
    return [
        e for e in _sources()
        if e.get("strategy") in _TRIO
        and str(e.get("glob") or "").startswith("weld/")
    ]


def _is_test_material(rel: str) -> bool:
    parts = rel.split("/")
    return rel.startswith(_TEST_TREE + "/") or _FIXTURE_DIR in parts[:-1]


def _all_weld_python_files() -> set[str]:
    """Every ``.py`` file under ``weld/``, as repo-relative posix paths."""
    return {
        p.relative_to(_REPO_ROOT).as_posix()
        for p in (_REPO_ROOT / "weld").rglob("*.py")
        if p.is_file()
    }


@unittest.skipUnless(
    _DISCOVER_YAML.is_file(),
    "repo .weld/discover.yaml not present (e.g. published source tree)",
)
class DiscoverYamlPythonCoverageTest(unittest.TestCase):
    """The trio must cover all weld product Python, by construction."""

    def setUp(self) -> None:
        super().setUp()
        self.all_python = _all_weld_python_files()
        self.product = {
            rel for rel in self.all_python if not _is_test_material(rel)
        }
        # Guards the guard: an empty or test-only universe would make
        # every assertion below vacuously true.
        self.assertTrue(self.product, "found no weld product Python files")

    def test_every_product_module_is_covered_by_each_trio_strategy(self) -> None:
        # The load-bearing assertion. Expectation is derived from the tree,
        # not from the config, so adding weld/newpkg/thing.py without
        # covering it fails here rather than surfacing months later as a
        # query that answers confidently wrong.
        for strategy in _TRIO:
            with self.subTest(strategy=strategy):
                covered = in_scope_files(
                    _entries_for(strategy), self.all_python
                )
                missing = self.product - covered
                self.assertEqual(
                    missing, set(),
                    f"{strategy} does not cover {sorted(missing)}; "
                    "every Python file under weld/ that is not test "
                    "material must be resolved by the canonical trio",
                )

    def test_test_material_is_not_claimed_by_the_trio(self) -> None:
        # The other direction: weld/tests belongs to the test_peer entry
        # (two strategies claiming the same file: anchors is ambiguous
        # ownership), and fixtures are simulated foreign projects whose
        # directories must not become weld package nodes.
        test_material = self.all_python - self.product
        self.assertTrue(test_material, "expected weld/tests to hold Python")
        for strategy in _TRIO:
            with self.subTest(strategy=strategy):
                leaked = in_scope_files(
                    _entries_for(strategy), self.all_python
                ) & test_material
                self.assertEqual(
                    leaked, set(),
                    f"{strategy} claims test material: {sorted(leaked)[:5]}",
                )

    def test_excludes_match_file_paths_not_only_directories(self) -> None:
        # All three strategies hand excludes to ``walk_glob``, which
        # prunes matching *directories* during descent, so a bare
        # directory name is honoured by each of them (bd 3abf; before it,
        # python_callgraph resolved its glob with no excludes at all and
        # emitted 10.4k spurious symbol nodes from the trees this list
        # names). Descent-based pruning is not the only consumer, though:
        # ``matches_exclude`` tests a path with no ancestor-directory
        # check, and every caller that matches a *path list* rather than
        # walking a tree gets that behaviour -- ``files:``/``path:``
        # entries in ``resolve_source_files``, and any future one. The
        # subtree form matches the file path directly, so it is the form
        # that stays correct under both kinds of matcher; pinning it here
        # keeps the config from drifting to the narrower one.
        entries = _weld_scoped_entries()
        excludes = [p for e in entries for p in (e.get("exclude") or [])]
        self.assertTrue(excludes, "weld/ trio entries declare no excludes")
        for rel in sorted(self.all_python - self.product):
            with self.subTest(path=rel):
                self.assertTrue(
                    matches_exclude(rel, excludes),
                    f"{rel} is excluded only by directory pruning, so a "
                    "path-list matcher would still treat it as in scope. "
                    "Use the <dir>/** subtree form in .weld/discover.yaml",
                )

    def test_trio_entries_share_one_glob_and_exclude_list(self) -> None:
        # python_module and python_callgraph are a declared strategy pair
        # (ADR 0041 § Layer 3): they must visit the same file set or
        # ``wd lint`` reports strategy-pair-consistency violations.
        # python_package rides the same glob so every file anchor gets its
        # inbound contains edge. Divergence is easiest to introduce by
        # editing one entry's exclude list and forgetting the others.
        entries = _weld_scoped_entries()
        self.assertEqual(
            {e.get("strategy") for e in entries}, set(_TRIO),
            "the weld/ source tree must be covered by exactly the "
            f"canonical trio; got {sorted({e.get('strategy') for e in entries})}",
        )
        shapes = {
            (e.get("glob"), tuple(e.get("exclude") or ())) for e in entries
        }
        self.assertEqual(
            len(shapes), 1,
            "all weld/ trio entries must carry the identical glob and "
            f"exclude list; got {sorted(shapes)}",
        )

    def test_weld_glob_is_recursive(self) -> None:
        # Pins the by-construction property itself. Re-enumerating one
        # entry per subpackage would satisfy the coverage assertion above
        # at HEAD while restoring the failure mode: the next subpackage
        # to land starts invisible and stays invisible until someone
        # trips over it.
        globs = {e.get("glob") for e in _weld_scoped_entries()}
        for glob in globs:
            with self.subTest(glob=glob):
                self.assertIn(
                    "**", glob or "",
                    "weld/ Python coverage must be recursive so a new "
                    "subpackage is discoverable without a config edit",
                )

    def test_previously_invisible_subpackages_are_covered(self) -> None:
        for rel in _REPORTED_INVISIBLE:
            with self.subTest(path=rel):
                self.assertIn(
                    rel, self.all_python,
                    "fixture path drifted; update _REPORTED_INVISIBLE",
                )
                for strategy in _TRIO:
                    self.assertIn(
                        rel, in_scope_files(_entries_for(strategy), [rel]),
                        f"{rel} is not covered by {strategy}",
                    )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
