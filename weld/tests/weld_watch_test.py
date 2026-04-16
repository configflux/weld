"""Tests for ``weld.watch`` -- auto-rediscovery on filesystem change.

Scenarios covered:

- ``parse_debounce`` accepts the supported formats and rejects bad input.
- ``_PollingBackend`` detects added, modified, and deleted files.
- ``WatchEngine`` debounces rapid bursts of events.
- ``WatchEngine`` calls the discovery callback on change and emits
  a graph diff summary.
- ``get_backend`` returns a polling backend when watchdog is not
  installed, and a watchdog backend when it is.
"""

from __future__ import annotations

import io
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from weld import watch  # noqa: E402


# ---------------------------------------------------------------------------
# parse_debounce
# ---------------------------------------------------------------------------


class ParseDebounceTests(unittest.TestCase):
    def test_seconds_suffix(self) -> None:
        self.assertAlmostEqual(watch.parse_debounce("2s"), 2.0)

    def test_fractional_seconds(self) -> None:
        self.assertAlmostEqual(watch.parse_debounce("1.5s"), 1.5)

    def test_milliseconds_suffix(self) -> None:
        self.assertAlmostEqual(watch.parse_debounce("500ms"), 0.5)

    def test_bare_number_is_seconds(self) -> None:
        self.assertAlmostEqual(watch.parse_debounce("3"), 3.0)

    def test_zero_allowed(self) -> None:
        self.assertAlmostEqual(watch.parse_debounce("0"), 0.0)

    def test_rejects_negative(self) -> None:
        with self.assertRaises(ValueError):
            watch.parse_debounce("-1s")

    def test_rejects_garbage(self) -> None:
        with self.assertRaises(ValueError):
            watch.parse_debounce("fast")

    def test_rejects_empty(self) -> None:
        with self.assertRaises(ValueError):
            watch.parse_debounce("")


# ---------------------------------------------------------------------------
# PollingBackend
# ---------------------------------------------------------------------------


class PollingBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / ".weld").mkdir()

    def _enumerate_all(self, root: Path) -> list[str]:
        files: list[str] = []
        for p in root.rglob("*"):
            if p.is_file() and ".weld" not in p.parts:
                files.append(str(p.relative_to(root)))
        return sorted(files)

    def test_reports_added_file(self) -> None:
        backend = watch._PollingBackend(self.root, self._enumerate_all)
        backend.snapshot()  # baseline: empty
        (self.root / "a.py").write_text("x = 1", encoding="utf-8")
        changed = backend.poll()
        self.assertEqual(changed, {"a.py"})

    def test_reports_modified_file(self) -> None:
        (self.root / "a.py").write_text("x = 1", encoding="utf-8")
        backend = watch._PollingBackend(self.root, self._enumerate_all)
        backend.snapshot()
        # Force a different mtime -- set to a past timestamp then modify.
        import os
        past = time.time() - 10
        os.utime(self.root / "a.py", (past, past))
        backend.snapshot()
        (self.root / "a.py").write_text("x = 2", encoding="utf-8")
        changed = backend.poll()
        self.assertEqual(changed, {"a.py"})

    def test_reports_deleted_file(self) -> None:
        (self.root / "a.py").write_text("x = 1", encoding="utf-8")
        backend = watch._PollingBackend(self.root, self._enumerate_all)
        backend.snapshot()
        (self.root / "a.py").unlink()
        changed = backend.poll()
        self.assertEqual(changed, {"a.py"})

    def test_no_change_returns_empty(self) -> None:
        (self.root / "a.py").write_text("x = 1", encoding="utf-8")
        backend = watch._PollingBackend(self.root, self._enumerate_all)
        backend.snapshot()
        self.assertEqual(backend.poll(), set())


# ---------------------------------------------------------------------------
# WatchEngine
# ---------------------------------------------------------------------------


class _FakeClock:
    """Deterministic monotonic clock with a drive-able now()."""

    def __init__(self, start: float = 0.0) -> None:
        self.t = start

    def now(self) -> float:
        return self.t

    def advance(self, delta: float) -> None:
        self.t += delta


