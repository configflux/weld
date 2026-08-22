"""Terminal control-sequence containment on the human-readable `wd` surface.

Node ids are derived from scanned file paths and symbol names, so a hostile
repository can name a file ``foo\x1b[2J.py`` and get ANSI/control bytes into a
graph id. Every human-readable render path then replays those bytes into the
operator's terminal (screen clears, cursor jumps, text that lies about what
happened). This suite pins the containment contract:

* :func:`weld._safe_text.sanitize_terminal_text` escapes the dangerous
  classes -- C0 minus TAB/LF, DEL, and C1 (which a UTF-8 terminal still
  decodes as controls, ``\\x9b`` being CSI) -- to a visible ``\\xNN``, plus
  the bidi override/isolate set to ``\\uNNNN``.
* The escape is applied at the *write* boundary of the rendered-text path, so
  every ``wd`` read command is covered by one contract rather than per-site
  escaping.

The ``--json`` half of the contract lives in
:mod:`weld.tests.weld_json_terminal_safety_test`: ``json.dumps`` escapes all
of C0 but passes DEL/C1 through verbatim, so that surface needs its own
encoding-level escape and its own parity proof.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from weld import _errors
from weld._graph_cli import main as graph_cli_main
from weld._safe_text import sanitize_terminal_line, sanitize_terminal_text

#: A screen-clearing sequence -- the canonical proof-of-concept payload.
CLEAR_SCREEN = "\x1b[2J"

#: RIGHT-TO-LEFT OVERRIDE, spelled by code point: a source file holding the
#: raw character would reorder its own display in every editor that renders it.
_RLO = chr(0x202E)


def _run_and_capture(fn, argv):
    """Invoke *fn(argv)* and return (exit_code, stdout, stderr)."""
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    exit_code = 0
    try:
        with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
            fn(argv)
    except SystemExit as exc:
        code = exc.code
        if code is None:
            exit_code = 0
        elif isinstance(code, int):
            exit_code = code
        else:
            exit_code = 1
    return exit_code, stdout_buf.getvalue(), stderr_buf.getvalue()


class SanitizeTerminalTextTest(unittest.TestCase):
    """Unit contract for the single sanitization helper."""

    def test_escape_sequence_is_neutralized(self):
        self.assertEqual(
            sanitize_terminal_text(f"file:foo{CLEAR_SCREEN}bar"),
            "file:foo\\x1b[2Jbar",
        )

    def test_dangerous_classes_are_escaped(self):
        # NUL, BEL, backspace, CR (line-overwrite), ESC, DEL, and C1 CSI.
        for raw, expected in (
            ("\x00", "\\x00"),
            ("\x07", "\\x07"),
            ("\x08", "\\x08"),
            ("\r", "\\x0d"),
            ("\x1b", "\\x1b"),
            ("\x7f", "\\x7f"),
            ("\x9b", "\\x9b"),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(sanitize_terminal_text(raw), expected)

    def test_line_variant_also_escapes_newlines(self):
        # A one-line contract cannot afford a smuggled LF: it would forge a
        # second line that looks like a separate, real diagnostic.
        self.assertEqual(sanitize_terminal_line("a\nb"), "a\\x0ab")
        self.assertEqual(sanitize_terminal_line("a\tb"), "a\tb")

    def test_layout_whitespace_is_preserved(self):
        # TAB and LF are the rendered layout itself; escaping them would
        # destroy every multi-line envelope this runs over.
        self.assertEqual(sanitize_terminal_text("a\tb\nc"), "a\tb\nc")

    def test_clean_text_is_returned_unchanged(self):
        # Byte-identity for the overwhelmingly common case: no diff in
        # existing output, and the fast path returns the very same object.
        clean = "# query: auth\n  matches (1):\n    1. symbol:py:a:b\n"
        self.assertIs(sanitize_terminal_text(clean), clean)

    def test_non_ascii_text_is_untouched(self):
        # Printable non-ASCII (accents, CJK, emoji) is legitimate content.
        text = "café 中文 \U0001f600  "
        self.assertEqual(sanitize_terminal_text(text), text)

    def test_escaped_output_contains_no_control_bytes(self):
        raw = "".join(chr(c) for c in range(0x00, 0xA0)) + "\x7f"
        out = sanitize_terminal_text(raw)
        residual = [c for c in out if c not in "\t\n" and (
            ord(c) < 0x20 or 0x7F <= ord(c) <= 0x9F
        )]
        self.assertEqual(residual, [], f"control bytes survived: {residual!r}")


class BidiOverrideTest(unittest.TestCase):
    """Trojan Source: glyph reordering, escaped; directional marks, kept.

    An override makes a node id *render* as a different id than it is, so an
    operator can be shown ``safe.py`` for an id that is nothing of the sort.
    The marks are a different matter -- they carry legitimate meaning in
    right-to-left text, and blanket-escaping them would damage real output to
    buy a much weaker guarantee.
    """

    #: LRE, RLE, PDF, LRO, RLO and the four isolate controls.
    OVERRIDES = tuple(chr(c) for c in list(range(0x202A, 0x202F)) + list(
        range(0x2066, 0x206A)
    ))
    #: LRM, RLM, ALM -- legitimate in real bidi content.
    MARKS = (chr(0x200E), chr(0x200F), chr(0x061C))

    def test_every_override_is_escaped(self):
        for ch in self.OVERRIDES:
            with self.subTest(codepoint=f"U+{ord(ch):04X}"):
                out = sanitize_terminal_text(f"a{ch}b")
                self.assertEqual(out, f"a\\u{ord(ch):04x}b")
                self.assertNotIn(ch, out)

    def test_escape_uses_u_form_not_x_form(self):
        # \x202e would read as \x20 followed by a literal "2e" -- exactly the
        # ambiguity the escape exists to remove.
        self.assertEqual(sanitize_terminal_text(_RLO), "\\u202e")

    def test_directional_marks_survive(self):
        for ch in self.MARKS:
            with self.subTest(codepoint=f"U+{ord(ch):04X}"):
                self.assertEqual(sanitize_terminal_text(f"a{ch}b"), f"a{ch}b")

    def test_right_to_left_letters_are_untouched(self):
        text = "مرحبا שלום"
        self.assertIs(sanitize_terminal_text(text), text)

    def test_line_variant_escapes_overrides_too(self):
        self.assertEqual(sanitize_terminal_line(f"a{_RLO}b"), "a\\u202eb")

    def test_hostile_id_no_longer_reorders(self):
        # The canonical Trojan Source shape: RLO makes the tail render mirrored.
        out = sanitize_terminal_text(f"file:src/{_RLO}gpj.exe")
        self.assertNotIn(_RLO, out)
        self.assertIn("\\u202e", out)


class _HostileGraphCase(unittest.TestCase):
    """Base fixture: a graph whose node id carries a screen-clear sequence."""

    node_id = f"symbol:py:evil{CLEAR_SCREEN}:handler"

    #: A second node with a hostile `props.summary` and no `description` at
    #: all, so rendering it exercises the fallback path (bd ph1g / ADR 0114:
    #: a module's own opening docstring line, rendered when no enrichment
    #: description exists). Its id and label are clean on purpose -- the
    #: escape lives only in the field under test.
    summary_node_id = "file:weld/evilsummary.py"

    #: Same shape as ``summary_node_id``, but ``type: symbol`` (bd p6ke: a
    #: symbol's own docstring now populates ``props.summary`` too). Id and
    #: label clean on purpose -- the escape lives only in the field under
    #: test, proving `prose_line`'s fallback is type-agnostic, not re-derived
    #: per node type.
    symbol_summary_node_id = "symbol:py:weld.evilsymbol:handler"

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._weld = os.path.join(self._tmp, ".weld")
        os.makedirs(self._weld)
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        graph = {
            "meta": {"version": 1},
            "nodes": {
                self.node_id: {
                    "type": "symbol",
                    "label": f"handler{CLEAR_SCREEN}",
                    "props": {"description": f"does {CLEAR_SCREEN} things"},
                },
                self.summary_node_id: {
                    "type": "file",
                    "label": "evilsummary.py",
                    "props": {
                        "file": "weld/evilsummary.py",
                        "summary": f"a module about {CLEAR_SCREEN} evil things",
                    },
                },
                self.symbol_summary_node_id: {
                    "type": "symbol",
                    "label": "handler",
                    "props": {
                        "qualname": "handler",
                        "summary": f"a function about {CLEAR_SCREEN} evil things",
                    },
                },
            },
            "edges": [],
        }
        with open(os.path.join(self._weld, "graph.json"), "w", encoding="utf-8") as fh:
            json.dump(graph, fh)
        self._prev_refresh = os.environ.get("WELD_AUTO_REFRESH")
        os.environ["WELD_AUTO_REFRESH"] = "0"
        self.addCleanup(self._restore_refresh)

    def _restore_refresh(self):
        if self._prev_refresh is None:
            os.environ.pop("WELD_AUTO_REFRESH", None)
        else:
            os.environ["WELD_AUTO_REFRESH"] = self._prev_refresh

    def _wd(self, *argv):
        """Run one ``wd`` command against the hostile fixture.

        Refresh is frozen by environment rather than by ``--no-refresh``:
        ``stats`` does not carry that flag, so passing it made argparse exit
        with empty stdout and every ``assertNotIn("\\x1b", stdout)`` below
        passed without exercising anything.
        """
        return _run_and_capture(
            graph_cli_main, ["--root", self._tmp, *argv],
        )


class RenderedOutputContainmentTest(_HostileGraphCase):
    """No `wd` text render replays a control byte from the graph."""

    def test_query_text_output_has_no_raw_escape(self):
        exit_code, stdout, _stderr = self._wd("query", "evil")
        self.assertEqual(exit_code, 0)
        self.assertNotIn("\x1b", stdout)
        self.assertIn("\\x1b[2J", stdout)

    def test_context_text_output_has_no_raw_escape(self):
        _exit_code, stdout, _stderr = self._wd("context", self.node_id)
        self.assertNotIn("\x1b", stdout)
        self.assertIn("\\x1b[2J", stdout)

    def test_node_not_found_text_output_has_no_raw_escape(self):
        # The named site: emit_node_lookup renders a caller-supplied id back.
        exit_code, stdout, stderr = self._wd(
            "context", f"symbol:py:missing{CLEAR_SCREEN}",
        )
        self.assertNotEqual(exit_code, 0)
        self.assertNotIn("\x1b", stdout + stderr)

    def test_callers_text_output_has_no_raw_escape(self):
        _exit_code, stdout, stderr = self._wd("callers", self.node_id)
        self.assertNotIn("\x1b", stdout + stderr)

    def test_stats_text_output_has_no_raw_escape(self):
        _exit_code, stdout, _stderr = self._wd("stats")
        # Assert the command actually produced a render before concluding
        # anything about what it did not contain.
        self.assertTrue(stdout.strip(), "wd stats produced no output")
        self.assertNotIn("\x1b", stdout)


class SummaryFallbackContainmentTest(_HostileGraphCase):
    """`props.summary` (bd ph1g / ADR 0114) is repo-controlled text with no
    enrichment gate -- discovery writes it for ~100% of Python file nodes,
    unreviewed. It now renders as the human-output fallback when a node has
    no `description` (this issue), so it must cross the same escape
    boundary as every other rendered field. ``summary_node_id`` carries no
    `description` at all, so what runs here is specifically the fallback
    path, not the already-covered `description` line.
    """

    def test_query_summary_fallback_has_no_raw_escape(self):
        exit_code, stdout, _stderr = self._wd("query", "evilsummary")
        self.assertEqual(exit_code, 0)
        self.assertIn("summary:", stdout)
        self.assertNotIn("\x1b", stdout)
        self.assertIn("\\x1b[2J", stdout)

    def test_context_summary_fallback_has_no_raw_escape(self):
        _exit_code, stdout, _stderr = self._wd(
            "context", self.summary_node_id,
        )
        self.assertIn("summary:", stdout)
        self.assertNotIn("\x1b", stdout)
        self.assertIn("\\x1b[2J", stdout)


class SymbolSummaryFallbackContainmentTest(_HostileGraphCase):
    """Same fallback path, one node type down (bd p6ke). Nothing here should
    behave differently from `SummaryFallbackContainmentTest` -- pinned
    anyway, so a future render site keyed on `node["type"] == "file"` would
    be caught rather than silently un-covering symbols.
    """

    def test_query_summary_fallback_has_no_raw_escape(self):
        exit_code, stdout, _stderr = self._wd("query", "evilsymbol")
        self.assertEqual(exit_code, 0)
        self.assertIn("summary:", stdout)
        self.assertNotIn("\x1b", stdout)
        self.assertIn("\\x1b[2J", stdout)

    def test_context_summary_fallback_has_no_raw_escape(self):
        _exit_code, stdout, _stderr = self._wd("context", self.symbol_summary_node_id)
        self.assertIn("summary:", stdout)
        self.assertNotIn("\x1b", stdout)
        self.assertIn("\\x1b[2J", stdout)


class JsonSurfaceKeepsItsValuesTest(_HostileGraphCase):
    """``--json`` still hands back the raw id; only the encoding changed.

    The full ``--json`` contract (DEL/C1, CLI==MCP byte parity) is pinned in
    :mod:`weld.tests.weld_json_terminal_safety_test`. What belongs *here* is
    the invariant the text-side escape must never bleed into: the JSON
    surface is not sanitized, it is encoded, so a machine consumer's value is
    exactly what the graph holds.
    """

    def test_json_output_is_valid_and_round_trips_the_raw_id(self):
        exit_code, stdout, _stderr = self._wd("query", "evil", "--json")
        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout)
        ids = [m.get("id") for m in payload.get("matches") or []]
        self.assertIn(self.node_id, ids, "json must carry the raw id verbatim")

    def test_json_stream_never_carries_a_literal_escape_byte(self):
        # Not sanitization -- json.dumps escapes all of C0 by construction.
        _exit_code, stdout, _stderr = self._wd("query", "evil", "--json")
        self.assertNotIn("\x1b", stdout)
        self.assertIn("\\u001b", stdout)

    def test_text_escape_does_not_leak_into_json(self):
        # The text surface renders \x1b[2J; the JSON surface must not, or the
        # value a consumer parses would silently differ from the graph's.
        _exit_code, stdout, _stderr = self._wd("query", "evil", "--json")
        self.assertNotIn("\\x1b", stdout)


class SweptRenderSiteTest(_HostileGraphCase):
    """The escape is applied at every human-text write boundary, not just
    the retrieval surface.

    ``wd lint`` is the representative: it is a wholly separate CLI module
    with its own formatter, and it names the offending node id in each
    violation line -- so it proves the contract reaches beyond ``_emit``.
    """

    def test_lint_text_output_has_no_raw_escape(self):
        from weld.arch_lint import main as lint_main

        # A lone node with no edges trips orphan-detection, which renders
        # the node id straight into the violation line.
        _exit_code, stdout, _stderr = _run_and_capture(
            lint_main, ["--root", self._tmp],
        )
        self.assertIn("orphan-detection", stdout)
        self.assertNotIn("\x1b", stdout)
        self.assertIn("\\x1b[2J", stdout)


class StructuredErrorLineTest(unittest.TestCase):
    """``format_error_line`` is the stderr half of the same boundary."""

    def test_error_line_escapes_control_chars_in_detail(self):
        line = _errors.format_error_line(
            _errors.NODE_NOT_FOUND, f"symbol:py:evil{CLEAR_SCREEN}: missing",
        )
        self.assertNotIn("\x1b", line)
        self.assertIn("\\x1b[2J", line)

    def test_error_line_is_still_one_line(self):
        # A CR or LF smuggled through the detail must not split the
        # single-line contract that agents parse.
        line = _errors.format_error_line(
            _errors.NODE_NOT_FOUND, "bad\r\nerror[graph_missing]: fake",
        )
        self.assertEqual(len(line.splitlines()), 1, f"multi-line: {line!r}")

    def test_clean_error_line_is_unchanged(self):
        line = _errors.format_error_line(_errors.GRAPH_CORRUPT, "byte 38")
        self.assertIn("byte 38", line)
        self.assertIn(_errors.GRAPH_CORRUPT, line)


if __name__ == "__main__":
    unittest.main()
