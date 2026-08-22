"""Regression test: ``.weld/discover.yaml`` wires the Bazel strategy.

ADR 0044 shipped the ``bazel`` strategy -- ``build-target``/``test-target``
nodes, ``contains`` edges to each ``srcs`` file, ``depends_on`` edges to each
in-repo ``deps`` label -- together with its own unit tests
(``weld_bazel_strategy_test``, ``weld_bazel_labels_test``). Those tests prove
the strategy works when it is *called*. Nothing proved it was ever called
here: this repo's ``.weld/discover.yaml`` never carried a ``strategy: bazel``
source entry, so the graph held zero build targets and the entire ADR 0044
surface was dark in the repo that IS weld.

The failure mode that exposed it is the one this repo cares about most. A
structural question -- "is the tree-sitter wheel reachable from
``//weld/strategies``?" -- did not answer "absent". ``wd query`` ranked
unrelated Python symbols and ``wd context`` reported node-not-found, so the
question had to go to ``bazel query`` instead, and the graph gave no hint that
it simply had nothing to say. A strategy that ships, passes its own tests, and
is never configured fails exactly this quietly. Closes bd 180k.

So this test pins the *wiring*, which the strategy's own tests structurally
cannot: it derives its expectation from the tree (every BUILD file on disk)
and checks it against the config, so a new Bazel package is covered the moment
it lands, and narrowing or deleting the entry fails here.

Scope is decided with :func:`weld._staleness_coverage.in_scope_files` -- the
product's own "would discovery resolve this path?" matcher (ADR 0101) -- so
the config is asserted through the same code that decides coverage
staleness, with no discovery run and no git.

The repo's ``.weld/discover.yaml`` is internal state and is absent from the
published source tree, so the suite skips cleanly when it is not present.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from weld._staleness_coverage import in_scope_files
from weld._yaml import parse_yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DISCOVER_YAML = _REPO_ROOT / ".weld" / "discover.yaml"

_STRATEGY = "bazel"

# Both spellings Bazel accepts for a package definition. The repo uses
# ``BUILD.bazel`` throughout, but the expectation is derived from what is on
# disk rather than from that convention: a bare ``BUILD`` landing must fail
# here -- and so force a config edit -- instead of going undiscovered.
_BUILD_BASENAMES = frozenset({"BUILD", "BUILD.bazel"})

# Directories never descended when enumerating the expectation. Deliberately
# a local, literal list rather than weld's own ``walk_glob``: deriving the
# expectation from the same traversal the config is checked through would make
# the coverage assertion tautological. ``bazel-*`` are output-base symlinks
# (following them walks the entire Bazel cache), ``.claude`` holds SDK
# worktrees -- full repo copies whose BUILD files are duplicates of these.
_PRUNED_DIRS = frozenset({
    ".git", ".claude", ".venv", "node_modules", "__pycache__", ".cache",
})
_PRUNED_PREFIXES = ("bazel-",)

# Simulated foreign projects, excluded by the config per discover.yaml's
# header policy ("not test fixtures"). No fixture carries a BUILD file today;
# the rule is pinned against a synthetic path below so the first one cannot
# quietly inject another project's build graph into weld's own.
_FIXTURE_DIR = "fixtures"
_SYNTHETIC_FIXTURE_BUILD = "weld/bench/fixtures/monorepo/pkg/BUILD.bazel"

# Names the originating report rather than leaving a failure as a set diff.
# The strategies package is where the gap was found: the load-bearing question
# was which wheels ``//weld/strategies`` pulls in.
_REPORTED_PACKAGE = "weld/strategies/BUILD.bazel"


def _sources() -> list[dict]:
    """Parse the repo's checked-in ``.weld/discover.yaml`` source entries."""
    data = parse_yaml(_DISCOVER_YAML.read_text(encoding="utf-8"))
    sources = data.get("sources") or []
    return [entry for entry in sources if isinstance(entry, dict)]


def _bazel_entries() -> list[dict]:
    return [e for e in _sources() if e.get("strategy") == _STRATEGY]


