"""Fd-lifecycle pin for the MCP stdio test client.

The wire smoke test spawns ``python -m weld.mcp_server`` on three pipes.
Closing only stdin leaves the stdout and stderr readers for the collector,
which surfaces as ``ResourceWarning: unclosed file`` on every run that has a
supported SDK, and as an unreaped fd pair on a loaded runner. The invariant
that prevents both -- every pipe closed, child reaped, on every exit path --
is asserted here rather than inside the smoke test for two reasons:

* It holds on *every* environment. The smoke test's wire path only executes
  where a 2.x ``mcp`` SDK is importable, so a pin living there would skip on
  the machines most likely to regress it. ``weld.mcp_server`` exits either
  way -- serving the protocol with an SDK, or with the documented exit-2 hint
  without one -- and both exits are a valid subject for a teardown assertion.
* The kill path cannot be reached through the server at all: it needs a child
  that ignores EOF. :func:`~weld.tests.mcp_stdio_client.shutdown` is exercised
  directly against such a child below, which is the only way that branch --
  the one a plain ``with subprocess.Popen(...)`` would have silently dropped
  in favour of an unbounded ``wait()`` -- gets covered.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

from weld.tests import mcp_stdio_client


_NAMES = ("stdin", "stdout", "stderr")


def _pipes(proc: subprocess.Popen) -> tuple:
    return (proc.stdin, proc.stdout, proc.stderr)


def assert_fully_torn_down(case: unittest.TestCase, proc: subprocess.Popen) -> None:
    """Every pipe closed and the child reaped -- the whole contract."""
    for name, pipe in zip(_NAMES, _pipes(proc)):
        case.assertIsNotNone(pipe, f"{name} was not piped")
        case.assertTrue(
            pipe.closed,
            f"{name} is still open after teardown -- this is exactly the "
            "leak that reaches CI as a ResourceWarning",
        )
    case.assertIsNotNone(proc.returncode, "child was not reaped; it is now a zombie")


class ServerProcessTeardownTest(unittest.TestCase):
    """``server_process`` must leave nothing open and nothing running."""

    def test_clean_exit_closes_every_pipe(self) -> None:
        with mcp_stdio_client.server_process() as proc:
            # Assert the precondition rather than the child's liveness: the
            # child may already be gone (it exits 2 without an SDK), but the
            # parent's three pipe objects are open either way, which is what
            # makes the closed-after assertion mean anything.
            for pipe in _pipes(proc):
                self.assertFalse(pipe.closed, "pipe was never open")
        assert_fully_torn_down(self, proc)

    def test_pipes_close_when_the_body_raises(self) -> None:
        sentinel = RuntimeError("body failed")
        with self.assertRaises(RuntimeError) as caught:
            with mcp_stdio_client.server_process() as proc:
                raise sentinel
        # The failure must reach the test runner unchanged: a teardown that
        # swallows or replaces it turns every wire failure into a mystery.
        self.assertIs(caught.exception, sentinel)
        assert_fully_torn_down(self, proc)

    def test_server_env_points_the_child_at_this_checkout(self) -> None:
        env = mcp_stdio_client.server_env()
        repo_root = str(Path(__file__).resolve().parents[2])
        self.assertEqual(
            env["PYTHONPATH"].split(os.pathsep)[0],
            repo_root,
            "child PYTHONPATH must lead with this checkout, or the smoke "
            "test silently exercises an installed weld instead",
        )


class ShutdownKillPathTest(unittest.TestCase):
    """A child that ignores EOF is killed, not waited on forever."""

    def test_child_that_ignores_eof_is_killed_and_its_pipes_closed(self) -> None:
        # Never reads stdin, so closing it is not an exit signal. A plain
        # Popen context manager would block here until the runner's test
        # timeout; the bounded shutdown must return promptly instead.
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(600)"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        mcp_stdio_client.shutdown(proc, timeout=0.5)
        assert_fully_torn_down(self, proc)
        self.assertNotEqual(
            proc.returncode, 0, "a killed child must not report success"
        )

    def test_shutdown_is_idempotent(self) -> None:
        # Teardown runs from a ``finally``; calling it on an already-reaped
        # process must not raise on top of whatever sent us there.
        proc = subprocess.Popen(
            [sys.executable, "-c", ""],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        mcp_stdio_client.shutdown(proc, timeout=10)
        mcp_stdio_client.shutdown(proc, timeout=10)
        self.assertEqual(proc.returncode, 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
