"""The index path vocabulary is POSIX, and both of its ends say so.

Weld builds repo-relative file listings in two places that are compared
against each other. :mod:`weld.glob_match` spelled its listing with
``as_posix()``; :mod:`weld._source_resolve` -- the source of
``DiscoveryState.files`` and the dirty/stale sets -- spelled its own with
``str(p.relative_to(root))``, which is separator-native. Identical on POSIX,
divergent off it, where ``files_missing_from_inventory`` then compares POSIX
in-scope paths against a native inventory: every in-scope file reads as
uncovered, ``coverage_stale`` is permanently true, and auto-refresh re-runs
discovery on every read (bd v552).

bd pbi8 deliberately did not just POSIX-ify the resolver, and the reason is
the second half of this contract: the dirty set flows on to the strategies as
``IncrementalHint.dirty_files`` and is matched there against a path the
strategy re-derives itself. Rewriting one end alone fixes staleness and
breaks dirty scoping on the same platform -- ``python_callgraph`` would match
nothing and parse nothing. So both ends now build through
:func:`weld._rel_path.rel_to_root`, and the cases below pin each of them.

HONEST LIMITATION -- READ BEFORE TRUSTING THESE TESTS. Weld has no Windows
lane, so none of this runs on the platform where the defect bites. Unlike
the fold in ``incremental_rel_path_form_test``, which is a string operation
and can be simulated by patching ``_FOREIGN_SEPARATORS``, this is a *path
construction* -- so the non-POSIX platform is supplied instead as
``PureWindowsPath`` input, whose ``relative_to``/``as_posix`` semantics are
the real ones on any host. What that proves is the form contract, not that
weld runs on Windows.

The last class runs on real POSIX paths and pins the property that keeps
this from being an unconditional ``replace("\\\\", "/")``: a file legitimately
named ``weird\\name.py`` is a real, distinct file here, and its spelling
survives untouched.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path, PurePosixPath, PureWindowsPath

from weld._rel_path import needs_folding, rel_to_root
from weld._source_resolve import resolve_source_files
from weld._staleness_coverage import in_scope_files
from weld.strategies._incremental_hint import dirty_matched


class RelToRootFormTest(unittest.TestCase):
    """The construction helper answers the canonical form on any input."""

    def test_windows_shaped_path_yields_posix_separators(self) -> None:
        # The discriminating case: str(p.relative_to(root)) answers
        # "lib\\thing.py" here, which is the spelling the index used to carry.
        self.assertEqual(
            "lib/thing.py",
            rel_to_root(
                PureWindowsPath(r"C:\repo\lib\thing.py"),
                PureWindowsPath(r"C:\repo"),
            ),
        )

    def test_posix_path_is_unchanged(self) -> None:
        self.assertEqual(
            "lib/thing.py",
            rel_to_root(
                PurePosixPath("/repo/lib/thing.py"), PurePosixPath("/repo")
            ),
        )

    def test_file_directly_under_root_has_no_separator(self) -> None:
        self.assertEqual(
            "README.md",
            rel_to_root(
                PureWindowsPath(r"C:\repo\README.md"), PureWindowsPath(r"C:\repo")
            ),
        )

    def test_path_outside_root_raises_value_error(self) -> None:
        # Callers that treat this as "skip this file" catch it themselves;
        # the helper must not swallow it into a bogus path.
        with self.assertRaises(ValueError):
            rel_to_root(PurePosixPath("/elsewhere/x.py"), PurePosixPath("/repo"))

    def test_needs_folding_is_false_on_this_platform(self) -> None:
        # Guards the claim every other test here rests on: on the platform
        # CI runs, the canonical form is what the OS already produces, so
        # this change is the exact identity.
        self.assertFalse(needs_folding())


class DirtyScopeMatchesTheIndexSpellingTest(unittest.TestCase):
    """The strategy side of the dirty-file match speaks the same vocabulary."""

    def test_native_shaped_paths_match_a_posix_dirty_set(self) -> None:
        root = PureWindowsPath(r"C:\repo")
        matched = [
            PureWindowsPath(r"C:\repo\lib\thing.py"),
            PureWindowsPath(r"C:\repo\lib\other.py"),
        ]
        # Spelled the way _source_resolve now spells the index.
        dirty = frozenset({"lib/thing.py"})

        kept = dirty_matched(matched, root, dirty)

        # With the old str() re-derivation this is [] -- the strategy would
        # parse nothing and python_callgraph would emit an empty graph.
        self.assertEqual([PureWindowsPath(r"C:\repo\lib\thing.py")], kept)

    def test_unmatched_and_out_of_root_paths_are_dropped(self) -> None:
        root = PureWindowsPath(r"C:\repo")
        matched = [
            PureWindowsPath(r"C:\repo\lib\clean.py"),
            PureWindowsPath(r"D:\other\stray.py"),
        ]
        self.assertEqual([], dirty_matched(matched, root, frozenset({"lib/x.py"})))


class ResolverAndScopeAgreeTest(unittest.TestCase):
    """The two listings that are compared against each other are equal."""

    def test_resolver_output_is_exactly_the_in_scope_set(self) -> None:
        source = {"glob": "pkg/**/*.py"}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pkg" / "deep").mkdir(parents=True)
            (root / "pkg" / "top.py").write_text("", encoding="utf-8")
            (root / "pkg" / "deep" / "nested.py").write_text("", encoding="utf-8")

            resolved = resolve_source_files(root, source)

        # This equality is what files_missing_from_inventory depends on, and it is
        # what a native inventory broke off POSIX: the resolver's spelling
        # has to be the one in_scope_files (via glob_match) produces.
        self.assertEqual(
            {"pkg/deep/nested.py", "pkg/top.py"}, set(resolved),
        )
        self.assertEqual(set(resolved), in_scope_files([source], resolved))
        self.assertFalse([p for p in resolved if "\\" in p])


class LiteralBackslashFilenameTest(unittest.TestCase):
    """No unconditional replace: a POSIX file may legitimately contain ``\\``."""

    def test_backslash_in_a_filename_is_not_a_separator_here(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pkg").mkdir()
            (root / "pkg" / "weird\\name.py").write_text("", encoding="utf-8")

            resolved = resolve_source_files(root, {"glob": "pkg/*.py"})

        # Folding this would claim a file named pkg/weird/name.py that does
        # not exist -- and in a purge, one file's edit would drop another's
        # nodes. Construction via as_posix() cannot make that mistake.
        self.assertEqual(["pkg/weird\\name.py"], resolved)


if __name__ == "__main__":
    unittest.main()
