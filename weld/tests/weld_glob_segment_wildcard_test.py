"""A wildcard in a *directory* segment, at the resolver layer (bd uhxjc).

The flat branch of :func:`weld.glob_match._walk_one` guarded on
``(root / pattern).parent.is_dir()``. For ``apps/*/package.json`` that parent
is the literal path ``<root>/apps/*``, never a directory, so the branch
returned ``[]`` and the pattern matched nothing at all -- exactly the shape
the module's own docstring records as fixed for ``**`` (bd t06t,
``docs/**/*.md`` -> the literal ``docs/**``) and which was still live one
branch over.

Three shapes, because the issue names three and they fail for the same reason
but are not the same pattern to translate:

* one wildcard segment, wildcard-free filename -- ``apps/*/package.json``;
* a wildcard mid-path with more path after it -- ``services/*/src/main.py``;
* a wildcard in more than one segment -- ``services/*/src/*.py``.

The controls matter as much as the cases. A "fix" that routed *every*
pattern to the ``**`` walker would satisfy all three above and silently turn
each single-directory glob in every shipped config into a recursive one, so
:class:`LiteralDirectoryGlobUnchangedTest` pins that ``src/*.py`` still stops
at one directory, and :class:`RecursiveGlobUnchangedTest` pins that ``**``
is untouched.

:class:`ResolverLayersAgreeTest` is the reason this is a correctness bug
rather than a missing feature: the same call decides what a *strategy*
emits (:func:`weld.strategies._glob_resolve.resolve_glob`, ADR 0112), what
discovery records as **in scope**
(:func:`weld._source_resolve.resolve_source_files`), and -- separately, by a
second implementation over a path list -- what the ADR 0101 coverage probe
calls in scope (:func:`weld._staleness_coverage.in_scope_files`). The third
never had the defect, so before the fix it reported files the walk could not
resolve: over-reporting, which ADR 0101 names as the expensive direction
because a file the state can never cover is permanent staleness and a
refresh on every read.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from weld._source_resolve import resolve_source_files
from weld._staleness_coverage import in_scope_files
from weld.glob_match import walk_glob
from weld.strategies._glob_resolve import resolve_glob


class _TreeCase(unittest.TestCase):
    """A real on-disk fixture tree; nothing here is mocked."""

    #: A monorepo shape: two workspace packages, two services, and a
    #: single-directory tree with something buried under it.
    FILES: dict[str, str] = {
        "apps/a/package.json": '{"name": "app-a"}\n',
        "apps/b/package.json": '{"name": "app-b"}\n',
        # Deeper than the pattern's one wildcard segment reaches.
        "apps/b/nested/package.json": '{"name": "nested"}\n',
        "services/x/src/main.py": "x = 1\n",
        "services/x/src/helper.py": "h = 1\n",
        "services/y/src/main.py": "y = 1\n",
        "services/y/src/util.py": "u = 1\n",
        "services/y/tests/spec.py": "s = 1\n",
        "src/top.py": "t = 1\n",
        "src/deep/buried.py": "b = 1\n",
        "package.json": '{"name": "root"}\n',
    }

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        for rel, body in self.FILES.items():
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")

    def rels(self, paths: list[Path]) -> list[str]:
        return sorted(p.relative_to(self.root).as_posix() for p in paths)

    def walked(self, pattern: str, excludes: list[str] | None = None) -> list[str]:
        return self.rels(walk_glob(self.root, pattern, excludes=excludes))


class SegmentWildcardResolvesTest(_TreeCase):
    """The defect itself, one case per shape the issue names."""

    def test_one_wildcard_directory_segment(self) -> None:
        """``apps/*/package.json`` -- the shape the bug was found on."""
        self.assertEqual(
            self.walked("apps/*/package.json"),
            ["apps/a/package.json", "apps/b/package.json"],
        )

    def test_one_wildcard_segment_does_not_reach_deeper(self) -> None:
        """``*`` is one segment, not ``**``: ``apps/b/nested`` stays out.

        Without this, "resolves at all" and "resolves correctly" are the same
        assertion, and a fix that widened ``*`` into ``**`` would pass.
        """
        self.assertNotIn(
            "apps/b/nested/package.json", self.walked("apps/*/package.json"),
        )

    def test_wildcard_mid_path_with_a_literal_filename(self) -> None:
        """``services/*/src/main.py`` -- wildcard neither first nor last."""
        self.assertEqual(
            self.walked("services/*/src/main.py"),
            ["services/x/src/main.py", "services/y/src/main.py"],
        )

    def test_wildcard_in_more_than_one_segment(self) -> None:
        """``services/*/src/*.py`` -- directory *and* filename wildcards."""
        self.assertEqual(
            self.walked("services/*/src/*.py"),
            [
                "services/x/src/helper.py",
                "services/x/src/main.py",
                "services/y/src/main.py",
                "services/y/src/util.py",
            ],
        )

    def test_a_character_class_in_a_directory_segment_resolves(self) -> None:
        """``[xy]`` is a metacharacter too, and hit the same literal parent."""
        self.assertEqual(
            self.walked("services/[x]/src/main.py"), ["services/x/src/main.py"],
        )

    def test_a_question_mark_in_a_directory_segment_resolves(self) -> None:
        self.assertEqual(
            self.walked("apps/?/package.json"),
            ["apps/a/package.json", "apps/b/package.json"],
        )

    def test_no_segment_wildcard_pattern_yields_a_directory(self) -> None:
        """bd 0d73's agreement holds on the widened route as well."""
        for pattern in ("apps/*/package.json", "services/*/src/*.py",
                        "services/*/*", "apps/*/*"):
            with self.subTest(pattern=pattern):
                for path in walk_glob(self.root, pattern):
                    self.assertFalse(path.is_dir(), path)


