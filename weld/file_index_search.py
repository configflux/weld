#!/usr/bin/env python3
"""Read-side search over the file keyword index.

``find_files`` is the matcher behind ``wd find`` and MCP ``weld_find`` /
``weld_references``. It is split out from :mod:`weld.file_index` (which
owns *building* the index) so the build and query halves stay within the
line-count budget and can evolve independently. :mod:`weld.file_index`
re-exports :func:`find_files` so existing imports keep working.

The matcher mirrors ``wd query`` tokenization: a single-word term keeps
the historical substring + basename-boost behaviour, while a multi-word
term is whitespace-tokenized and ranked by how many distinct query words
each file's tokens hit.
"""

from __future__ import annotations

import re
from pathlib import Path

# Splits a filename into its alphanumeric word components so the
# multi-word ranker can recognise a file the user named directly:
# ``mcp_server.py`` -> ``{'mcp', 'server', 'py'}``. Linear and anchored
# only by character class (no nested quantifiers), so it is ReDoS-free on
# any input.
_BASENAME_WORD_RE = re.compile(r"[a-z0-9]+")

# Boost added when the query is the file's literal basename. Must beat
# any plausible body-mention density; generic tokens cap at 512.
_BASENAME_MATCH_BOOST = 1024

# Upper bound on distinct query words considered for a multi-word term.
# ``wd find`` queries are short by nature; this cap keeps the matcher
# linear and DoS-safe if a crafted term arrives with thousands of
# whitespace-separated words. Deduplication already collapses repeats,
# so the cap only bites on adversarially diverse input.
_MAX_QUERY_WORDS = 32


def _find_single_token(
    index: dict[str, list[str]],
    term: str,
) -> list[dict]:
    """Single-word ``wd find`` path: case-insensitive substring match with
    the literal-basename boost. This is the historical behaviour, kept
    intact so dotted literals (``install.sh``) and single keywords
    (``mcp``, ``sh``) do not regress.
    """
    term_lower = term.lower()
    results: list[dict] = []
    for path, tokens in index.items():
        matching_tokens = [t for t in tokens if term_lower in t.lower()]
        if not matching_tokens:
            continue
        score = len(matching_tokens)
        if Path(path).name.lower() == term_lower:
            score += _BASENAME_MATCH_BOOST
        results.append({"path": path, "tokens": matching_tokens, "score": score})
    return results


def _basename_covers_all_words(path: str, distinct_words: list[str]) -> bool:
    """Return True if every query word is an alphanumeric component of the
    file's basename -- i.e. the user effectively spelled the filename out.

    ``mcp server`` over ``weld/mcp_server.py`` is True (basename words
    ``{mcp, server, py}`` cover both); ``mcp server`` over
    ``weld/tests/bootstrap_per_host_test.py`` is False. Only fires for
    genuine multi-word queries (the caller passes >= 2 distinct words),
    so it cannot promote on a lone-word match.
    """
    basename_words = set(_BASENAME_WORD_RE.findall(Path(path).name.lower()))
    return all(word in basename_words for word in distinct_words)


def _find_multi_word(
    index: dict[str, list[str]],
    words: list[str],
) -> list[dict]:
    """Multi-word ``wd find`` path, mirroring ``wd query`` tokenization.

    A file matches if any of its index tokens contains any query *word*
    as a substring (OR semantics). Results are returned already ranked,
    in priority order:

    1. files whose basename spells out *every* query word -- so ``wd find
       'mcp server'`` pins the file literally named ``mcp_server.py`` at
       the top, the multi-word analogue of the single-word
       literal-basename boost;
    2. number of *distinct* query words the file's tokens hit;
    3. raw matching-token count;
    4. path ascending (final tiebreak).

    Ranking uses a tuple sort key rather than a single weighted integer,
    so the "distinct words hit" axis dominates *regardless* of how many
    tokens a file carries -- there is no per-file token cap to assume, so
    a numeric weight could be defeated by a pathologically large file.
    The flat ``score`` field is emitted for display only (basename-boost +
    matching-token count); it is not the sort key.
    """
    # Dedupe + cap the query words: matching cost is O(files * tokens *
    # distinct_words), so bounding the last factor keeps a pathological
    # multi-word term linear in the index. ``dict.fromkeys`` preserves
    # first-seen order for a deterministic cap.
    distinct_words = list(dict.fromkeys(words))[:_MAX_QUERY_WORDS]
    ranked: list[tuple[tuple, dict]] = []
    for path, tokens in index.items():
        matching_tokens: list[str] = []
        words_hit: set[str] = set()
        for tok in tokens:
            tok_lower = tok.lower()
            hit = False
            for word in distinct_words:
                if word in tok_lower:
                    words_hit.add(word)
                    hit = True
            if hit:
                matching_tokens.append(tok)
        if not words_hit:
            continue
        basename_hit = _basename_covers_all_words(path, distinct_words)
        # Display score: basename boost (so the boosted set is visibly
        # ranked above the rest) plus matching-token count.
        score = len(matching_tokens) + (_BASENAME_MATCH_BOOST if basename_hit else 0)
        # Sort key: basename-cover, then distinct words hit, then token
        # count -- all descending -- then path ascending. Negated numeric
        # fields give descending order under ``sorted`` ascending.
        sort_key = (
            0 if basename_hit else 1,
            -len(words_hit),
            -len(matching_tokens),
            path,
        )
        ranked.append((sort_key, {
            "path": path, "tokens": matching_tokens, "score": score,
        }))
    ranked.sort(key=lambda item: item[0])
    return [entry for _, entry in ranked]


def find_files(
    index: dict[str, list[str]],
    term: str,
    limit: int | None = None,
) -> dict:
    """Search the index for files matching *term*.

    *term* is tokenized on whitespace, mirroring ``wd query``:

    * **Single word** (the common case, including dotted literals like
      ``install.sh``): case-insensitive substring match against each
      index token. A case-insensitive basename match adds
      ``_BASENAME_MATCH_BOOST`` so a literal query like ``wd find
      'publish.sh'`` pins the actual file ahead of docs that mention the
      basename in prose. Otherwise files rank by matching-token count
      descending, then path ascending.
    * **Multiple words** (e.g. ``wd find 'mcp server'``): a file matches
      if any of its tokens contains any query word (OR semantics), and
      files that hit more *distinct* query words rank first. This is the
      whitespace-tokenized analogue of the ``wd query`` multi-word path,
      so ``'mcp server'`` lands ``weld/mcp_server.py`` instead of the
      empty result the old single-substring matcher produced.

    ``limit`` slices results after ranking. The only regex used is a
    fixed module-level basename splitter (never compiled from *term*), and
    the distinct-word count is bounded, so a crafted term cannot trigger
    catastrophic backtracking or super-linear matching.
    """
    words = term.lower().split()
    if not words:
        return {"query": term, "files": []}
    if len(words) == 1:
        results = _find_single_token(index, words[0])
        results.sort(key=lambda r: (-r["score"], r["path"]))
    else:
        # Already ranked by ``_find_multi_word`` (tuple sort key).
        results = _find_multi_word(index, words)

    if limit is not None:
        results = results[:max(limit, 0)]

    return {"query": term, "files": results}
