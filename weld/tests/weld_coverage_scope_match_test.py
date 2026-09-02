"""Scope matching for coverage staleness (ADR 0101).

``in_scope_files`` decides "would discovery resolve this path?" by matching a
known path list, because the read path cannot afford the glob walk that
``resolve_source_files`` performs (~730 ms on this repo). That makes it a
second implementation of scope resolution, and these tests are what keep the
two from drifting.

The two directions of disagreement are not symmetric. Under-reporting scope
costs a missed detection. **Over**-reporting marks a file the discovery state
can never cover, which means staleness on every read forever -- so the
never-over-report property is asserted on its own, not merely implied by the
equality test beside it.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from weld._staleness_coverage import in_scope_files
from weld.tests._coverage_stale_lib import (
    indexed_files,
    make_repo,
    sources,
    walked_files,
)


class InScopeEquivalenceTest(unittest.TestCase):
    """The path-list matcher must agree with a real glob walk."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        make_repo(self.root)

    def _matched(self) -> set[str]:
        return in_scope_files(sources(self.root), indexed_files(self.root))

    def test_matcher_never_over_reports(self) -> None:
        # The load-bearing direction: anything the matcher calls in-scope
        # that a walk would not resolve becomes a file the state can never
        # cover, i.e. permanent staleness and a refresh on every read.
        extra = self._matched() - walked_files(self.root)
        self.assertEqual(extra, set(), f"matcher over-reported scope: {extra}")

    def test_matcher_agrees_with_the_walk(self) -> None:
        self.assertEqual(self._matched(), walked_files(self.root))

    def test_single_directory_glob_does_not_recurse(self) -> None:
        self.assertIn("src/a.py", self._matched())
        self.assertNotIn("src/deep/c.py", self._matched())

    def test_globstar_glob_recurses(self) -> None:
        self.assertIn("pkg/sub/two.py", self._matched())

    def test_wildcard_directory_segment_glob_resolves(self) -> None:
        # bd uhxjc: the matcher always translated `apps/*/package.json`
        # correctly; `walk_glob`'s flat branch resolved nothing for it, so
        # these two files were in scope and permanently uncoverable. Named
        # here as well as caught by the two equivalence cases above, because
        # this is the shape the shared fixture used not to carry at all.
        self.assertIn("apps/a/package.json", self._matched())
        self.assertIn("apps/b/package.json", self._matched())

    def test_wildcard_directory_segment_glob_spans_one_segment(self) -> None:
        self.assertNotIn("apps/b/nested/package.json", self._matched())

    def test_brace_alternative_glob_resolves_every_alternative(self) -> None:
        # bd 2z5no: `walk_glob` expands `{json,toml}` into one pattern per
        # alternative before walking; the matcher translated the pattern as
        # written, and `_glob_pattern_to_regex` escapes `{` into a literal, so
        # the entry matched nothing at all. Under-reporting, so it cost
        # detections rather than refresh loops -- which is why it was silent.
        # Reachable on every stock Node repo: `wd init` writes `**/*.{ts,tsx}`
        # and `**/*.{js,jsx,mjs,cjs}`, so the never-ingested signal was dead
        # for the whole language. Named here as well as caught by the two
        # equivalence cases above, because this is a shape the shared fixture
        # used not to carry at all.
        self.assertIn("cfg/app.json", self._matched())
        self.assertIn("cfg/app.toml", self._matched())

    def test_brace_alternative_glob_matches_only_its_alternatives(self) -> None:
        # The narrow half: a group is its alternatives, never "any suffix".
        # Without this, expanding to a wildcard would pass the case above and
        # put every neighbouring file permanently in scope.
        self.assertNotIn("cfg/app.yaml", self._matched())

    def test_pattern_exclude_applies(self) -> None:
        self.assertNotIn("pkg/vendor/dep.py", self._matched())

    def test_bare_directory_name_exclude_applies(self) -> None:
        # "generated" names a directory, not the file inside it; only the
        # ancestor check keeps pkg/generated/gen.py out of scope.
        self.assertNotIn("pkg/generated/gen.py", self._matched())

    def test_files_key_resolves(self) -> None:
        self.assertIn("MODULE.bazel", self._matched())

    def test_glob_without_wildcard_resolves(self) -> None:
        self.assertIn("README.md", self._matched())

    def test_unmatched_file_is_out_of_scope(self) -> None:
        self.assertNotIn("notes.txt", self._matched())


class PathKeyScopeTest(unittest.TestCase):
    """The ``path`` key names one file outright.

    Exercised directly rather than through the shared config: the strategies
    that pair with a ``path`` entry are not the ones the shared tree needs,
    and the discovery-running tests in the sibling module require every
    configured strategy to accept its own keys.
    """

    def test_path_key_resolves(self) -> None:
        srcs = [{"path": "README.md", "type": "doc"}]
        self.assertEqual(
            in_scope_files(srcs, ["README.md", "notes.txt"]), {"README.md"},
        )

    def test_path_key_honours_exclude(self) -> None:
        srcs = [{"path": "README.md", "exclude": ["README.md"]}]
        self.assertEqual(in_scope_files(srcs, ["README.md"]), set())

    def test_source_without_resolution_keys_is_skipped(self) -> None:
        self.assertEqual(in_scope_files([{"type": "doc"}], ["README.md"]), set())

    def test_non_mapping_source_is_ignored(self) -> None:
        # A hand-edited discover.yaml can yield a list entry that is not a
        # mapping; a freshness probe must not raise on it.
        self.assertEqual(in_scope_files(["oops"], ["README.md"]), set())

    def test_excluded_dir_names_are_never_in_scope(self) -> None:
        # walk_glob prunes these unconditionally, config or not.
        srcs = [{"glob": "**/*.py"}]
        self.assertEqual(
            in_scope_files(srcs, [".git/hooks/x.py", "node_modules/a/b.py"]),
            set(),
        )


if __name__ == "__main__":
    unittest.main()
