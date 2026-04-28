"""Unit tests for :mod:`weld._telemetry_redact`.

Validates the defensive redactor that gates every event before write per
ADR 0035 § "Strict allowlist event schema (v1)" / "Never recorded".

The redactor's contract is simple but load-bearing: any string field that
looks remotely like a path, email, query, exception message, or other
free-form user data MUST be rejected by ``is_safe_string`` and the
enclosing event MUST be dropped by ``validate_event``. Tests are split
into a positive set (every legitimate enum value the writer can emit
passes) and a negative table (every prohibited shape is rejected).
"""

from __future__ import annotations

import unittest

from weld import _telemetry_allowlist as allowlist
from weld import _telemetry_redact as redact


def _ok_event(**overrides: object) -> dict:
    """Return a minimal event that should pass ``validate_event``."""
    base = {
        "schema_version": 1,
        "ts": "2026-04-28T14:03:11Z",
        "weld_version": "0.10.5",
        "surface": "cli",
        "command": "discover",
        "outcome": "ok",
        "exit_code": 0,
        "duration_ms": 482,
        "error_kind": None,
        "python_version": "3.12.3",
        "platform": "linux",
        "flags": ["--json", "--root"],
    }
    base.update(overrides)
    return base


class IsSafeStringPositiveTests(unittest.TestCase):
    def test_simple_lower_word_ok(self) -> None:
        self.assertTrue(redact.is_safe_string("discover"))

    def test_short_dotted_version_ok(self) -> None:
        self.assertTrue(redact.is_safe_string("3.12.3"))

    def test_iso_timestamp_passes_via_validate_event(self) -> None:
        # ISO timestamps contain ':' which is in the disallowed punctuation
        # set for enum-shaped strings. They are validated by validate_event
        # via a dedicated regex, not by is_safe_string. The full event with
        # a valid timestamp must still pass.
        self.assertIsNotNone(
            redact.validate_event(_ok_event(ts="2026-04-28T14:03:11Z"))
        )

    def test_exception_class_name_ok(self) -> None:
        self.assertTrue(redact.is_safe_string("ValueError"))
        self.assertTrue(redact.is_safe_string("KeyError"))
        self.assertTrue(redact.is_safe_string("BrokenPipeError"))


class IsSafeStringNegativeTests(unittest.TestCase):
    """Table-driven rejection cases."""

    BAD_STRINGS = [
        # Forward slashes: paths, URLs, search queries.
        "/abs/path/file.py",
        "relative/dir/file.py",
        "https://example.com/foo",
        "weld/cli.py",
        "search/with/slashes",
        # Backslashes: Windows paths.
        r"C:\Users\foo",
        r"weld\cli.py",
        # @ -- emails, scoped npm packages, decorators.
        "user@example.com",
        "@scoped/pkg",
        # .. and ~ -- relative path indicators.
        "../relative",
        "~/home",
        "/home/user/..",
        # Drive-letter prefix without backslash.
        "C:foo",
        "z:bar",
        # Long whitespace runs.
        "foo    bar",
        "x" + "    " + "y",
        # Long digit runs.
        "abc12345",
        "id99999",
        "12345",
        # Long strings (> 96 chars).
        "x" * 97,
        "a" * 200,
        # Email-like patterns.
        "name+tag@host.tld",
        # Embedded control characters.
        "foo\x07bar",
        "a\x00b",
        # JSON-ish blobs leak.
        '{"key":"value"}',
        # Things that look like exception messages.
        "Could not open file at /tmp/x",
    ]

    def test_each_bad_string_rejected(self) -> None:
        for bad in self.BAD_STRINGS:
            with self.subTest(bad=bad):
                self.assertFalse(
                    redact.is_safe_string(bad),
                    f"redactor accepted prohibited string: {bad!r}",
                )


