"""Unit tests for telemetry wrapping of :func:`weld.mcp_server.dispatch`.

Per ADR 0035 § "Failure-isolated writer" and § "Strict allowlist event
schema (v1)", every MCP tool dispatch must:

- Record exactly one event with ``surface="mcp"``, ``command=<tool>``,
  ``exit_code=-1`` (the MCP sentinel), ``error_kind=None`` on success.
- Record exactly one event with ``outcome="error"``,
  ``error_kind=<exception class>`` on failure, AND propagate the original
  exception unchanged.
- Produce one event per call within a long-lived process (stdio loop).
- Survive a writer that raises -- the dispatch return value and any raised
  exception must be unchanged when telemetry's writer breaks.
- Become a no-op when telemetry is opted out (``WELD_TELEMETRY=off``).

These cover T4 of the local-telemetry epic.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld import _telemetry as tel  # noqa: E402
from weld import mcp_server  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_repo_with_graph(root: Path) -> Path:
    """Seed a minimal ``.weld/`` so the missing-graph guard passes and
    :func:`weld._telemetry.resolve_path` resolves to ``<root>/.weld/``.

    Graph shape mirrors ``weld_mcp_server_test.py``: nodes is a dict,
    edges is a list. A single ``entity:Foo`` node lets ``weld_query``
    return a real envelope without needing a full discovery run.
    """
    from weld.contract import SCHEMA_VERSION

    weld_dir = root / ".weld"
    weld_dir.mkdir(parents=True, exist_ok=True)
    (weld_dir / "discover.yaml").write_text("# fixture\n", encoding="utf-8")
    graph = {
        "meta": {
            "version": SCHEMA_VERSION,
            "git_sha": "deadbeef",
            "updated_at": "2026-04-28T00:00:00+00:00",
        },
        "nodes": {
            "entity:Foo": {
                "type": "entity",
                "label": "Foo",
                "props": {
                    "file": "x.py",
                    "exports": ["Foo"],
                    "description": "test fixture entity",
                },
            },
        },
        "edges": [],
    }
    (weld_dir / "graph.json").write_text(
        json.dumps(graph), encoding="utf-8",
    )
    return root


def _read_events(root: Path) -> list[dict]:
    path = root / ".weld" / tel.TELEMETRY_FILENAME
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text().splitlines() if line.strip()
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class _MCPTestBase(unittest.TestCase):
    """Shared setup that pins WELD_TELEMETRY out of the host environment.

    We delete the env var so the default-on tier in ``is_enabled`` decides,
    independent of however the developer's shell happens to be configured.
    """

    def setUp(self) -> None:  # noqa: D401 - unittest hook
        self._saved_env = os.environ.pop("WELD_TELEMETRY", None)

    def tearDown(self) -> None:  # noqa: D401 - unittest hook
        if self._saved_env is not None:
            os.environ["WELD_TELEMETRY"] = self._saved_env
        else:
            os.environ.pop("WELD_TELEMETRY", None)


class SuccessfulDispatchRecordsOkEventTests(_MCPTestBase):
    def test_records_one_ok_event_with_mcp_sentinel_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo_with_graph(Path(tmp))

            result = mcp_server.dispatch("weld_query", {"term": "Foo"}, root=root)

            # Sanity: dispatch returned something dict-shaped from the inner
            # tool handler.
            self.assertIsInstance(result, dict)

            events = _read_events(root)
            self.assertEqual(len(events), 1)
            ev = events[0]
            self.assertEqual(ev["surface"], "mcp")
            self.assertEqual(ev["command"], "weld_query")
            self.assertEqual(ev["outcome"], "ok")
            self.assertEqual(ev["exit_code"], -1)  # ADR 0035 MCP sentinel.
            self.assertIsNone(ev["error_kind"])
            self.assertEqual(ev["flags"], [])


class UnknownToolRecordsErrorEventAndReraisesTests(_MCPTestBase):
    def test_unknown_tool_raises_and_records_error_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo_with_graph(Path(tmp))

            with self.assertRaises(KeyError) as ctx:
                mcp_server.dispatch(
                    "weld_does_not_exist", {}, root=root,
                )

            # Original exception propagated unchanged.
            self.assertIn("weld_does_not_exist", str(ctx.exception))

            events = _read_events(root)
            self.assertEqual(len(events), 1)
            ev = events[0]
            self.assertEqual(ev["surface"], "mcp")
            self.assertEqual(ev["outcome"], "error")
            self.assertEqual(ev["error_kind"], "KeyError")
            self.assertEqual(ev["exit_code"], -1)
            # Unknown tool name MUST be coerced to "unknown" by the
            # allowlist -- the on-disk artifact never leaks user input.
            self.assertEqual(ev["command"], "unknown")


class MultipleConsecutiveCallsProduceOneEventEachTests(_MCPTestBase):
    def test_three_consecutive_dispatches_yield_three_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo_with_graph(Path(tmp))

            mcp_server.dispatch("weld_query", {"term": "a"}, root=root)
            mcp_server.dispatch("weld_query", {"term": "b"}, root=root)
            mcp_server.dispatch("weld_query", {"term": "c"}, root=root)

            events = _read_events(root)
            self.assertEqual(len(events), 3)
            for ev in events:
                self.assertEqual(ev["surface"], "mcp")
                self.assertEqual(ev["command"], "weld_query")
                self.assertEqual(ev["outcome"], "ok")
                self.assertEqual(ev["exit_code"], -1)


class WriterFailureIsolationTests(_MCPTestBase):
    """ADR 0035 § "Failure-isolated writer": telemetry breakage MUST NOT
    alter dispatch return value or exception propagation.
    """

    def test_writer_failure_does_not_alter_success_return(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo_with_graph(Path(tmp))

            baseline = mcp_server.dispatch(
                "weld_query", {"term": "Foo"}, root=root,
            )
            # Wipe events the baseline produced so the second call's
            # writer monkey-patch is the only thing under test.
            (root / ".weld" / tel.TELEMETRY_FILENAME).unlink()

            with mock.patch.object(
                tel,
                "_write_locked",
                side_effect=RuntimeError("synthetic writer failure"),
            ):
                result = mcp_server.dispatch(
                    "weld_query", {"term": "Foo"}, root=root,
                )

            # Return value identical to baseline (writer failure invisible
            # to caller).
            self.assertEqual(result, baseline)
            # No event landed because the writer raised; failure isolation
            # swallowed it inside Recorder.__exit__.
            self.assertEqual(_read_events(root), [])

    def test_writer_failure_preserves_original_exception(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo_with_graph(Path(tmp))

            with mock.patch.object(
                tel,
                "_write_locked",
                side_effect=RuntimeError("synthetic writer failure"),
            ):
                with self.assertRaises(KeyError) as ctx:
                    mcp_server.dispatch(
                        "weld_does_not_exist", {}, root=root,
                    )

            # Inner KeyError propagated unchanged; the writer's
            # RuntimeError did NOT replace it.
            self.assertIn("weld_does_not_exist", str(ctx.exception))


class OptOutDisablesRecordingTests(_MCPTestBase):
    def test_env_var_off_records_no_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo_with_graph(Path(tmp))

            with mock.patch.dict(os.environ, {"WELD_TELEMETRY": "off"}):
                result = mcp_server.dispatch(
                    "weld_query", {"term": "Foo"}, root=root,
                )

            self.assertIsInstance(result, dict)
            # No telemetry file should be created when opted out on the
            # very first call.
            self.assertFalse(
                (root / ".weld" / tel.TELEMETRY_FILENAME).exists()
            )


class DispatchSignatureIsPreservedTests(_MCPTestBase):
    """The wrapping must keep the public signature of ``dispatch``.

    Callers in tests, the stdio loop, and downstream tools rely on the
    ``(tool_name, arguments, *, root=...)`` shape and on ``KeyError`` for
    unknown tools. We assert both here so any future refactor that breaks
    them fails loudly instead of silently changing behavior.
    """

    def test_keyword_only_root_still_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = _make_repo_with_graph(Path(tmp))
            # ``root`` must remain keyword-only -- positional should fail.
            with self.assertRaises(TypeError):
                mcp_server.dispatch("weld_query", {"term": "Foo"}, root)  # type: ignore[misc]
            # Keyword form still works.
            mcp_server.dispatch("weld_query", {"term": "Foo"}, root=root)


if __name__ == "__main__":
    unittest.main()
