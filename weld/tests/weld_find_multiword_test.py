"""Regression: ``wd find`` must tokenize space-separated multi-word terms.

Background
----------
``find_files`` historically did a single literal substring match against
each index token (``term.lower() in token.lower()``). Index tokens never
contain spaces, so a space-separated query like ``wd find 'mcp server'``
matched nothing -- even though ``wd find 'mcp'`` returned the right files
and ``wd query 'mcp server'`` worked. This mirrors the already-closed
multi-word ``wd query`` gap (strict-AND with OR fallback) and the
``install.sh`` dot-tokenization gap, but for the ``wd find`` whitespace
case which was still live.

Contract pinned here
--------------------
* A multi-word term is split on whitespace; a file matches if any of its
  index tokens contains any query token (OR semantics).
* Ranking surfaces files that hit more distinct query tokens first, so a
  file whose tokens contain *both* words (``weld/mcp_server.py`` ->
  ``mcp_server`` contains ``mcp`` and ``server``) ranks above a file that
  hits only one word.
* Single-token behaviour is byte-for-byte unchanged (substring match +
  basename boost), so ``wd find 'mcp'`` and ``wd find 'install.sh'`` do
  not regress.
* The matcher is substring-only (no regex compiled from user input) and
  bounded, so a pathological multi-word term cannot hang or crash.

The index is built in-memory so the assertions are independent of disk
layout, Bazel sandboxing, and fixture drift.
"""

from __future__ import annotations

import unittest


from weld.file_index import _tokenize_path, find_files  # noqa: E402


def _mcp_like_index() -> dict[str, list[str]]:
    """An index slice modelling the real ``mcp server`` reproduction.

    ``weld/mcp_server.py`` tokenizes to ``['weld', 'mcp_server',
    'mcp_server.py']``; ``mcp_server`` contains both ``mcp`` and
    ``server`` as substrings, so it must hit both query words. The other
    files each hit at most one word.
    """
    return {
        "weld/mcp_server.py": _tokenize_path("weld/mcp_server.py") + [
            "load_file_index", "weld_find",
        ],
        "weld/server.py": _tokenize_path("weld/server.py") + ["Server"],
        "weld/mcp_tools.py": _tokenize_path("weld/mcp_tools.py") + ["mcp"],
        "docs/mcp.md": _tokenize_path("docs/mcp.md") + ["MCP", "guide"],
        "docs/unrelated.md": _tokenize_path("docs/unrelated.md") + ["intro"],
    }


