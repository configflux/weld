"""The shared strategy glob resolver's contract (ADR 0112).

Fourteen private ``_resolve_glob`` copies became one module, so the contract
they each half-stated is now stated once and pinned once here:

* ``**`` and flat patterns both resolve, and neither is gated on a
  ``(root / pattern).parent.is_dir()`` guard (bd t06t -- that guard is False
  for every ``**`` pattern, so ten strategies emitted nothing at all);
* ``{a,b}`` alternatives expand, de-duplicate, and union (previously only two
  of the fourteen did this);
* the result is sorted (previously nine of the fourteen were);
* ``exclude:`` is honoured in every form ``walk_glob`` honours, and is *not*
  re-applied afterwards;
* the tuple shape returns per-file provenance, never a directory.

:class:`WalkGlobBranchAgreementTest` pins the layer below (bd 0d73): the two
branches of :func:`weld.glob_match.walk_glob` must return the same *kind* of
thing, which they did not -- the flat branch delegated to ``Path.glob`` and
yielded matching directories, while the ``**`` branch iterated ``os.walk``'s
filenames and never did.

:class:`IndexAndStrategyAgreeTest` pins why brace expansion lives in
``walk_glob`` rather than above it. ``typescript_exports`` and ``express``
expanded braces in their own copies, so
:func:`weld._source_resolve.resolve_source_files` -- resolving the *same*
``discover.yaml`` entry to decide what discovery records as in scope -- saw a
pattern it could not match and recorded nothing, while the strategy emitted
nodes. Editing one of those files then marked nothing stale.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

from weld.glob_match import expand_braces, walk_glob
from weld.strategies._glob_resolve import (
    resolve_glob,
    resolve_glob_with_provenance,
)


class _TreeCase(unittest.TestCase):
    """A real on-disk fixture tree; the resolver is not mocked anywhere."""

    #: rel path -> body. Directories are created implicitly.
    FILES: dict[str, str] = {}

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        for rel, body in self.FILES.items():
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")

    def rels(self, paths: list[Path]) -> list[str]:
        return [p.relative_to(self.root).as_posix() for p in paths]


class ExpandBracesTest(unittest.TestCase):
    """One top-level group expands; anything else passes through."""

    def test_no_brace_is_the_identity(self) -> None:
        self.assertEqual(expand_braces("src/**/*.ts"), ["src/**/*.ts"])

    def test_single_group_expands_in_order(self) -> None:
        self.assertEqual(
            expand_braces("src/**/*.{ts,tsx}"),
            ["src/**/*.ts", "src/**/*.tsx"],
        )

    def test_alternatives_are_trimmed_and_deduplicated(self) -> None:
        self.assertEqual(
            expand_braces("src/*.{ts, ts , tsx}"),
            ["src/*.ts", "src/*.tsx"],
        )

    def test_empty_alternatives_are_dropped(self) -> None:
        self.assertEqual(expand_braces("src/*.{ts,,}"), ["src/*.ts"])

    def test_unterminated_group_passes_through(self) -> None:
        """A half-expanded pattern is a wrong answer; unexpanded is the old one."""
        self.assertEqual(expand_braces("src/*.{ts"), ["src/*.{ts"])

    def test_multiple_groups_pass_through_unexpanded(self) -> None:
        pattern = "{a,b}/src/*.{ts,tsx}"
        self.assertEqual(expand_braces(pattern), [pattern])

    def test_nested_groups_pass_through_unexpanded(self) -> None:
        pattern = "src/*.{ts,{tsx,jsx}}"
        self.assertEqual(expand_braces(pattern), [pattern])


class ResolveGlobTest(_TreeCase):
    """Resolution: recursion, braces, order, and excludes."""

    FILES = {
        "pkg/top.ts": "export const a = 1;\n",
        "pkg/top.tsx": "export const b = 2;\n",
        "pkg/deep/nested/leaf.ts": "export const c = 3;\n",
        "pkg/tests/spec.ts": "export const d = 4;\n",
        "other/elsewhere.ts": "export const e = 5;\n",
    }

    def test_recursive_pattern_reaches_every_depth(self) -> None:
        matched = resolve_glob(self.root, "pkg/**/*.ts")
        self.assertEqual(
            self.rels(matched),
            ["pkg/deep/nested/leaf.ts", "pkg/tests/spec.ts", "pkg/top.ts"],
        )

    def test_recursive_pattern_needs_no_parent_directory_guard(self) -> None:
        """bd t06t: ``Path('pkg/**/*.ts').parent`` is never a directory.

        Ten strategies gated their resolve on exactly that, so a user who
        wrote a recursive glob got silence rather than a subset -- no partial
        result to notice. The resolver must have no such guard.
        """
        self.assertFalse((self.root / "pkg/**").is_dir())
        self.assertTrue(resolve_glob(self.root, "pkg/**/*.ts"))

    def test_flat_pattern_stays_in_one_directory(self) -> None:
        self.assertEqual(
            self.rels(resolve_glob(self.root, "pkg/*.ts")), ["pkg/top.ts"],
        )

    def test_missing_parent_directory_resolves_to_nothing(self) -> None:
        """The behaviour the deleted guard provided, now the walker's job."""
        self.assertEqual(resolve_glob(self.root, "nope/*.ts"), [])

    def test_brace_alternatives_union_and_sort(self) -> None:
        self.assertEqual(
            self.rels(resolve_glob(self.root, "pkg/*.{ts,tsx}")),
            ["pkg/top.ts", "pkg/top.tsx"],
        )

    def test_repeated_alternative_does_not_double_count(self) -> None:
        matched = self.rels(resolve_glob(self.root, "pkg/**/*.{ts,ts}"))
        self.assertEqual(sorted(matched), sorted(set(matched)))

    def test_result_is_sorted(self) -> None:
        matched = self.rels(resolve_glob(self.root, "**/*.ts"))
        self.assertEqual(matched, sorted(matched))

    def test_directory_form_exclude_prunes_the_subtree(self) -> None:
        self.assertEqual(
            self.rels(resolve_glob(self.root, "pkg/**/*.ts", ["pkg/tests"])),
            ["pkg/deep/nested/leaf.ts", "pkg/top.ts"],
        )

    def test_bare_directory_name_exclude_prunes_at_depth(self) -> None:
        self.assertEqual(
            self.rels(resolve_glob(self.root, "pkg/**/*.ts", ["tests"])),
            ["pkg/deep/nested/leaf.ts", "pkg/top.ts"],
        )

    def test_subtree_form_exclude_prunes(self) -> None:
        self.assertEqual(
            self.rels(resolve_glob(self.root, "pkg/**/*.ts", ["pkg/tests/**"])),
            ["pkg/deep/nested/leaf.ts", "pkg/top.ts"],
        )

    def test_excludes_apply_to_every_brace_alternative(self) -> None:
        """A per-alternative walk must not let one alternative leak."""
        matched = self.rels(
            resolve_glob(self.root, "pkg/**/*.{ts,tsx}", ["pkg/tests"])
        )
        self.assertNotIn("pkg/tests/spec.ts", matched)
        self.assertIn("pkg/top.tsx", matched)

    def test_no_excludes_and_empty_excludes_agree(self) -> None:
        self.assertEqual(
            resolve_glob(self.root, "pkg/**/*.ts"),
            resolve_glob(self.root, "pkg/**/*.ts", []),
        )


