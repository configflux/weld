"""``--json`` terminal safety: DEL/C1 escaped, values and parity preserved.

``json.dumps`` escapes every C0 character by construction, which is why the
first pass at terminal safety left ``--json`` alone. That premise is only
two-thirds true: under ``ensure_ascii=False`` the encoder passes **DEL**
(``\\x7f``) and the whole **C1** range (``\\x80``-``\\x9f``) through verbatim,
and ``U+009B`` *is* CSI -- a one-byte control-sequence introducer that a UTF-8
terminal acts on with no ESC involved. So ``wd query --json | less`` was a live
injection channel even though the C0 argument was sound.

The fix is deliberately an *encoding* change, not a sanitization:
:func:`weld._safe_text.dumps_safe_json` re-spells those code points as their
JSON ``\\uXXXX`` escapes, which every conformant parser decodes back to the
identical string. That is what makes it safe to move both surfaces at once --
the CLI ``--json`` writers and the MCP serializer share the emitter, so the
ADR 0083 byte-identity parity contract is preserved rather than traded away.

This suite pins all three halves of that claim:

* the bytes no longer carry a raw DEL/C1 (the security property),
* the parsed value is unchanged (the machine-consumer property),
* CLI and MCP produce the same bytes for the same hostile payload (parity).
"""

from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout

from weld._graph_cli import main as graph_cli_main
from weld._mcp_guard import serialize_dispatch
from weld._safe_text import dumps_safe_json, sanitize_json_text

#: CSI as a single byte. No ESC, no bracket -- a UTF-8 terminal decodes
#: U+009B and starts consuming a control sequence.
CSI = "\x9b"
DEL = "\x7f"
#: RIGHT-TO-LEFT OVERRIDE, spelled by code point so this source file does not
#: reorder its own display.
RLO = chr(0x202E)

#: A node id a hostile repository can mint from a filename.
HOSTILE = f"symbol:py:evil{CSI}2Kpwned{DEL}:handler"


def _has_raw_control(text: str) -> bool:
    return any(0x7F <= ord(ch) <= 0x9F for ch in text)


class SafeJsonEmitterTest(unittest.TestCase):
    """Unit contract for the emitter every ``--json`` writer shares."""

    def test_del_and_c1_are_escaped(self):
        out = dumps_safe_json({"id": HOSTILE})
        self.assertFalse(_has_raw_control(out), repr(out))
        self.assertIn("\\u009b", out)
        self.assertIn("\\u007f", out)

    def test_value_round_trips_exactly(self):
        payload = {"id": HOSTILE, "nested": [{"k": f"a{CSI}b"}]}
        self.assertEqual(json.loads(dumps_safe_json(payload)), payload)

    def test_every_c1_codepoint_is_covered(self):
        raw = "".join(chr(c) for c in range(0x7F, 0xA0))
        out = dumps_safe_json({"v": raw})
        self.assertFalse(_has_raw_control(out), repr(out))
        self.assertEqual(json.loads(out)["v"], raw)

    def test_printable_non_ascii_stays_readable(self):
        # ensure_ascii=True would have "fixed" this too, by mangling every
        # accent, CJK glyph and emoji in the payload. That is why it is not
        # the tool used here.
        payload = {"v": "café 中文 \U0001f600"}
        out = dumps_safe_json(payload)
        self.assertIn("café 中文", out)
        self.assertEqual(json.loads(out), payload)

    def test_clean_payload_is_byte_identical_to_plain_dumps(self):
        payload = {"b": 1, "a": [1, 2, {"c": "x"}]}
        for kwargs in ({}, {"indent": 2}, {"indent": 2, "sort_keys": True}):
            with self.subTest(kwargs=kwargs):
                self.assertEqual(
                    dumps_safe_json(payload, **kwargs),
                    json.dumps(payload, ensure_ascii=False, **kwargs),
                )

    def test_ensure_ascii_true_is_honored_for_ascii_only_callers(self):
        # Surfaces that always emitted pure ASCII keep their exact bytes;
        # the escape pass is then a no-op rather than a behavior change.
        payload = {"v": f"café{CSI}"}
        self.assertEqual(
            dumps_safe_json(payload, ensure_ascii=True),
            json.dumps(payload, ensure_ascii=True),
        )

    def test_structural_json_whitespace_is_untouched(self):
        # The escape runs over the whole document, so it must not disturb the
        # grammar outside string literals.
        out = dumps_safe_json({"a": [1, 2]}, indent=2)
        self.assertEqual(json.loads(out), {"a": [1, 2]})
        self.assertIn("\n", out)

    def test_sanitize_json_text_is_identity_on_clean_text(self):
        clean = '{"a": 1}'
        self.assertIs(sanitize_json_text(clean), clean)

    def test_bidi_overrides_are_escaped_here_too(self):
        # `wd list` and `wd dump` print JSON to a terminal with no flag, so
        # an operator reads this by eye; a reordered filename deceives here
        # exactly as it does in a rendered block.
        payload = {"id": f"file:src/{RLO}gpj.exe"}
        out = dumps_safe_json(payload)
        self.assertNotIn(RLO, out)
        self.assertIn("\\u202e", out)
        self.assertEqual(json.loads(out), payload)

    def test_directional_marks_are_kept_in_json(self):
        payload = {"v": f"a{chr(0x200F)}b"}
        out = dumps_safe_json(payload)
        self.assertIn(chr(0x200F), out)
        self.assertEqual(json.loads(out), payload)

    def test_serialized_text_escape_matches_the_object_emitter(self):
        payload = {"id": f"x{CSI}{RLO}y"}
        self.assertEqual(
            sanitize_json_text(json.dumps(payload, ensure_ascii=False)),
            dumps_safe_json(payload),
        )