class FindFilesMultiWordTest(unittest.TestCase):
    """The headline bug: ``mcp server`` must surface ``mcp_server.py``."""

    def test_multiword_term_returns_matches(self) -> None:
        result = find_files(_mcp_like_index(), "mcp server")
        paths = [entry["path"] for entry in result["files"]]
        self.assertTrue(
            paths,
            "multi-word 'mcp server' must not return an empty file list",
        )
        self.assertIn(
            "weld/mcp_server.py", paths,
            f"'mcp server' must surface weld/mcp_server.py; got {paths!r}",
        )

    def test_multiword_ranks_both_word_hits_above_single_word_hits(self) -> None:
        """A file whose tokens hit both words ranks above files hitting one.

        ``weld/mcp_server.py`` (``mcp_server`` contains both ``mcp`` and
        ``server``) must outrank ``weld/server.py`` (``server`` only) and
        ``docs/mcp.md`` (``mcp`` only).
        """
        result = find_files(_mcp_like_index(), "mcp server")
        paths = [entry["path"] for entry in result["files"]]
        self.assertEqual(
            paths[0], "weld/mcp_server.py",
            f"the file matching both words must rank first; got {paths!r}",
        )
        # docs/unrelated.md hits neither word and must be excluded.
        self.assertNotIn("docs/unrelated.md", paths)

    def test_literally_named_file_outranks_higher_token_density(self) -> None:
        """The file the user named must win even against a file that hits
        both words in *more* tokens.

        ``weld/mcp_server.py`` spells out ``mcp`` and ``server`` in its
        basename, so it must rank above ``tests/smoke_test.py`` which
        mentions both words only in many content tokens (not its name).
        This is the multi-word analogue of the single-word
        literal-basename boost and is exactly the headline expectation of
        the bug ('mcp server' lands mcp_server.py).
        """
        index = {
            "weld/mcp_server.py": _tokenize_path("weld/mcp_server.py"),
            # Many content tokens that each contain a query word, modelling
            # a test file that talks about the mcp server constantly but
            # whose *basename* does not spell out the query words.
            "tests/smoke_test.py": _tokenize_path("tests/smoke_test.py") + [
                "mcp_a", "mcp_b", "mcp_c", "server_a", "server_b",
                "server_c", "mcp_server_d", "mcp_server_e",
            ],
        }
        result = find_files(index, "mcp server")
        paths = [entry["path"] for entry in result["files"]]
        self.assertEqual(
            paths[0], "weld/mcp_server.py",
            f"the literally-named file must rank first; got {paths!r}",
        )

    def test_distinct_word_hits_dominate_token_count(self) -> None:
        """Ranking must put a file hitting MORE distinct query words above a
        file hitting fewer words, even if the loser carries far more
        matching tokens. This pins the tuple-sort contract so the ranking
        cannot silently regress to a magnitude-fragile weighted integer (a
        real file in this repo has 513 tokens, which would defeat a naive
        512-based weight).
        """
        # Loser: hits only 'mcp', but in 600 distinct tokens.
        many_mcp_tokens = [f"mcp_{i}" for i in range(600)]
        index = {
            "weld/huge_mcp_only.py": _tokenize_path("weld/huge_mcp_only.py")
            + many_mcp_tokens,
            # Winner: hits BOTH words, in just two tokens, and its basename
            # does not spell out the words (so the basename boost is not
            # what carries it -- the distinct-word axis is).
            "weld/both.py": _tokenize_path("weld/both.py") + ["mcp", "server"],
        }
        result = find_files(index, "mcp server")
        paths = [entry["path"] for entry in result["files"]]
        self.assertEqual(
            paths[0], "weld/both.py",
            f"a 2-word hit must outrank a 1-word hit regardless of token "
            f"count; got {paths!r}",
        )

    def test_basename_boost_requires_all_words_in_basename(self) -> None:
        """The basename boost must not fire unless *every* query word is a
        basename component -- otherwise a file matching one word in its
        name would be over-promoted.
        """
        index = {
            # 'server' is in the basename, 'mcp' is not -> no boost; ranks
            # purely on word-hit count and token density.
            "weld/server.py": _tokenize_path("weld/server.py") + ["mcp"],
            # both words are basename components -> boosted, must win.
            "weld/mcp_server.py": _tokenize_path("weld/mcp_server.py"),
        }
        result = find_files(index, "mcp server")
        paths = [entry["path"] for entry in result["files"]]
        self.assertEqual(paths[0], "weld/mcp_server.py")

    def test_multiword_score_is_positive_int(self) -> None:
        result = find_files(_mcp_like_index(), "mcp server")
        for entry in result["files"]:
            self.assertIsInstance(entry["score"], int)
            self.assertGreater(entry["score"], 0)

    def test_multiword_tokens_field_reports_matching_tokens(self) -> None:
        """Each entry's ``tokens`` field must list the tokens it matched,
        so a consumer can see *why* a file was returned.
        """
        result = find_files(_mcp_like_index(), "mcp server")
        top = result["files"][0]
        self.assertEqual(top["path"], "weld/mcp_server.py")
        # mcp_server / mcp_server.py both contain both words.
        self.assertTrue(top["tokens"], "matching tokens must not be empty")
        for tok in top["tokens"]:
            self.assertTrue(
                "mcp" in tok.lower() or "server" in tok.lower(),
                f"reported token {tok!r} matches neither query word",
            )

    def test_multiword_query_field_echoes_original_term(self) -> None:
        """The envelope ``query`` must echo the original (un-split) term so
        the CLI/MCP surface can display what the user typed.
        """
        result = find_files(_mcp_like_index(), "mcp server")
        self.assertEqual(result["query"], "mcp server")

    def test_multiword_limit_caps_after_ranking(self) -> None:
        result = find_files(_mcp_like_index(), "mcp server", limit=1)
        self.assertEqual(len(result["files"]), 1)
        self.assertEqual(result["files"][0]["path"], "weld/mcp_server.py")

    def test_second_reproduction_query_cli(self) -> None:
        """The issue also reproduced with ``wd find 'query cli'``."""
        index = {
            "weld/_graph_cli.py": _tokenize_path("weld/_graph_cli.py") + [
                "render_query", "render_find",
            ],
            "weld/graph_query.py": _tokenize_path("weld/graph_query.py") + [
                "query_graph",
            ],
            "weld/cli.py": _tokenize_path("weld/cli.py") + ["main"],
            "docs/readme.md": _tokenize_path("docs/readme.md") + ["intro"],
        }
        result = find_files(index, "query cli")
        paths = [entry["path"] for entry in result["files"]]
        self.assertTrue(paths, "'query cli' must return matches")
        # _graph_cli.py path tokens contain '_graph_cli' (has 'cli') and the
        # added 'render_query' token (has 'query') -> hits both words.
        self.assertIn("weld/_graph_cli.py", paths)