class WatchEngineTests(unittest.TestCase):
    def _make_engine(
        self,
        on_change,
        poll_fn,
        *,
        debounce: float = 0.1,
    ):
        clock = _FakeClock()
        engine = watch.WatchEngine(
            debounce_seconds=debounce,
            poll_fn=poll_fn,
            on_change=on_change,
            clock=clock.now,
            sleep=lambda _s: None,
        )
        return engine, clock

    def test_single_change_triggers_callback(self) -> None:
        events = iter([{"a.py"}, set(), set()])
        calls: list[set[str]] = []

        def on_change(changed: set[str]) -> None:
            calls.append(set(changed))

        engine, clock = self._make_engine(
            on_change=on_change,
            poll_fn=lambda: next(events, set()),
            debounce=0.5,
        )

        # First tick: change detected, pending set starts
        engine.tick()
        self.assertEqual(calls, [])  # debounce not elapsed
        # Advance past debounce; next tick with no new change should flush
        clock.advance(0.6)
        engine.tick()
        self.assertEqual(calls, [{"a.py"}])

    def test_burst_is_debounced_into_one_call(self) -> None:
        # Two bursts of changes inside the debounce window should collapse
        # into a single callback invocation.
        events = iter([{"a.py"}, {"b.py"}, set(), set()])
        calls: list[set[str]] = []

        def on_change(changed: set[str]) -> None:
            calls.append(set(changed))

        engine, clock = self._make_engine(
            on_change=on_change,
            poll_fn=lambda: next(events, set()),
            debounce=1.0,
        )
        engine.tick()                # change at t=0
        clock.advance(0.3)
        engine.tick()                # another change, still within debounce
        self.assertEqual(calls, [])
        clock.advance(1.1)           # advance past debounce since last change
        engine.tick()                # no new events, should flush
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], {"a.py", "b.py"})

    def test_no_events_no_callback(self) -> None:
        calls: list[set[str]] = []
        engine, clock = self._make_engine(
            on_change=lambda c: calls.append(set(c)),
            poll_fn=lambda: set(),
            debounce=0.1,
        )
        for _ in range(5):
            engine.tick()
            clock.advance(0.2)
        self.assertEqual(calls, [])

    def test_second_change_after_flush_triggers_second_callback(self) -> None:
        events = iter([{"a.py"}, set(), set(), {"b.py"}, set(), set()])
        calls: list[set[str]] = []

        def on_change(changed: set[str]) -> None:
            calls.append(set(changed))

        engine, clock = self._make_engine(
            on_change=on_change,
            poll_fn=lambda: next(events, set()),
            debounce=0.5,
        )
        engine.tick()                   # change 1
        clock.advance(0.6)
        engine.tick()                   # flush 1
        engine.tick()                   # idle
        engine.tick()                   # change 2
        clock.advance(0.6)
        engine.tick()                   # flush 2
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], {"a.py"})
        self.assertEqual(calls[1], {"b.py"})


# ---------------------------------------------------------------------------
# run_once and diff summary
# ---------------------------------------------------------------------------


class RunOnceTests(unittest.TestCase):
    def test_run_once_calls_discovery_and_emits_summary(self) -> None:
        fake_summary = "+ 1 node added (x)"
        out = io.StringIO()

        discover_calls: list[set[str]] = []

        def fake_discover(changed: set[str]) -> str:
            discover_calls.append(set(changed))
            return fake_summary

        watch.run_once({"a.py", "b.py"}, fake_discover, stream=out)

        self.assertEqual(discover_calls, [{"a.py", "b.py"}])
        output = out.getvalue()
        self.assertIn(fake_summary, output)
        # It should mention the number of files changed.
        self.assertIn("2", output)

    def test_run_once_handles_empty_diff(self) -> None:
        out = io.StringIO()

        def fake_discover(_changed: set[str]) -> str:
            return "No changes detected."

        watch.run_once({"a.py"}, fake_discover, stream=out)
        output = out.getvalue()
        self.assertIn("No changes detected", output)


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------


class GetBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_falls_back_to_polling_when_watchdog_missing(self) -> None:
        with mock.patch.object(watch, "_try_import_watchdog", return_value=None):
            backend = watch.get_backend(
                self.root,
                lambda _r: [],
                prefer_watchdog=True,
            )
        self.assertIsInstance(backend, watch._PollingBackend)

    def test_respects_prefer_watchdog_false(self) -> None:
        # Even if watchdog is installed, prefer_watchdog=False forces polling.
        with mock.patch.object(watch, "_try_import_watchdog", return_value=object()):
            backend = watch.get_backend(
                self.root,
                lambda _r: [],
                prefer_watchdog=False,
            )
        self.assertIsInstance(backend, watch._PollingBackend)


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------


class CLIArgsTests(unittest.TestCase):
    def test_parse_watch_args_default_debounce(self) -> None:
        args = watch._parse_args([])
        self.assertAlmostEqual(args.debounce_seconds, watch.DEFAULT_DEBOUNCE)

    def test_parse_watch_args_custom_debounce(self) -> None:
        args = watch._parse_args(["--debounce", "2s"])
        self.assertAlmostEqual(args.debounce_seconds, 2.0)

    def test_parse_watch_args_root(self) -> None:
        args = watch._parse_args(["/tmp/some/root"])
        self.assertEqual(args.root, "/tmp/some/root")

    def test_parse_watch_args_rejects_bad_debounce(self) -> None:
        with self.assertRaises(SystemExit):
            watch._parse_args(["--debounce", "fast"])


# ---------------------------------------------------------------------------
# CLI wiring smoke test
# ---------------------------------------------------------------------------


class CLIDispatchTests(unittest.TestCase):
    """Verify the top-level CLI dispatcher routes 'watch' to weld.watch.main.

    We don't actually run the watch loop -- we capture the call and return
    a sentinel exit code so the test is deterministic.
    """

    def test_cli_dispatches_watch_subcommand(self) -> None:
        from weld import cli as cli_mod

        captured: dict = {}

        def fake_watch_main(argv):
            captured["argv"] = list(argv)
            return 0

        with mock.patch.object(watch, "main", side_effect=fake_watch_main):
            rc = cli_mod.main(["watch", "--debounce", "2s", "/tmp/demo"])

        self.assertEqual(rc, 0)
        self.assertEqual(
            captured["argv"],
            ["--debounce", "2s", "/tmp/demo"],
        )

    def test_cli_help_mentions_watch(self) -> None:
        """The top-level --help output must advertise the watch subcommand."""
        from weld import cli as cli_mod

        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            rc = cli_mod.main(["--help"])
        self.assertEqual(rc, 0)
        self.assertIn("watch", out.getvalue())


if __name__ == "__main__":
    unittest.main()
