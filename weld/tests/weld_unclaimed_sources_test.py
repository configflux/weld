"""What "claimed" means (ADR 0135, ADR 0144).

Field eval v0.23.1 Finding 05: a ``discover.yaml`` generated before a strategy
shipped keeps discovering with the old config, so 100% of a language's source
can be invisible to the graph while ``wd doctor`` and ``wd prime`` both report
healthy. ADR 0135 made that gap visible by comparing languages on disk against
the ``strategy:`` names the config mentions; ADR 0144 replaced the comparison,
because a name is not a claim -- one ``tree_sitter`` entry read as claiming
every tree-sitter language, and a glob matching none of a language's files
still claimed it, so a config wiring ``**/*.ts`` spoke for unread ``.tsx``.

This module is the rule and only the rule: every case feeds source entries and
repo-relative paths, and no case touches a filesystem. The disk walk that
produces those paths and the three surfaces that report the answer live in
``weld_unclaimed_surfaces_test.py``.
"""

from __future__ import annotations

import unittest

from weld._unclaimed_sources import (
    UnclaimedClass,
    detect_unclaimed_from_sources,
)


def _entry(glob: str, strategy: str, **extra) -> dict:
    """One ``discover.yaml`` source entry, as the parsed config yields it."""
    return {"glob": glob, "type": "file", "strategy": strategy, **extra}


def _paths(prefix: str, ext: str, count: int) -> list[str]:
    return [f"{prefix}/f{i}{ext}" for i in range(count)]


class UnclaimedComparisonTest(unittest.TestCase):
    """Pure comparison of files on disk against what the config claims."""

    def test_present_language_no_claimer_is_unclaimed(self):
        got = detect_unclaimed_from_sources(
            [_entry("doc/*.md", "markdown")], _paths("src", ".cs", 8),
        )
        self.assertEqual(got, [UnclaimedClass("csharp", 8)])

    def test_language_claimed_by_tree_sitter_is_silent(self):
        got = detect_unclaimed_from_sources(
            [_entry("**/*.cs", "tree_sitter", language="csharp")],
            _paths("src", ".cs", 8),
        )
        self.assertEqual(got, [])

    def test_language_claimed_by_specific_strategy_is_silent(self):
        # csharp is claimed by any csharp_* strategy, not only tree_sitter:
        # the claiming set is membership, not id equality (ADR 0135). What
        # ADR 0144 adds is that the entry must also match the files.
        got = detect_unclaimed_from_sources(
            [_entry("**/*.cs", "csharp_project")], _paths("src", ".cs", 3),
        )
        self.assertEqual(got, [])

    def test_python_needs_a_python_strategy(self):
        self.assertEqual(
            detect_unclaimed_from_sources(
                [_entry("**/*.py", "markdown")], _paths("pkg", ".py", 5),
            ),
            [UnclaimedClass("python", 5)],
        )
        self.assertEqual(
            detect_unclaimed_from_sources(
                [_entry("**/*.py", "python_module")], _paths("pkg", ".py", 5),
            ),
            [],
        )

    def test_language_with_no_claiming_set_is_never_reported(self):
        # Ruby is a language `EXT_TO_LANG` counts and weld cannot extract:
        # re-running init would wire nothing for it, so warning about it would
        # be noise, not signal.
        self.assertEqual(
            detect_unclaimed_from_sources([], ["app/models.rb"]), [],
        )

    def test_language_absent_from_disk_is_never_reported(self):
        # Nothing claims csharp here either -- there is simply no csharp.
        self.assertEqual(
            detect_unclaimed_from_sources([_entry("**/*.md", "markdown")], []),
            [],
        )

    def test_results_sorted_by_descending_count(self):
        got = detect_unclaimed_from_sources(
            [],
            _paths("a", ".go", 2) + _paths("b", ".cs", 9) + _paths("c", ".rs", 5),
        )
        self.assertEqual(
            [u.language for u in got], ["csharp", "rust", "go"],
        )


