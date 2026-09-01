"""The refusal markers, derived from ``weld._errors`` rather than copied.

ADR 0139 mechanism 4 (bd ``5038-fprr2``), which asks for exactly this in its
last sentence: "derived-not-restated applies inside the suite too --
``weld/tests/_graph_invariants.py``'s cannot-answer markers become imports of
the constants that produce them". Those markers used to be four string literals,
which made that module a second source of truth for a vocabulary
``weld/_errors.py`` already owns: the contract could be reworded and the
invariant would go on matching text nothing emitted any more, which is a
cannot-answer assertion that has quietly stopped asserting.

Two marker sets live here, one per question
:func:`weld.tests._graph_invariants.assert_cannot_answer` asks. **Did this
output refuse** is the first. Its derivation names the error **codes**, not the
strings, and yields three markers, one per surface a refusal can reach the
reader through:

* the structured line's ``error[`` prefix, cut off what
  :func:`weld._errors.format_error_line` actually builds. It carries every
  code, so it is the marker that covers ``file_index_missing`` and the rest
  without either being named here.
* the graph-missing summary, whole. The block at
  ``weld._graph_cli.missing_graph_message`` predates the structured line and
  emits this without an ``error[`` prefix.
* the ``result_unknown`` verdict clause, split off the summary at the ``--``
  aside this repository's prose uses -- the same clause boundary
  ``tools/contract_strings.py`` splits on. ``weld.impact_format`` prints the
  verdict on its own line, never the whole summary.

The fourth literal, ``cannot be computed``, is deliberately gone. No rule over
``weld/_errors.py`` reproduces it *and* matches the second producer: the tables
say "cross-repo dependents cannot be computed for this repo node" while
:mod:`weld._impact_cannot_answer` says "cross-repo dependents **of <label>**
cannot be computed", and the interpolated label breaks every shared phrase
longer than the three bare words -- so a principled derivation yields a
fragment that producer's own output does not contain. Restating the three words
is the copy this module exists to delete. Nothing loses coverage:
``weld.impact_cli._cannot_answer_exit`` writes the structured line to stderr
for the human *and* ``--json`` renders alike, so ``error[`` reaches every
impact refusal, and the human render carries the verdict clause on top.
``graph_invariants_cannot_answer_markers_test`` holds both halves of that
claim against the real producers.

**Did the refusal say what to do about it** is the second question, and the
remediation set answers it. That one is cut off three producers rather than one
table:

* the ``hint:`` label, taken from a real
  :func:`weld._errors.format_error_line` result -- the token that formatter
  writes immediately before the hint it renders.
* the ``Then retry:`` label, taken from a real
  :func:`weld._errors.structured_payload` retry field.
* the imperative an artifact-missing hint opens with, from the hints for
  ``graph_missing`` and ``file_index_missing``. This is the marker the
  first-run guidance block leans on: :func:`weld._graph_cli.\
missing_graph_message` predates the structured line and carries neither the
  ``error[`` prefix nor the ``hint:`` label, so without it the refusal a
  first-run user is most likely to meet would state no remediation as far as
  the invariant could tell. Both codes are named, not one, so neither hint can
  be reworded without the derivation moving.

The fourth literal, ``"See "``, is deliberately gone, and this paragraph is the
record of that decision (bd ``5038-hkb8x``). Two places in the tree emit it: the
no-resolver branch of :func:`weld._impact_cannot_answer.uncomputable_repo_reason`
("... See cross_repo_strategies in .weld/workspaces.yaml."), and
:mod:`weld._wiki_renderers`, which is not a refusal at all -- so half of what the
marker matched was prose that would have let a rendered wiki page satisfy a
remediation assertion. The refusal half loses nothing as the CLI emits it:
``wd impact`` writes :func:`weld._errors.format_error_line` to stderr for that
same refusal, so ``hint:`` covers it on the streams
:func:`weld.tests._graph_invariants.assert_cannot_answer` is handed, and
``graph_invariants_cannot_answer_markers_test.InvariantBehaviourUnchangedTest``
holds that against the real CLI exit path -- its fixture reaches the no-resolver
reason variant, which is the only refusal-path producer of ``"See "``.

The residue, stated rather than glossed: the impact *human render considered
alone* now carries no remediation marker, where ``"See "`` used to match its
reason prose. Nothing asks that question -- every caller passes the stream the
structured line goes to, and the two are written by one exit path -- and the
alternative was to keep, for a four-character fragment matching any prose that
contains the word, exactly the second source of truth this module exists to
delete.

Deriving in-process, off the imported module, rather than through
``contract_strings.fragments_from_file``: that helper reads the file so it can
also read it at another git ref, which is the sweep's problem and not this
one's. The objects here are the very ones the CLI formats with, so there is no
step between what is asserted and what is emitted for a skew to live in.

The derivation fails closed, for the reason ``contract_strings`` does: a marker
set that comes out short is silent in the direction that matters, making
:func:`weld.tests._graph_invariants.assert_cannot_answer` reject real refusals
and :func:`~weld.tests._graph_invariants.assert_answered_empty` accept them.
The remediation set fails closed too, against a quieter failure: no assertion
rejects output for *lacking* a remediation marker in the answered direction, so
a collapsed derivation there does not misclassify anything -- it stops
classifying. The empty string is a substring of every output, so a marker that
shrank to nothing would let a refusal stating no remediation at all pass the one
check that exists to catch it.
"""