def _all_build_files() -> set[str]:
    """Every BUILD/BUILD.bazel under the repo, as repo-relative posix paths."""
    found: set[str] = set()
    # followlinks=False is what actually stops the ``bazel-*`` output-base
    # symlinks from being descended; the prefix prune below keeps them out of
    # the listing too, so a reader does not have to know that to trust this.
    for dirpath, dirnames, filenames in os.walk(_REPO_ROOT, followlinks=False):
        dirnames[:] = [
            d for d in dirnames
            if d not in _PRUNED_DIRS and not d.startswith(_PRUNED_PREFIXES)
        ]
        rel_dir = Path(dirpath).relative_to(_REPO_ROOT)
        for name in filenames:
            if name in _BUILD_BASENAMES:
                found.add((rel_dir / name).as_posix())
    return found


def _is_fixture(rel: str) -> bool:
    return _FIXTURE_DIR in rel.split("/")[:-1]


@unittest.skipUnless(
    _DISCOVER_YAML.is_file(),
    "repo .weld/discover.yaml not present (e.g. published source tree)",
)
class DiscoverYamlBazelCoverageTest(unittest.TestCase):
    """The bazel strategy must be wired, and cover every Bazel package."""

    def setUp(self) -> None:
        super().setUp()
        self.all_build = _all_build_files()
        self.product = {
            rel for rel in self.all_build if not _is_fixture(rel)
        }
        # Guards the guard: an empty universe would make the coverage
        # assertion below vacuously true, which is precisely the state this
        # test exists to detect.
        self.assertTrue(
            self.product,
            "found no BUILD files under the repo root; the enumeration "
            "prunes too aggressively or the checkout is incomplete",
        )

    def test_bazel_strategy_is_configured(self) -> None:
        # The regression itself. ADR 0044's strategy passed its own tests for
        # months while never running here, because nothing asserted that a
        # shipped strategy is also a configured one.
        entries = _bazel_entries()
        self.assertTrue(
            entries,
            "no 'strategy: bazel' source entry in .weld/discover.yaml, so "
            "the graph holds no build-target nodes and every Bazel "
            "structural question falls through to `bazel query`",
        )

    def test_every_build_file_is_in_scope(self) -> None:
        # The load-bearing assertion. Expectation comes from the tree, so a
        # new Bazel package is covered the moment it lands rather than
        # starting invisible and staying invisible until someone trips on it.
        covered = in_scope_files(_bazel_entries(), self.all_build)
        missing = self.product - covered
        self.assertEqual(
            missing, set(),
            f"the bazel entry does not cover {sorted(missing)}; every "
            "BUILD file that is not fixture material must be resolved",
        )

    def test_glob_is_recursive(self) -> None:
        # Pins the by-construction property. Enumerating one entry per Bazel
        # package would satisfy the coverage assertion at HEAD while
        # restoring the failure mode for the next package to land.
        globs = {e.get("glob") for e in _bazel_entries()}
        self.assertTrue(globs, "the bazel entry declares no glob")
        for glob in globs:
            with self.subTest(glob=glob):
                self.assertIn(
                    "**", glob or "",
                    "Bazel package coverage must be recursive so a new "
                    "package is discoverable without a config edit",
                )

    def test_fixture_build_files_are_not_claimed(self) -> None:
        # The other direction. Fixtures simulate foreign projects, so their
        # targets are not weld's build graph -- discovering them would answer
        # structural questions with another project's wiring. in_scope_files
        # is pure, so the rule is pinned against a synthetic path and does
        # not wait for a real fixture BUILD file to appear.
        self.assertEqual(
            in_scope_files(_bazel_entries(), [_SYNTHETIC_FIXTURE_BUILD]),
            set(),
            f"{_SYNTHETIC_FIXTURE_BUILD} is in scope; fixture BUILD files "
            "must stay excluded (discover.yaml header: not test fixtures)",
        )

    def test_reported_package_is_covered(self) -> None:
        self.assertIn(
            _REPORTED_PACKAGE, self.all_build,
            "fixture path drifted; update _REPORTED_PACKAGE",
        )
        self.assertIn(
            _REPORTED_PACKAGE,
            in_scope_files(_bazel_entries(), [_REPORTED_PACKAGE]),
            f"{_REPORTED_PACKAGE} is not covered by the bazel entry -- the "
            "package the originating dogfood gap was filed against",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