class _HostileGraphCase(unittest.TestCase):
    """A graph whose node id carries a one-byte CSI and a DEL."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        weld_dir = os.path.join(self._tmp, ".weld")
        os.makedirs(weld_dir)
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        graph = {
            "meta": {"version": 1},
            "nodes": {
                HOSTILE: {
                    "type": "symbol",
                    "label": f"handler{CSI}",
                    "props": {"description": f"does {CSI}2J things"},
                },
            },
            "edges": [],
        }
        with open(
            os.path.join(weld_dir, "graph.json"), "w", encoding="utf-8",
        ) as fh:
            json.dump(graph, fh)
        self._prev_refresh = os.environ.get("WELD_AUTO_REFRESH")
        os.environ["WELD_AUTO_REFRESH"] = "0"
        self.addCleanup(self._restore_refresh)

    def _restore_refresh(self):
        if self._prev_refresh is None:
            os.environ.pop("WELD_AUTO_REFRESH", None)
        else:
            os.environ["WELD_AUTO_REFRESH"] = self._prev_refresh

    def _wd_json(self, *argv):
        """Run one ``wd`` read command with ``--json`` and return stdout.

        Refresh is frozen via ``WELD_AUTO_REFRESH=0`` rather than
        ``--no-refresh``: not every read subcommand carries that flag, and an
        argparse error would leave stdout empty -- which every "no raw control
        byte" assertion would then pass vacuously. The non-empty check below
        is the guard against exactly that.
        """
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                graph_cli_main(["--root", self._tmp, *argv, "--json"])
        except SystemExit:
            pass
        out = buf.getvalue()
        self.assertTrue(out.strip(), f"no output from wd {' '.join(argv)}")
        return out


class JsonCommandSurfaceTest(_HostileGraphCase):
    """No ``--json`` read command emits a raw DEL/C1 from the graph."""

    def test_query_json_has_no_raw_control_byte(self):
        out = self._wd_json("query", "evil")
        self.assertFalse(_has_raw_control(out), repr(out[:400]))
        self.assertIn("\\u009b", out)

    def test_query_json_still_parses_to_the_raw_id(self):
        payload = json.loads(self._wd_json("query", "evil"))
        ids = [m.get("id") for m in payload.get("matches") or []]
        self.assertIn(HOSTILE, ids, "the parsed value must be the true id")

    def test_context_json_has_no_raw_control_byte(self):
        out = self._wd_json("context", HOSTILE)
        self.assertFalse(_has_raw_control(out), repr(out[:400]))

    def test_stats_json_has_no_raw_control_byte(self):
        self.assertFalse(_has_raw_control(self._wd_json("stats")))


class CliMcpParityTest(_HostileGraphCase):
    """The escape moved both surfaces together, as the contract requires.

    A fix applied to only one of them would have been worse than no fix: the
    ADR 0083 thin-wrapper invariant says an agent must get the same answer
    from ``wd ... --json`` and the MCP tool, and a divergence in escaping is
    a divergence in bytes.
    """

    def _mcp_json(self, tool, arguments):
        return serialize_dispatch(
            self._dispatch, tool, arguments, root=self._tmp,
        )

    @staticmethod
    def _dispatch(tool_name, arguments, root):
        from weld import mcp_server

        if tool_name == "weld_query":
            return mcp_server.weld_query(root=root, **(arguments or {}))
        raise KeyError(tool_name)

    def test_mcp_serializer_escapes_the_same_classes(self):
        out = self._mcp_json("weld_query", {"term": "evil"})
        self.assertFalse(_has_raw_control(out), repr(out[:400]))
        self.assertIn("\\u009b", out)

    def test_mcp_value_round_trips_to_the_raw_id(self):
        payload = json.loads(self._mcp_json("weld_query", {"term": "evil"}))
        ids = [m.get("id") for m in payload.get("matches") or []]
        self.assertIn(HOSTILE, ids)

    def test_both_surfaces_encode_the_hostile_id_identically(self):
        cli = json.loads(self._wd_json("query", "evil"))
        mcp = json.loads(self._mcp_json("weld_query", {"term": "evil"}))
        cli_ids = sorted(m.get("id") for m in cli.get("matches") or [])
        mcp_ids = sorted(m.get("id") for m in mcp.get("matches") or [])
        self.assertEqual(cli_ids, mcp_ids)
        # And the encoded form is the same on both, not merely the value.
        self.assertEqual(
            dumps_safe_json(cli_ids), dumps_safe_json(mcp_ids),
        )

    def test_serializer_error_payloads_are_escaped_too(self):
        # The last-resort transport guard stringifies an exception, which can
        # quote a caller-supplied id.
        def boom(_tool, _args, root):
            raise KeyError(f"unknown tool {CSI}2K")

        out = serialize_dispatch(boom, "nope", None, root=self._tmp)
        self.assertFalse(_has_raw_control(out), repr(out))


if __name__ == "__main__":
    unittest.main()