from __future__ import annotations

import re

from weld import _errors

#: The aside this repository's prose uses inside a contract summary, and the
#: boundary the verdict clause is cut at. ``tools/contract_strings.py`` splits
#: on the same thing; spelled separately because that module is a Bazel-free
#: tool this test library has no reason to depend on for one delimiter.
_SUMMARY_ASIDE = " -- "

#: Codes whose standing summary marks output as a cannot-answer outcome. Two,
#: not all eight: these are the surfaces that refuse *without* the structured
#: line -- the graph-missing guidance block and the impact verdict -- and
#: widening the set would make ``assert_answered_empty`` reject an answered
#: result that merely quoted an unrelated summary.
_SUMMARY_CODES = (_errors.GRAPH_MISSING, _errors.RESULT_UNKNOWN)

#: Shortest marker worth matching on. Below this a marker stops discriminating:
#: ``assert_answered_empty`` fails whenever a healthy answer happens to contain
#: the fragment. Set at the length of the shortest marker the contract actually
#: produces (``error[``), so it admits today's set exactly and rejects anything
#: shorter -- a derivation that collapsed to a stray character, or a table entry
#: reworded down to a word.
_MIN_MARKER_LENGTH = 6

#: Codes whose remediation hint opens with a bare imperative -- the two
#: artifact-missing refusals, ``graph_missing`` and ``file_index_missing``.
#: Both are named so that rewording either one moves the derived set; naming
#: only the one whose block needs the marker would leave the other free to
#: drift into a different vocabulary unremarked.
_IMPERATIVE_HINT_CODES = (_errors.GRAPH_MISSING, _errors.FILE_INDEX_MISSING)

#: Stand-in retry command, used only to locate where the retry field's label
#: ends. Spelled so it cannot occur in the label prose being cut, which is what
#: keeps the cut a derivation rather than a guess about wording.
_RETRY_PROBE = "<retry-probe>"

#: Shortest remediation marker worth matching on, set the way
#: :data:`_MIN_MARKER_LENGTH` is -- at the length of the shortest marker the
#: contract produces today (``Run:``), so it admits this set exactly.
_MIN_REMEDIATION_LENGTH = 4

#: Tokens a hint names that a reader can act on: a config key or a path, told
#: apart from prose by carrying ``_`` or ``/``. Used by the parity test to ask
#: whether a second producer points at the same remediation the contract does,
#: without restating either one's wording.
_ACTIONABLE_TOKEN = re.compile(r"\S*[_/]\S*")


def error_line_prefix() -> str:
    """The ``error[`` prefix of a structured CLI line, from its own formatter.

    Cut off a real :func:`weld._errors.format_error_line` result rather than
    spelled, so a change to that shape moves this with it.
    """
    line = _errors.format_error_line(_errors.GRAPH_MISSING)
    cut = line.find("[")
    if cut < 0:
        raise ValueError(f"structured error line has no code bracket: {line!r}")
    return line[: cut + 1]


def verdict_clause(code: str) -> str:
    """The leading clause of *code*'s standing summary, or the whole summary.

    A summary that carries the ``--`` aside states its verdict first and
    explains it after; a renderer prints only the verdict. One without the
    aside is already the whole claim.
    """
    summary = _errors.default_summary(code)
    verdict, aside, _rest = summary.partition(_SUMMARY_ASIDE)
    return verdict.strip() if aside else summary


