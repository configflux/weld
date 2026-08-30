"""Unit tests for ``weld._errors`` -- the shared structured-error layer.

ADR 0035 (telemetry error categories) and ADR 0025 (trust posture) require
that failure surfaces never leak raw file contents, secrets, or environment
values. This module is the single source of the CLI/MCP error vocabulary
(``graph_missing`` / ``graph_corrupt`` / ``schema_mismatch`` /
``node_not_found``) so both surfaces emit identical ``error_code`` + ``hint``.

These tests pin: (1) every code has a stable hint; (2) the structured payload
shape matches the legacy ``_mcp_guard`` shape so MCP clients keep parsing
``error`` / ``error_code`` / ``hint``; (3) the corrupt-graph classifier maps
a ``JSONDecodeError`` to ``graph_corrupt`` and a ``SchemaVersionError`` to
``schema_mismatch``; (4) the corrupt-graph message is *safe* -- it never
echoes the raw bytes that failed to parse (only a byte offset / line+col).
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path


from weld import _errors  # noqa: E402
from weld._graph_schema import SchemaVersionError  # noqa: E402


class ErrorVocabularyTest(unittest.TestCase):
    """Every code resolves to a non-empty, stable hint."""

    def test_all_codes_have_hints(self) -> None:
        for code in (
            _errors.GRAPH_MISSING,
            _errors.GRAPH_CORRUPT,
            _errors.SCHEMA_MISMATCH,
            _errors.NODE_NOT_FOUND,
            _errors.RESULT_UNKNOWN,
        ):
            self.assertIn(code, _errors.ERROR_HINTS)
            self.assertTrue(_errors.ERROR_HINTS[code].strip())

    def test_result_unknown_hint_points_at_cross_repo_strategies(self) -> None:
        # ADR 0134 Finding 06: the cannot-answer hint for impact must name the
        # remediation surface an operator edits.
        hint = _errors.ERROR_HINTS[_errors.RESULT_UNKNOWN]
        self.assertIn("cross_repo_strategies", hint)

    def test_missing_graph_hint_mentions_init_and_discover(self) -> None:
        hint = _errors.ERROR_HINTS[_errors.GRAPH_MISSING]
        self.assertIn("wd init", hint)
        self.assertIn("wd discover", hint)

    def test_corrupt_hint_points_at_rediscover(self) -> None:
        self.assertIn("wd discover", _errors.ERROR_HINTS[_errors.GRAPH_CORRUPT])

    def test_schema_hint_points_at_upgrade(self) -> None:
        self.assertIn("upgrade", _errors.ERROR_HINTS[_errors.SCHEMA_MISMATCH].lower())


class StructuredPayloadTest(unittest.TestCase):
    """The MCP payload shape stays compatible with the legacy guard."""

    def test_payload_carries_code_hint_and_error(self) -> None:
        payload = _errors.structured_payload(_errors.GRAPH_CORRUPT)
        self.assertEqual(payload["error_code"], _errors.GRAPH_CORRUPT)
        self.assertEqual(payload["hint"], _errors.ERROR_HINTS[_errors.GRAPH_CORRUPT])
        self.assertIn("error", payload)
        self.assertTrue(payload["error"].strip())

    def test_payload_optional_retry_field(self) -> None:
        payload = _errors.structured_payload(
            _errors.GRAPH_MISSING, retry_cmd="weld_query"
        )
        self.assertIn("retry", payload)
        self.assertIn("weld_query", payload["retry"])

    def test_payload_detail_overrides_default_error_message(self) -> None:
        payload = _errors.structured_payload(
            _errors.GRAPH_CORRUPT, detail="Expecting ':' delimiter at byte 38"
        )
        self.assertIn("byte 38", payload["error"])
        # Even with detail, the hint stays the stable vocabulary hint.
        self.assertEqual(payload["hint"], _errors.ERROR_HINTS[_errors.GRAPH_CORRUPT])

    def test_payload_is_json_serializable(self) -> None:
        # MCP serializes payloads via json.dumps; ensure no exotic types.
        json.dumps(_errors.structured_payload(_errors.SCHEMA_MISMATCH))


class ClassifyGraphLoadErrorTest(unittest.TestCase):
    """Map a load exception to its (code, safe-message) pair."""

    def _make_decode_error(self, raw: str) -> json.JSONDecodeError:
        try:
            json.loads(raw)
        except json.JSONDecodeError as exc:  # noqa: PERF203 - test helper
            return exc
        raise AssertionError("expected JSONDecodeError")

    def test_json_decode_error_maps_to_graph_corrupt(self) -> None:
        secret_blob = '{"meta": {"token": "SUPER-SECRET-VALUE"'  # truncated
        exc = self._make_decode_error(secret_blob)
        code, message = _errors.classify_graph_load_error(
            exc, Path("/repo/.weld/graph.json")
        )
        self.assertEqual(code, _errors.GRAPH_CORRUPT)
        # SAFETY: the raw bytes (and any secret inside them) must NOT appear.
        self.assertNotIn("SUPER-SECRET-VALUE", message)
        self.assertNotIn(secret_blob, message)
        # A byte offset is allowed (and useful) -- it is not file content.
        self.assertTrue(
            any(tok in message.lower() for tok in ("byte", "line", "column", "char")),
            f"corrupt message should localize the failure: {message!r}",
        )

    def test_schema_version_error_maps_to_schema_mismatch(self) -> None:
        secret_path = "/home/alice/work/.weld/graph.json"
        exc = SchemaVersionError(
            f"graph.json at {secret_path} has schema_version 9; "
            "this build supports up to 2. Please upgrade weld."
        )
        code, message = _errors.classify_graph_load_error(
            exc, Path(secret_path)
        )
        self.assertEqual(code, _errors.SCHEMA_MISMATCH)
        # The structural signal (which version, upgrade) is preserved...
        self.assertIn("schema_version", message)
        # ...but the absolute filesystem path is stripped (ADR 0025): an MCP
        # client must not learn the home directory / username from the path.
        self.assertNotIn(secret_path, message)
        self.assertNotIn("/home/alice", message)
        self.assertIn("graph.json", message)

    def test_unknown_exception_is_not_classified(self) -> None:
        code, _message = _errors.classify_graph_load_error(
            ValueError("boom"), Path("/repo/.weld/graph.json")
        )
        self.assertIsNone(code)

    def test_graph_shape_error_maps_to_graph_corrupt(self) -> None:
        # bd 5038-1c7o: a syntactically valid graph.json missing (or with
        # the wrong type for) "nodes"/"edges" used to reach
        # Graph._build_inverted_index and raise an uncaught KeyError
        # instead of classifying like every other malformed-graph case.
        from weld._graph_schema import GraphShapeError

        exc = GraphShapeError(
            "graph payload 'nodes' must be an object, got NoneType"
        )
        code, message = _errors.classify_graph_load_error(
            exc, Path("/repo/.weld/graph.json")
        )
        self.assertEqual(code, _errors.GRAPH_CORRUPT)
        self.assertIn("nodes", message)
        # GraphShapeError IS a ValueError -- confirms it stays caught by
        # every existing `except (..., ValueError)` tuple with no changes
        # to those tuples, while still being classified more precisely than
        # a blanket ValueError. The sibling test right below this one pins
        # that a bare ValueError stays deliberately unclassified.
        self.assertIsInstance(exc, ValueError)

    def test_plain_value_error_is_still_not_classified(self) -> None:
        # Guards against a regression that widens the GraphShapeError check
        # into a blanket `isinstance(exc, ValueError)` -- that would
        # mislabel an unrelated ValueError raised anywhere during a graph
        # load as a corrupt graph instead of re-raising it.
        code, _message = _errors.classify_graph_load_error(
            ValueError("unrelated: not a graph-shape problem"),
            Path("/repo/.weld/graph.json"),
        )
        self.assertIsNone(code)

    def test_is_a_directory_error_maps_to_graph_corrupt(self) -> None:
        # ``Graph.load`` gates on ``.exists()`` (true for a directory too),
        # so a directory left at ``.weld/graph.json`` raises this from the
        # ``path.read_text()`` call rather than from a JSON parse -- but the
        # remedy is identical, so it is classified the same way (bd 9yc8).
        secret_path = "/home/alice/work/.weld/graph.json"
        exc = IsADirectoryError(21, "Is a directory", secret_path)
        code, message = _errors.classify_graph_load_error(
            exc, Path(secret_path)
        )
        self.assertEqual(code, _errors.GRAPH_CORRUPT)
        # SAFETY: the message is a fixed, path-free string -- stronger than
        # the JSON-decode case, which only echoes safe positional metadata.
        self.assertNotIn(secret_path, message)
        self.assertNotIn("/home/alice", message)
        self.assertTrue(message.strip())


class FormatErrorLineTest(unittest.TestCase):
    """The CLI one-line shape carries the code and the hint on one line."""

    def test_single_line_contains_code_and_hint(self) -> None:
        line = _errors.format_error_line(
            _errors.GRAPH_CORRUPT, "Expecting ':' delimiter"
        )
        self.assertNotIn("\n", line.rstrip("\n"))
        self.assertIn(_errors.GRAPH_CORRUPT, line)
        self.assertIn(_errors.ERROR_HINTS[_errors.GRAPH_CORRUPT], line)

    def test_line_includes_detail_when_present(self) -> None:
        line = _errors.format_error_line(_errors.GRAPH_CORRUPT, "byte 38")
        self.assertIn("byte 38", line)


if __name__ == "__main__":
    unittest.main()