class ValidateEventPositiveTests(unittest.TestCase):
    def test_minimal_ok_event_passes(self) -> None:
        event = _ok_event()
        self.assertIsNotNone(redact.validate_event(event))

    def test_every_cli_command_in_allowlist_passes(self) -> None:
        for cmd in allowlist.CLI_COMMANDS:
            with self.subTest(cmd=cmd):
                event = _ok_event(command=cmd)
                self.assertIsNotNone(redact.validate_event(event))

    def test_every_mcp_tool_in_allowlist_passes(self) -> None:
        for tool in allowlist.MCP_TOOLS:
            with self.subTest(tool=tool):
                event = _ok_event(surface="mcp", command=tool, exit_code=-1)
                self.assertIsNotNone(redact.validate_event(event))

    def test_every_allowlisted_flag_passes(self) -> None:
        for flag in allowlist.CLI_FLAGS:
            with self.subTest(flag=flag):
                event = _ok_event(flags=[flag])
                self.assertIsNotNone(redact.validate_event(event))

    def test_legitimate_error_kind_passes(self) -> None:
        for cls_name in ("ValueError", "KeyError", "RuntimeError",
                         "BrokenPipeError", "KeyboardInterrupt", "OSError"):
            with self.subTest(cls=cls_name):
                event = _ok_event(outcome="error", exit_code=1,
                                  error_kind=cls_name)
                self.assertIsNotNone(redact.validate_event(event))

    def test_numeric_fields_with_long_digit_values_pass(self) -> None:
        # Numeric allowlist exempts schema_version, exit_code, duration_ms.
        event = _ok_event(duration_ms=12345678)
        self.assertIsNotNone(redact.validate_event(event))

    def test_outcome_enum_values_pass(self) -> None:
        for outcome in ("ok", "error", "interrupted"):
            with self.subTest(outcome=outcome):
                event = _ok_event(outcome=outcome)
                self.assertIsNotNone(redact.validate_event(event))


class ValidateEventNegativeTests(unittest.TestCase):
    def test_path_in_error_kind_rejected(self) -> None:
        event = _ok_event(outcome="error", exit_code=1,
                          error_kind="/path/to/error")
        self.assertIsNone(redact.validate_event(event))

    def test_long_error_kind_rejected(self) -> None:
        event = _ok_event(outcome="error", exit_code=1,
                          error_kind="A" * 65)
        self.assertIsNone(redact.validate_event(event))

    def test_email_in_command_rejected(self) -> None:
        event = _ok_event(command="user@example.com")
        self.assertIsNone(redact.validate_event(event))

    def test_path_in_flags_rejected(self) -> None:
        event = _ok_event(flags=["/abs/path"])
        self.assertIsNone(redact.validate_event(event))

    def test_unknown_outcome_rejected(self) -> None:
        event = _ok_event(outcome="banana")
        self.assertIsNone(redact.validate_event(event))

    def test_unknown_surface_rejected(self) -> None:
        event = _ok_event(surface="webhook")
        self.assertIsNone(redact.validate_event(event))

    def test_missing_required_field_rejected(self) -> None:
        event = _ok_event()
        del event["weld_version"]
        self.assertIsNone(redact.validate_event(event))

    def test_wrong_schema_version_rejected(self) -> None:
        event = _ok_event(schema_version=99)
        self.assertIsNone(redact.validate_event(event))

    def test_non_int_exit_code_rejected(self) -> None:
        event = _ok_event(exit_code="zero")
        self.assertIsNone(redact.validate_event(event))

    def test_non_int_duration_rejected(self) -> None:
        event = _ok_event(duration_ms=4.2)
        self.assertIsNone(redact.validate_event(event))

    def test_negative_duration_rejected(self) -> None:
        event = _ok_event(duration_ms=-1)
        self.assertIsNone(redact.validate_event(event))

    def test_python_version_with_extra_text_rejected(self) -> None:
        event = _ok_event(python_version="3.12.3 (CPython, Linux build)")
        self.assertIsNone(redact.validate_event(event))

    def test_platform_with_hostname_rejected(self) -> None:
        event = _ok_event(platform="Linux-6.6.87-host123-x86_64")
        self.assertIsNone(redact.validate_event(event))

    def test_flags_field_must_be_list(self) -> None:
        event = _ok_event(flags="--json")
        self.assertIsNone(redact.validate_event(event))

    def test_error_kind_pattern_strict(self) -> None:
        # Spaces, punctuation, dashes -- all rejected by strict pattern.
        for bad in ("Value Error", "value-error", "Value.Error", "9Bad",
                    "X" * 65, ""):
            with self.subTest(bad=bad):
                event = _ok_event(outcome="error", exit_code=1,
                                  error_kind=bad)
                self.assertIsNone(redact.validate_event(event))


class EndToEndRedactionTests(unittest.TestCase):
    def test_leaky_event_with_path_in_error_kind_dropped(self) -> None:
        leaky = _ok_event(
            outcome="error",
            exit_code=2,
            error_kind="/private/path/secrets.json",
        )
        self.assertIsNone(redact.validate_event(leaky))

    def test_leaky_event_with_email_anywhere_dropped(self) -> None:
        leaky = _ok_event(weld_version="user@example.com")
        self.assertIsNone(redact.validate_event(leaky))

    def test_round_trip_preserves_safe_event(self) -> None:
        # An untouched ok event passes through unchanged.
        event = _ok_event()
        out = redact.validate_event(event)
        self.assertEqual(out, event)


if __name__ == "__main__":
    unittest.main()