def cannot_answer_markers() -> tuple[str, ...]:
    """Substrings that mark output as a cannot-answer outcome (ADR 0134 s2).

    Raises rather than returning a short set when a marker comes out empty or
    too short to discriminate -- see this module's header for why a quiet short
    answer is the worse failure.
    """
    markers = (error_line_prefix(), *(verdict_clause(c) for c in _SUMMARY_CODES))
    undersized = [m for m in markers if len(m) < _MIN_MARKER_LENGTH]
    if undersized:
        raise ValueError(
            f"derived cannot-answer marker(s) too short to match on: {undersized}"
        )
    return markers


def actionable_hint_tokens(code: str) -> tuple[str, ...]:
    """Config keys and paths named by *code*'s remediation hint, sorted.

    Raises on an empty result: a hint that names nothing actionable would let
    the parity assertion built on it pass vacuously.
    """
    hint = _errors.ERROR_HINTS[code]
    tokens = {
        token.rstrip(".,;:()")
        for token in _ACTIONABLE_TOKEN.findall(hint)
    }
    found = tuple(sorted(tokens - {""}))
    if not found:
        raise ValueError(f"hint for {code!r} names no config key or path: {hint!r}")
    return found


def hint_label() -> str:
    """The label a structured error line writes before its remediation hint.

    Read off a real :func:`weld._errors.format_error_line` result: the last
    whitespace-delimited token before the hint text is the label that
    introduces it. Cut rather than spelled, so a reworded separator moves this
    with it instead of leaving the invariant matching a shape nothing emits.

    The label does not vary by code -- the formatter builds one line shape for
    all of them -- so which code the line is built from only has to satisfy the
    cut, which needs a non-empty hint to locate. ``graph_missing`` is the same
    code :func:`error_line_prefix` builds its line from, so the two functions
    read one rendering rather than two.
    """
    code = _errors.GRAPH_MISSING
    line = _errors.format_error_line(code)
    hint = _errors.ERROR_HINTS[code]
    cut = line.rfind(hint) if hint else -1
    tokens = line[:cut].split() if cut > 0 else []
    if not tokens:
        raise ValueError(f"structured error line introduces no hint: {line!r}")
    return tokens[-1]


def retry_label() -> str:
    """The label :func:`weld._errors.structured_payload` puts before a retry.

    Cut off a payload built with :data:`_RETRY_PROBE`, so the label is whatever
    that function actually writes ahead of the command it was handed.
    """
    payload = _errors.structured_payload(
        _errors.GRAPH_MISSING, retry_cmd=_RETRY_PROBE,
    )
    retry = payload.get("retry")
    if not isinstance(retry, str):
        raise ValueError(f"structured payload carries no retry field: {payload!r}")
    label, found, _rest = retry.partition(_RETRY_PROBE)
    if not found or not label.strip():
        raise ValueError(f"retry field introduces no command: {retry!r}")
    return label.strip()


def hint_imperative(code: str) -> str:
    """The ``<Verb>:`` opening of *code*'s remediation hint.

    A hint that opens with one bare word and a colon is telling the reader to
    run something; that opening is the marker. Raises when the hint has no
    such opening rather than returning a fragment of its prose -- a marker cut
    out of mid-sentence text would match wherever that phrasing recurred.
    """
    hint = _errors.ERROR_HINTS[code]
    head, found, _rest = hint.partition(":")
    verb = head.strip()
    if not found or not verb or " " in verb:
        raise ValueError(
            f"hint for {code!r} does not open with an imperative: {hint!r}"
        )
    return f"{verb}:"


def remediation_markers() -> tuple[str, ...]:
    """Substrings that mark a refusal as stating what to do about itself.

    De-duplicated, because the two artifact-missing hints open with the same
    imperative today and a repeated marker would only pad the failure message.
    Raises rather than returning a short set, for the reason in this module's
    header: a marker below the discriminating length makes the remediation
    check pass on a refusal that states none.
    """
    markers = dict.fromkeys(
        (
            hint_label(),
            retry_label(),
            *(hint_imperative(code) for code in _IMPERATIVE_HINT_CODES),
        )
    )
    undersized = [m for m in markers if len(m) < _MIN_REMEDIATION_LENGTH]
    if undersized:
        raise ValueError(
            f"derived remediation marker(s) too short to match on: {undersized}"
        )
    return tuple(markers)