class SegmentWildcardExcludesTest(_TreeCase):
    """``exclude:`` must keep working on the shape that now resolves."""

    def test_directory_form_exclude_prunes(self) -> None:
        """The form only a prune-during-descent walker can honour.

        ``matches_exclude`` has no ancestor-directory check, so filtering an
        already-resolved list could never drop ``services/y/tests/spec.py``
        for ``services/y/tests``.
        """
        self.assertEqual(
            self.walked("services/*/*/*.py", ["services/y/tests"]),
            [
                "services/x/src/helper.py",
                "services/x/src/main.py",
                "services/y/src/main.py",
                "services/y/src/util.py",
            ],
        )

    def test_bare_directory_name_exclude_prunes(self) -> None:
        self.assertNotIn(
            "services/y/tests/spec.py",
            self.walked("services/*/*/*.py", ["tests"]),
        )

    def test_subtree_form_exclude_prunes(self) -> None:
        self.assertNotIn(
            "services/y/tests/spec.py",
            self.walked("services/*/*/*.py", ["services/y/tests/**"]),
        )

    def test_file_pattern_exclude_applies(self) -> None:
        self.assertEqual(
            self.walked("services/*/src/*.py", ["**/helper.py"]),
            [
                "services/x/src/main.py",
                "services/y/src/main.py",
                "services/y/src/util.py",
            ],
        )

    def test_no_excludes_and_empty_excludes_agree(self) -> None:
        self.assertEqual(
            walk_glob(self.root, "apps/*/package.json"),
            walk_glob(self.root, "apps/*/package.json", excludes=[]),
        )


class RecursiveGlobUnchangedTest(_TreeCase):
    """``**`` is not what this fix touches; prove it still answers."""

    def test_globstar_still_reaches_every_depth(self) -> None:
        self.assertEqual(
            self.walked("**/package.json"),
            [
                "apps/a/package.json",
                "apps/b/nested/package.json",
                "apps/b/package.json",
                "package.json",
            ],
        )

    def test_globstar_under_a_literal_prefix_still_answers(self) -> None:
        self.assertEqual(
            self.walked("apps/**/package.json"),
            [
                "apps/a/package.json",
                "apps/b/nested/package.json",
                "apps/b/package.json",
            ],
        )