class ResolveGlobWithProvenanceTest(_TreeCase):
    """The tuple shape is the file shape plus ADR 0017 provenance."""

    FILES = {
        "pkg/a.py": "x = 1\n",
        "pkg/deep/b.py": "y = 2\n",
        "README.md": "# root\n",
    }

    def test_files_half_matches_resolve_glob_exactly(self) -> None:
        files, _ = resolve_glob_with_provenance(self.root, "pkg/**/*.py")
        self.assertEqual(files, resolve_glob(self.root, "pkg/**/*.py"))

    def test_provenance_is_per_file_and_posix(self) -> None:
        _, provenance = resolve_glob_with_provenance(self.root, "pkg/**/*.py")
        self.assertEqual(provenance, ["pkg/a.py", "pkg/deep/b.py"])

    def test_provenance_never_records_a_directory(self) -> None:
        """bd 8ia5 / bd od2a: a root-anchored glob's parent entry is ``"./"``.

        That marker makes every path in the repository read as tracked
        source, which widens ``source_stale`` to the whole tree permanently.
        """
        _, provenance = resolve_glob_with_provenance(self.root, "*.md")
        self.assertEqual(provenance, ["README.md"])
        self.assertNotIn("./", provenance)

    def test_empty_match_claims_no_provenance(self) -> None:
        """An empty glob must not claim provenance it has not earned."""
        self.assertEqual(
            resolve_glob_with_provenance(self.root, "nope/**/*.py"), ([], []),
        )