class ClaimIsAMatchedFileTest(unittest.TestCase):
    """ADR 0144: an entry claims what its glob matches, and nothing else."""

    def test_dialect_sibling_the_glob_misses_is_unclaimed(self):
        # The bug, in one case: `.tsx` is typescript to EXT_TO_LANG exactly as
        # `.ts` is, so a claimed `a.ts` used to speak for an unread `p.tsx`.
        got = detect_unclaimed_from_sources(
            [_entry("**/*.ts", "tree_sitter", language="typescript")],
            ["src/a.ts", "src/p.tsx"],
        )
        self.assertEqual(got, [UnclaimedClass("typescript", 1)])

    def test_one_tree_sitter_entry_does_not_claim_every_tree_sitter_language(self):
        # `tree_sitter` is in go's claiming set too, so the name alone used to
        # claim the `.go` file this entry cannot possibly read.
        got = detect_unclaimed_from_sources(
            [_entry("**/*.ts", "tree_sitter", language="typescript")],
            ["src/a.ts", "cmd/main.go"],
        )
        self.assertEqual(got, [UnclaimedClass("go", 1)])

    def test_dialect_family_glob_claims_both_extensions(self):
        # The brace form `wd init` writes since ADR 0142 D1. It must be
        # expanded to match at all, so this is also the regression guard on
        # the remedy being able to close what the warning opens.
        self.assertEqual(
            detect_unclaimed_from_sources(
                [_entry("**/*.{ts,tsx}", "tree_sitter", language="typescript")],
                ["src/a.ts", "src/p.tsx"],
            ),
            [],
        )

    def test_excluded_files_are_not_claimed(self):
        got = detect_unclaimed_from_sources(
            [_entry("**/*.cs", "tree_sitter", exclude=["vendor/**"])],
            ["vendor/A.cs"],
        )
        self.assertEqual(got, [UnclaimedClass("csharp", 1)])

    def test_partly_excluded_language_is_silent(self):
        # A repo that scopes a language on purpose is not a gap: one claimed
        # file settles the extension (the per-file reading ADR 0144 rejects
        # would report `vendor/B.cs` here).
        got = detect_unclaimed_from_sources(
            [_entry("**/*.cs", "tree_sitter", exclude=["vendor/**"])],
            ["src/A.cs", "vendor/B.cs"],
        )
        self.assertEqual(got, [])

    def test_path_and_files_keys_claim_too(self):
        # `resolve_source_files` honours all three keys, so the claim check
        # that mirrors it must as well.
        self.assertEqual(
            detect_unclaimed_from_sources(
                [{"path": "A.cs", "type": "file", "strategy": "tree_sitter"}],
                ["A.cs"],
            ),
            [],
        )
        self.assertEqual(
            detect_unclaimed_from_sources(
                [{"files": ["A.cs"], "type": "file", "strategy": "tree_sitter"}],
                ["A.cs"],
            ),
            [],
        )

    def test_single_directory_glob_does_not_claim_a_subtree(self):
        # `*` never spans `/`: an entry on `src/*.py` claims `src/a.py` and
        # says nothing about `src/pkg/b.py`.
        got = detect_unclaimed_from_sources(
            [_entry("src/*.py", "python_module")], ["src/a.py"],
        )
        self.assertEqual(got, [])
        got = detect_unclaimed_from_sources(
            [_entry("src/*.py", "python_module")], ["src/pkg/b.py"],
        )
        self.assertEqual(got, [UnclaimedClass("python", 1)])


class GeneratedGlobsCloseEveryGapTest(unittest.TestCase):
    """A warning the remedy cannot silence is worse than the silence (0144).

    ``wd init --refresh`` wires exactly the languages this module reports, from
    :mod:`weld._init_language_entries`. So every language that can be reported
    must have generated globs that claim *every* extension ``EXT_TO_LANG`` maps
    to it -- otherwise adding an extension (say ``.mjs``) to the counter
    without adding it to the glob would mint a permanent warning.
    """

    def test_every_reportable_language_has_generated_globs(self):
        from weld._init_language_entries import _TREE_SITTER_LANGUAGES
        from weld._unclaimed_sources import _CLAIMING_STRATEGIES

        # Python is the one exception: its globs are derived from the tree by
        # `find_python_glob_roots` rather than fixed here, and `.py` is its
        # only extension, so one claimed file settles it.
        self.assertEqual(
            set(_CLAIMING_STRATEGIES) - {"python"},
            set(_TREE_SITTER_LANGUAGES),
        )

    def test_generated_globs_claim_every_extension_of_their_language(self):
        from weld._init_language_entries import _TREE_SITTER_LANGUAGES
        from weld.init_detect import EXT_TO_LANG

        for language, globs in _TREE_SITTER_LANGUAGES.items():
            extensions = sorted(
                ext for ext, lang in EXT_TO_LANG.items() if lang == language
            )
            sources = [_entry(glob, "tree_sitter") for glob in globs]
            paths = [f"src/sample{ext}" for ext in extensions]
            with self.subTest(language=language):
                self.assertEqual(
                    detect_unclaimed_from_sources(sources, paths), [],
                    f"wd init's globs for {language} leave an extension "
                    f"unclaimed, so its warning could never be closed",
                )


if __name__ == "__main__":
    unittest.main()
