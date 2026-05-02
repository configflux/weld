"""Regression: ``wd find install.sh`` must hit the actual ``install.sh`` file.

Background
----------
The file-index tokenizer used to drop the file extension and store only
the stem (``install.sh`` -> ``['install']``). Because ``find_files``
performs a substring match on tokens, a literal-with-dot query like
``install.sh`` never matched the stem-only token ``install`` (the dot
broke substring containment), so a natural query for the install script
returned only README/launch.md prose mentions and missed the file
itself.

The fix indexes the raw basename alongside the extension-stripped stem,
so ``install.sh`` now stores ``['install', 'install.sh']``. Existing
single-token searches (``install``, ``sh``) continue to match because
substring containment is preserved on the new basename token.

This test pins the tokenizer-level contract and the end-to-end
``find_files`` behaviour so the gap cannot silently regress.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld.file_index import _tokenize_path, find_files  # noqa: E402


class TokenizePathBasenameTest(unittest.TestCase):
    """``_tokenize_path`` must include the raw basename as a token when
    the filename has an extension. Extensionless basenames must not gain
    a duplicate entry.
    """

    def test_dotted_basename_added_alongside_stem(self) -> None:
        tokens = _tokenize_path("install.sh")
        self.assertIn("install", tokens)
        self.assertIn("install.sh", tokens)

    def test_dotted_basename_in_subdirectory(self) -> None:
        tokens = _tokenize_path("scripts/run.sh")
        self.assertIn("scripts", tokens)
        self.assertIn("run", tokens)
        self.assertIn("run.sh", tokens)

    def test_pyproject_toml_indexes_full_basename(self) -> None:
        """A second concrete example confirms the rule generalises."""
        tokens = _tokenize_path("pyproject.toml")
        self.assertIn("pyproject", tokens)
        self.assertIn("pyproject.toml", tokens)

    def test_build_bazel_indexes_full_basename(self) -> None:
        tokens = _tokenize_path("weld/BUILD.bazel")
        self.assertIn("BUILD", tokens)
        self.assertIn("BUILD.bazel", tokens)

    def test_extensionless_basename_unchanged(self) -> None:
        """``Makefile`` has no extension, so the stem equals the basename
        and no duplicate token must be added. This is the index-size
        regression guard for the common extensionless case.
        """
        tokens = _tokenize_path("Makefile")
        self.assertEqual(tokens, ["Makefile"])

    def test_dotfile_basename_unchanged(self) -> None:
        """Leading-dot filenames like ``.bazelrc`` have no extension by
        Python's ``Path.stem`` rule (the dot is the first character). The
        basename equals the stem, so no duplicate token must be added.
        """
        tokens = _tokenize_path(".bazelrc")
        self.assertEqual(tokens, [".bazelrc"])

    def test_python_module_basename_added(self) -> None:
        tokens = _tokenize_path("weld/file_index.py")
        self.assertIn("weld", tokens)
        self.assertIn("file_index", tokens)
        self.assertIn("file_index.py", tokens)


class FindFilesBasenameSearchTest(unittest.TestCase):
    """End-to-end: a literal-with-dot search must hit the file by name,
    and existing token-only searches must keep working.
    """

    @staticmethod
    def _index() -> dict[str, list[str]]:
        # Build the index the way ``build_file_index`` would: stem +
        # raw basename (when they differ) for the path itself, plus a
        # handful of content tokens to model real prose mentions.
        return {
            "install.sh": _tokenize_path("install.sh") + ["set", "echo"],
            "README.md": _tokenize_path("README.md") + [
                # README prose that mentions install.sh as a word would
                # be tokenized by the generic extractor; the extractor
                # never emits a literal token containing a dot, so the
                # only way a user can hit ``install.sh`` literally is
                # via the basename token added to install.sh itself.
                "install", "Quick", "Start",
            ],
            "docs/launch.md": _tokenize_path("docs/launch.md") + [
                "install", "launch", "guide",
            ],
            "Makefile": _tokenize_path("Makefile") + ["build", "test"],
            "pyproject.toml": _tokenize_path("pyproject.toml") + [
                "build-system", "tool",
            ],
        }

    def test_literal_dotted_query_hits_install_sh_at_top(self) -> None:
        """``wd find install.sh`` must rank install.sh first; before the
        fix, it returned only README/launch.md prose mentions.
        """
        result = find_files(self._index(), "install.sh")
        paths = [entry["path"] for entry in result["files"]]
        self.assertTrue(
            paths, "expected at least one match for 'install.sh'",
        )
        self.assertEqual(
            paths[0], "install.sh",
            f"install.sh must rank first for a literal-basename query; "
            f"got order {paths!r}",
        )

    def test_token_only_query_install_still_matches(self) -> None:
        """Regression guard: a single-token search (``install``) must
        still surface ``install.sh``. The new basename token contains
        ``install`` as a substring, so the existing matcher is happy.
        """
        result = find_files(self._index(), "install")
        paths = [entry["path"] for entry in result["files"]]
        self.assertIn("install.sh", paths)

    def test_token_only_query_sh_still_matches_install_sh(self) -> None:
        """Regression guard: the previously-working ``sh`` query path
        (which historically matched the stem-stripped suffix token) must
        still surface ``install.sh``. With the new basename token, ``sh``
        is a substring of ``install.sh`` so the match is preserved.
        """
        result = find_files(self._index(), "sh")
        paths = [entry["path"] for entry in result["files"]]
        self.assertIn("install.sh", paths)

    def test_pyproject_toml_literal_query_hits_pyproject_toml(self) -> None:
        result = find_files(self._index(), "pyproject.toml")
        paths = [entry["path"] for entry in result["files"]]
        self.assertIn("pyproject.toml", paths)


class TokenizePathIndexSizeRegressionTest(unittest.TestCase):
    """The basename-token rule must not silently inflate the index for
    extensionless filenames -- the common case for build/config files
    like ``Makefile``, ``BUILD``, ``Dockerfile``.
    """

    def test_no_duplicate_token_for_extensionless_filenames(self) -> None:
        for path in ("Makefile", "Dockerfile", "BUILD", "deploy/Dockerfile"):
            tokens = _tokenize_path(path)
            # Each token list must have no duplicates and length must
            # equal the path-segment count -- the basename rule must not
            # add a duplicate token for extensionless names.
            self.assertEqual(
                len(tokens), len(set(tokens)),
                f"unexpected duplicate token for {path!r}: {tokens!r}",
            )
            self.assertEqual(
                len(tokens), len(Path(path).parts),
                f"basename rule inflated index for extensionless {path!r}: "
                f"{tokens!r}",
            )


if __name__ == "__main__":
    unittest.main()