class WalkGlobBranchAgreementTest(_TreeCase):
    """bd 0d73: both ``walk_glob`` branches return the same kind of thing."""

    FILES = {
        "packs/a.yaml": "k: v\n",
        "packs/nested/b.yaml": "k: v\n",
    }

    def test_flat_branch_does_not_yield_a_directory(self) -> None:
        """``Path.glob`` matches ``packs/nested`` for ``packs/*``; we must not.

        A directory reaching ``build_file_hashes`` is opened, raises
        ``IsADirectoryError``, is swallowed as ``OSError`` and dropped -- so
        it sits in scope with no hash and no incremental basis, while the
        ADR 0101 accounting counts it as a file discovery should have covered.
        """
        self.assertTrue((self.root / "packs" / "nested").is_dir())
        matched = self.rels(walk_glob(self.root, "packs/*"))
        self.assertEqual(matched, ["packs/a.yaml"])

    def test_recursive_branch_agrees(self) -> None:
        matched = self.rels(walk_glob(self.root, "packs/**"))
        self.assertEqual(matched, ["packs/a.yaml", "packs/nested/b.yaml"])

    def test_neither_branch_yields_a_directory(self) -> None:
        for pattern in ("packs/*", "packs/**", "*", "**"):
            with self.subTest(pattern=pattern):
                for path in walk_glob(self.root, pattern):
                    self.assertFalse(path.is_dir(), path)

    def test_a_symlink_to_a_directory_is_not_yielded(self) -> None:
        """``is_dir()`` resolves the link, matching the ``**`` branch.

        In the recursive branch a symlinked directory lands in ``dirnames``
        and is never yielded, so the flat branch must drop it too.
        """
        link = self.root / "packs" / "link"
        try:
            os.symlink(self.root / "packs" / "nested", link)
        except (OSError, NotImplementedError):
            self.skipTest("platform does not support symlinks here")
        self.assertEqual(self.rels(walk_glob(self.root, "packs/*")),
                         ["packs/a.yaml"])

    def test_resolve_glob_inherits_the_agreement(self) -> None:
        self.assertEqual(
            self.rels(resolve_glob(self.root, "packs/*")), ["packs/a.yaml"],
        )

    def test_the_in_scope_file_list_records_no_directory(self) -> None:
        """bd 0d73's stated acceptance, at the layer that consumes the walk.

        ``resolve_source_files`` feeds ``DiscoveryState.files``, so a
        directory here is a path the hasher cannot open and the ADR 0101
        accounting counts as a file discovery should have covered -- in scope
        forever, coverable never.
        """
        from weld._source_resolve import resolve_source_files

        self.assertEqual(
            resolve_source_files(self.root, {"glob": "packs/*"}),
            ["packs/a.yaml"],
        )


class IndexAndStrategyAgreeTest(_TreeCase):
    """What a strategy resolves and what discovery records must be one set.

    ``resolve_source_files`` builds the in-scope file list the ADR 0101
    coverage accounting and the ADR 0017 staleness signals read. If a strategy
    resolves a wider set than that call does, its extra files are emitted as
    nodes that nothing considers in scope -- so editing one of them marks
    nothing stale, and the graph goes quietly out of date. Expanding braces
    inside ``walk_glob``, which both sides go through, is what makes the two
    sets equal by construction rather than by coincidence.
    """

    FILES = {
        "ui/src/Button.tsx": "export function Button() {}\n",
        "ui/src/index.ts": 'export * from "./Button";\n',
        "ui/src/nested/Card.tsx": "export function Card() {}\n",
        "ui/tests/spec.ts": "export const t = 1;\n",
    }

    def _agree(self, pattern: str, excludes: list[str] | None = None) -> None:
        from weld._source_resolve import resolve_source_files

        source: dict = {"glob": pattern}
        if excludes is not None:
            source["exclude"] = excludes
        self.assertEqual(
            sorted(resolve_source_files(self.root, source)),
            sorted(self.rels(resolve_glob(self.root, pattern, excludes))),
            f"in-scope set and strategy set disagree for {pattern!r}",
        )

    def test_brace_pattern_agrees(self) -> None:
        self._agree("ui/**/*.{ts,tsx}")

    def test_brace_pattern_is_not_simply_empty_on_both_sides(self) -> None:
        """Control: two empty sets would agree vacuously."""
        from weld._source_resolve import resolve_source_files

        matched = resolve_source_files(self.root, {"glob": "ui/**/*.{ts,tsx}"})
        self.assertEqual(len(matched), 4, matched)

    def test_brace_pattern_with_excludes_agrees(self) -> None:
        self._agree("ui/**/*.{ts,tsx}", ["ui/tests"])

    def test_plain_and_flat_patterns_agree(self) -> None:
        for pattern in ("ui/**/*.ts", "ui/src/*.tsx", "ui/src/*"):
            with self.subTest(pattern=pattern):
                self._agree(pattern)


if __name__ == "__main__":
    unittest.main()