class FindFilesSingleTokenRegressionTest(unittest.TestCase):
    """Single-token behaviour must be unchanged by the multi-word path."""

    def test_single_token_mcp_unchanged(self) -> None:
        """``mcp`` (one word) must still substring-match every mcp token."""
        result = find_files(_mcp_like_index(), "mcp")
        paths = [entry["path"] for entry in result["files"]]
        self.assertIn("weld/mcp_server.py", paths)
        self.assertIn("weld/mcp_tools.py", paths)
        self.assertIn("docs/mcp.md", paths)
        # weld/server.py has no 'mcp' substring anywhere -> excluded.
        self.assertNotIn("weld/server.py", paths)

    def test_single_dotted_literal_still_gets_basename_boost(self) -> None:
        """``install.sh`` is ONE whitespace token; the dotted-literal +
        basename-boost path must be untouched by multi-word handling.
        """
        index = {
            "install.sh": _tokenize_path("install.sh") + ["set", "echo"],
            "docs/launch.md": _tokenize_path("docs/launch.md") + [
                "install", "guide",
            ],
            "README.md": _tokenize_path("README.md") + ["install", "Start"],
        }
        result = find_files(index, "install.sh")
        paths = [entry["path"] for entry in result["files"]]
        self.assertEqual(
            paths[0], "install.sh",
            f"single dotted-literal must still pin the file first; "
            f"got {paths!r}",
        )

    def test_single_token_sh_still_no_basename_boost(self) -> None:
        """``sh`` (substring of a basename, not equal) must not be boosted;
        ranking stays by matching-token count.
        """
        index = {
            "tools/a.sh": _tokenize_path("tools/a.sh"),
            "tools/b.sh": _tokenize_path("tools/b.sh") + [
                "she", "shell", "sharing",
            ],
        }
        result = find_files(index, "sh")
        paths = [entry["path"] for entry in result["files"]]
        self.assertEqual(paths[0], "tools/b.sh")


class FindFilesMultiWordEdgeCaseTest(unittest.TestCase):
    """Whitespace edge cases and empty-result handling."""

    def test_leading_trailing_internal_whitespace_collapses(self) -> None:
        """Extra whitespace must be treated as a single separator -- the
        same ``str.split()`` semantics ``wd query`` uses.
        """
        index = _mcp_like_index()
        baseline = find_files(index, "mcp server")
        spaced = find_files(index, "  mcp   server  ")
        self.assertEqual(
            [f["path"] for f in spaced["files"]],
            [f["path"] for f in baseline["files"]],
        )

    def test_multiword_no_match_returns_empty_files(self) -> None:
        """A multi-word term with no token hits returns an empty file list,
        not a crash.
        """
        result = find_files(_mcp_like_index(), "zzz nonexistent")
        self.assertEqual(result["files"], [])
        self.assertEqual(result["query"], "zzz nonexistent")

    def test_only_one_word_matches_still_returns_that_file(self) -> None:
        """OR semantics: if only one query word hits anything, the matching
        files are still returned (not dropped by an AND requirement).
        """
        index = {
            "weld/server.py": _tokenize_path("weld/server.py") + ["Server"],
            "docs/intro.md": _tokenize_path("docs/intro.md") + ["intro"],
        }
        # 'mcp' matches nothing here; 'server' matches weld/server.py.
        result = find_files(index, "mcp server")
        paths = [entry["path"] for entry in result["files"]]
        self.assertEqual(paths, ["weld/server.py"])


class FindFilesMultiWordSafetyTest(unittest.TestCase):
    """Crafted multi-word terms must terminate quickly and never crash --
    no regex is compiled from user input, and token handling is bounded.
    """

    def test_pathological_long_multiword_term_terminates(self) -> None:
        """A term of thousands of whitespace-separated words must not hang
        or raise. We assert it returns a well-formed envelope; the cap on
        distinct query tokens keeps matching linear.
        """
        index = _mcp_like_index()
        term = " ".join(["mcp"] * 5000 + ["server"] * 5000 + ["zz"] * 5000)
        result = find_files(index, term)
        self.assertIn("files", result)
        self.assertIsInstance(result["files"], list)
        # 'mcp' and 'server' both hit weld/mcp_server.py.
        paths = [entry["path"] for entry in result["files"]]
        self.assertIn("weld/mcp_server.py", paths)

    def test_regex_metacharacters_in_term_are_literal(self) -> None:
        """Regex metacharacters must be treated as literal substrings (the
        matcher uses ``in``, never ``re``), so a crafted term like
        ``.*+ (a|b)`` neither matches everything nor raises.
        """
        index = _mcp_like_index()
        result = find_files(index, ".* (a|b)+")
        # None of the tokens literally contain these metachar strings.
        self.assertEqual(result["files"], [])


if __name__ == "__main__":
    unittest.main()
