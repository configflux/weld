"""Unit tests for the operational-notice sink (bd gcrf).

The one invariant: every ``[weld] ...`` operational line goes to **stderr**,
never stdout, so a ``--json`` payload on stdout stays a clean parse. These
tests pin the sink itself plus the read-path emitters that route through it.
"""

from __future__ import annotations

import io
import sys
import unittest

from weld._notice import emit


class NoticeEmitTest(unittest.TestCase):
    def test_writes_to_injected_stream_with_prefix_and_newline(self) -> None:
        buf = io.StringIO()
        emit("hello world", stream=buf)
        self.assertEqual(buf.getvalue(), "[weld] hello world\n")

    def test_keeps_existing_prefix(self) -> None:
        buf = io.StringIO()
        emit("[weld] auto-refresh: 1 file(s) changed", stream=buf)
        self.assertEqual(buf.getvalue(), "[weld] auto-refresh: 1 file(s) changed\n")

    def test_default_stream_is_stderr_never_stdout(self) -> None:
        old_out, old_err = sys.stdout, sys.stderr
        out, err = io.StringIO(), io.StringIO()
        sys.stdout, sys.stderr = out, err
        try:
            emit("no files changed, graph is up to date")
        finally:
            sys.stdout, sys.stderr = old_out, old_err
        self.assertEqual(out.getvalue(), "")
        self.assertIn("[weld] no files changed", err.getvalue())


class ReadPathEmittersStderrTest(unittest.TestCase):
    """The auto-refresh emitters must never touch stdout."""

    def test_no_refresh_warning_goes_to_injected_stderr(self) -> None:
        from weld._auto_refresh import _emit_no_refresh_warning

        buf = io.StringIO()
        _emit_no_refresh_warning(buf)
        self.assertIn("[weld] warning: graph is stale", buf.getvalue())

    def test_banner_goes_to_injected_stderr(self) -> None:
        from weld._auto_refresh import _emit_banner

        buf = io.StringIO()
        _emit_banner(
            stderr=buf,
            files_changed=2,
            elapsed_ms=5,
            incremental=True,
            json_output=False,
            safe=False,
        )
        self.assertIn("[weld] auto-refresh: 2 file(s) changed", buf.getvalue())

    def test_banner_suppressed_under_json(self) -> None:
        from weld._auto_refresh import _emit_banner

        buf = io.StringIO()
        _emit_banner(
            stderr=buf,
            files_changed=2,
            elapsed_ms=5,
            incremental=True,
            json_output=True,
            safe=False,
        )
        self.assertEqual(buf.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
