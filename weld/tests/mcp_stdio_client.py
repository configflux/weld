"""Test-side client for the ``python -m weld.mcp_server`` stdio surface.

Everything about driving the server *as a child process* lives here: the
environment it is launched with, the spawn/teardown, and the newline-framed
JSON-RPC it speaks. ``weld_mcp_smoke_test`` keeps the assertions about what
the server answers; this module keeps the plumbing that gets it asked.

The teardown is the load-bearing part. ``with subprocess.Popen(...)`` closes
the three pipe objects for you, but its ``__exit__`` ends in an *unbounded*
``wait()`` -- so a server that ignores EOF would hang the runner instead of
failing the test, trading a warning for something far worse. :func:`shutdown`
keeps the bounded shutdown (close stdin, wait with a timeout, kill if that
elapses) and closes every pipe on the way out. Leaving the stdout/stderr
readers to the collector instead is what produced

    ResourceWarning: unclosed file <_io.FileIO name=6 mode='rb'>

on every run that has a supported SDK -- log noise on public CI, and an
unreaped fd pair on a loaded runner.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

#: Seconds the server gets to exit on its own after stdin closes.
DEFAULT_SHUTDOWN_TIMEOUT = 10.0

#: Separate, generous grace for a *killed* child to be reaped and drained.
#: Deliberately not derived from the timeout above: a caller may bound the
#: polite exit tightly, and SIGKILL reaping should still not be raced.
_KILL_GRACE = 5.0


def server_env() -> dict[str, str]:
    """Return a child environment that imports weld from this checkout.

    The child would otherwise resolve ``weld`` against whatever happens to be
    installed, which is how a subprocess smoke test quietly stops testing the
    tree it was run from.
    """
    env = os.environ.copy()
    repo = str(Path(__file__).resolve().parent.parent.parent)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = repo + (os.pathsep + existing if existing else "")
    return env


@contextlib.contextmanager
def server_process(
    timeout: float = DEFAULT_SHUTDOWN_TIMEOUT,
) -> Iterator[subprocess.Popen]:
    """Spawn the stdio server on three pipes and always tear it down.

    ``bufsize=0`` because the caller writes a request and immediately blocks
    on the reply: any buffering on stdin is a deadlock waiting to happen.
    """
    proc = subprocess.Popen(
        [sys.executable, "-m", "weld.mcp_server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=server_env(),
        bufsize=0,
    )
    try:
        yield proc
    finally:
        shutdown(proc, timeout)


def shutdown(
    proc: subprocess.Popen, timeout: float = DEFAULT_SHUTDOWN_TIMEOUT
) -> None:
    """Close stdin, bound the wait, kill if it elapses, close every pipe.

    ``communicate`` is what closes stdin -- the server's EOF signal -- and it
    drains stdout/stderr while waiting, so a server that logs more than a pipe
    buffer holds cannot deadlock against the timeout. The trailing loop runs
    unconditionally: whichever branch above was taken, every pipe object ends
    closed, which is the invariant ``mcp_stdio_client_test`` asserts. A second
    timeout after ``kill()`` is swallowed rather than raised, so a wedged child
    cannot mask the test failure that is usually the reason we got here.
    """
    try:
        proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.communicate(timeout=_KILL_GRACE)
    finally:
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            if pipe is not None:
                with contextlib.suppress(OSError, ValueError):
                    pipe.close()


def send(proc: subprocess.Popen, payload: dict) -> None:
    """Write one JSON-RPC message in the newline framing MCP uses on stdio."""
    assert proc.stdin is not None
    proc.stdin.write(json.dumps(payload).encode("utf-8") + b"\n")
    proc.stdin.flush()


def recv(proc: subprocess.Popen) -> dict:
    """Read one JSON-RPC reply, reporting the child's stderr on EOF."""
    assert proc.stdout is not None
    line = proc.stdout.readline()
    if not line:
        raise AssertionError(
            f"MCP server closed stdout without a reply; stderr={_stderr(proc)!r}"
        )
    return json.loads(line.decode("utf-8"))


def _stderr(proc: subprocess.Popen) -> bytes:
    """Best-effort drain of the child's stderr, for a failure message only."""
    if proc.stderr is None:
        return b""
    try:
        return proc.stderr.read()
    except Exception:
        return b""