class LiteralDirectoryGlobUnchangedTest(_TreeCase):
    """The fast single-directory path must cost nothing, and stay flat.

    This is the control that makes the fix a *narrowing* of the flat branch's
    condition rather than a removal of the branch.
    """

    def test_literal_directory_glob_resolves_its_one_directory(self) -> None:
        self.assertEqual(self.walked("src/*.py"), ["src/top.py"])

    def test_literal_directory_glob_still_does_not_recurse(self) -> None:
        self.assertNotIn("src/deep/buried.py", self.walked("src/*.py"))

    def test_root_anchored_glob_still_resolves(self) -> None:
        """No ``/`` at all: the directory part is empty, so still flat."""
        self.assertEqual(self.walked("*.json"), ["package.json"])

    def test_a_wildcard_free_pattern_still_resolves(self) -> None:
        self.assertEqual(
            self.walked("apps/a/package.json"), ["apps/a/package.json"],
        )

    def test_a_missing_literal_directory_still_answers_empty(self) -> None:
        self.assertEqual(self.walked("nope/*.py"), [])


class ResolverLayersAgreeTest(_TreeCase):
    """One glob, three readers, one answer -- ADR 0112 and ADR 0101.

    What a strategy emits, what discovery records as in scope, and what the
    coverage probe calls in scope have to be the same set. They are three
    call paths, and the last is a second implementation over a path list, so
    the agreement is asserted rather than assumed.
    """

    PATTERNS = (
        "apps/*/package.json",
        "services/*/src/main.py",
        "services/*/src/*.py",
    )

    def _walk_set(self, pattern: str) -> set[str]:
        return set(self.walked(pattern))

    def test_strategy_and_index_resolve_the_same_files(self) -> None:
        """ADR 0112: a strategy's set and discovery's in-scope set are one.

        A strategy that resolved more than ``resolve_source_files`` does
        would emit nodes for files nothing considers in scope, so editing one
        of them would mark nothing stale.
        """
        for pattern in self.PATTERNS:
            with self.subTest(pattern=pattern):
                self.assertEqual(
                    sorted(resolve_source_files(self.root, {"glob": pattern})),
                    self.rels(resolve_glob(self.root, pattern)),
                )

    def test_the_agreement_is_not_vacuous(self) -> None:
        """Control: two empty sets would agree for the wrong reason."""
        for pattern in self.PATTERNS:
            with self.subTest(pattern=pattern):
                self.assertTrue(
                    resolve_source_files(self.root, {"glob": pattern}),
                    f"{pattern!r} resolved nothing -- the defect, not agreement",
                )

    def test_the_coverage_matcher_never_over_reports(self) -> None:
        """ADR 0101's load-bearing direction, on the shape that broke it.

        ``in_scope_files`` matches a path list with the shared regex
        translation, which handles a wildcard directory segment correctly.
        The walk did not -- so the matcher called four files in scope that
        no discovery run could ever cover: permanent ``coverage_stale``, and
        a refresh on every read that cannot fix it.
        """
        candidates = sorted(self.FILES)
        for pattern in self.PATTERNS:
            with self.subTest(pattern=pattern):
                matched = in_scope_files([{"glob": pattern}], candidates)
                extra = matched - self._walk_set(pattern)
                self.assertEqual(
                    extra, set(),
                    f"coverage matcher over-reported {sorted(extra)} for "
                    f"{pattern!r}: files in scope that a walk cannot resolve",
                )

    def test_the_coverage_matcher_agrees_with_the_walk(self) -> None:
        """The other direction too: under-reporting costs a missed detection."""
        candidates = sorted(self.FILES)
        for pattern in self.PATTERNS:
            with self.subTest(pattern=pattern):
                self.assertEqual(
                    in_scope_files([{"glob": pattern}], candidates),
                    self._walk_set(pattern),
                )


if __name__ == "__main__":
    unittest.main()
