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

This test pins the tokenizer-level contract of
:func:`weld._file_index_extractors._tokenize_path` and the end-to-end
behaviour of :func:`weld.file_index_search.find_files` -- both reached here
through the :mod:`weld.file_index` facade that re-exports them -- so the gap
cannot silently regress.
"""

from __future__ import annotations

import unittest
from pathlib import Path


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


class FindFilesBasenameBoostTest(unittest.TestCase):
    """Ranking: a literal-basename query must rank the file itself above
    documents that merely mention the same basename in prose.

    Background
    ----------
    Indexing the raw basename (``install.sh``, ``publish.sh``,
    ``BUILD.bazel``) lets a literal-with-dot query match the file by
    name. But a generic-text extractor (``_extract_generic_tokens``)
    also harvests dotted tokens out of prose, so a doc that mentions
    ``tools/publish.sh`` once also yields a single matching token. With
    the previous ``score == len(matching_tokens)`` ranker, the file and
    every doc that mentioned it tied at score 1, and the order fell back
    to alphabetical path -- ``docs/`` shows up before ``tools/``, so the
    actual file dropped below docs that merely referred to it.

    These tests pin the new contract, which lives in
    :mod:`weld.file_index_search`: when a matching token equals the file's
    own basename AND is a case-insensitive exact match for the query, that
    file gets the ``_BASENAME_MATCH_BOOST`` score boost so it wins ties
    against body mentions.
    """

    def test_publish_sh_ranks_above_docs_that_mention_it(self) -> None:
        """The reported regression: ``wd find 'publish.sh'`` must put
        ``tools/publish.sh`` first, above three docs that each carry one
        body mention of ``publish.sh`` / ``tools/publish.sh``.
        """
        index = {
            "tools/publish.sh": _tokenize_path("tools/publish.sh") + [
                "set", "echo", "release",
            ],
            "tools/audit_publish.sh": _tokenize_path(
                "tools/audit_publish.sh"
            ) + ["set", "echo"],
            # Body mentions: the generic extractor harvests dotted
            # tokens out of prose, so each of these contributes exactly
            # one matching token (the literal string from the doc).
            "docs/release.md": _tokenize_path("docs/release.md") + [
                "publish.sh", "Release", "guide",
            ],
            "docs/adrs/0048-release-coordinator-agent.md": _tokenize_path(
                "docs/adrs/0048-release-coordinator-agent.md"
            ) + ["tools/publish.sh", "ADR"],
            "docs/postmortems/2026-05-02-x.md": _tokenize_path(
                "docs/postmortems/2026-05-02-x.md"
            ) + ["tools/publish.sh", "postmortem"],
        }
        result = find_files(index, "publish.sh")
        paths = [entry["path"] for entry in result["files"]]
        self.assertTrue(paths, "expected at least one match")
        self.assertEqual(
            paths[0], "tools/publish.sh",
            f"tools/publish.sh must rank first; got {paths!r}",
        )

    def test_basename_match_outranks_multiple_body_mentions(self) -> None:
        """A file whose basename equals the query must rank above a doc
        that mentions the same basename multiple times. The boost is
        large enough that ordinary prose densities cannot defeat it.
        """
        index = {
            "tools/publish.sh": _tokenize_path("tools/publish.sh") + [
                "set", "echo",
            ],
            # Doc with two prose mentions of the basename; the generic
            # extractor deduplicates within a file, but we model two
            # distinct tokens that both contain "publish.sh" as a
            # substring to simulate maximum body density.
            "docs/release.md": _tokenize_path("docs/release.md") + [
                "publish.sh", "tools/publish.sh", "release",
            ],
        }
        result = find_files(index, "publish.sh")
        paths = [entry["path"] for entry in result["files"]]
        self.assertEqual(paths[0], "tools/publish.sh")

    def test_basename_match_is_case_insensitive(self) -> None:
        """Query case must not affect the basename boost.
        ``BUILD.bazel`` is the canonical mixed-case basename in this
        repo; ensure ``wd find 'build.bazel'`` still ranks the file first.
        """
        index = {
            "weld/BUILD.bazel": _tokenize_path("weld/BUILD.bazel") + [
                "py_library", "py_test",
            ],
            "docs/build.md": _tokenize_path("docs/build.md") + [
                "BUILD.bazel", "guide",
            ],
        }
        result = find_files(index, "build.bazel")
        paths = [entry["path"] for entry in result["files"]]
        self.assertEqual(paths[0], "weld/BUILD.bazel")

    def test_substring_query_does_not_get_basename_boost(self) -> None:
        """A query that is a substring of a basename (but not equal to
        it) must NOT trigger the boost -- otherwise every ``.sh`` file
        would be promoted on a query for ``sh``. Existing substring
        ranking by len(matching_tokens) must be preserved for that case.
        """
        index = {
            # Two .sh files: one with one matching token, one with three.
            "tools/a.sh": _tokenize_path("tools/a.sh"),  # 'a', 'a.sh'
            "tools/b.sh": _tokenize_path("tools/b.sh") + [
                "she", "shell", "sharing",
            ],
        }
        result = find_files(index, "sh")
        paths = [entry["path"] for entry in result["files"]]
        # b.sh has more matching tokens; without boost it must rank above a.sh.
        self.assertEqual(paths[0], "tools/b.sh")

    def test_score_remains_positive_int(self) -> None:
        """The score field must remain a positive integer so existing
        consumers/CLI formatting (``%5d``) keep working.
        """
        index = {
            "tools/publish.sh": _tokenize_path("tools/publish.sh"),
            "docs/release.md": _tokenize_path("docs/release.md") + [
                "publish.sh",
            ],
        }
        result = find_files(index, "publish.sh")
        for entry in result["files"]:
            self.assertIsInstance(entry["score"], int)
            self.assertGreater(entry["score"], 0)

    def test_basename_boost_does_not_reorder_basename_vs_basename(self) -> None:
        """Two files whose basenames both equal the query (impossible
        in practice for the same path, but possible across paths via a
        nested file with the same basename, e.g. two ``BUILD.bazel`` files)
        must remain ordered by path ascending -- the existing tiebreak.
        """
        index = {
            "weld/BUILD.bazel": _tokenize_path("weld/BUILD.bazel"),
            "tools/BUILD.bazel": _tokenize_path("tools/BUILD.bazel"),
        }
        result = find_files(index, "BUILD.bazel")
        paths = [entry["path"] for entry in result["files"]]
        self.assertEqual(paths, ["tools/BUILD.bazel", "weld/BUILD.bazel"])


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
