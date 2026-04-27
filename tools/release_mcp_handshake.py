#!/usr/bin/env python3
"""Wire-level MCP handshake for the public release pipeline.

Invoked by the public ``publish-pypi.yml`` workflow after installing
``configflux-weld[mcp]`` into a disposable venv. Performs a minimal
MCP handshake (``initialize`` + ``notifications/initialized`` +
``tools/list``) against ``python -m weld.mcp_server`` and asserts the
wire response lists exactly the tool names embedded below. A drift
between the running registry and this fixture exits non-zero so the
release fails before the irreversible PyPI upload.

Usage:

    python tools/release_mcp_handshake.py [working_dir]

``working_dir`` is optional and defaults to a fresh tempdir. When
supplied it must be writable and is used as the server's working
directory so the request does not consult an existing
``.weld/graph.json``.

Exit codes:

* 0 -- handshake succeeded; ``tools/list`` matched the embedded names.
* 1 -- any handshake or assertion failure (full diagnostic on stderr).

Implementation note (race-freedom): the handshake is fully
synchronous on both sides. The wrapper sends ``initialize``, waits
for the ``id=1`` response on stdout (with a per-step timeout), and
only then sends ``notifications/initialized`` followed immediately
by ``tools/list``. It then waits for the ``id=2`` response on
stdout. No reliance on stdin EOF semantics, no inter-message
``time.sleep`` heuristics. This shape was chosen after
v0.10.0/v0.11.0 publish failures where the ``subprocess.run(...,
input=...)`` and ``Popen + sleep + communicate()`` shapes both
raced the mcp server's anyio task group on github.com runners
(initialize response observed; tools/list response missing).

The script is intentionally self-contained: stdlib only, plus the
``mcp`` package brought in by the ``[mcp]`` extra at install time.
The expected name list is embedded sorted at the top of the file so
public readers can audit what is being asserted without consulting
any other source.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading

# The 13 tool names exposed by weld's MCP server registry. Embedded
# sorted so a reader can audit the assertion locally without grepping
# the wheel. Drift between this list and the live registry fails the
# handshake; drift between this list and the in-tree fixture is
# guarded by an internal consistency test before this overlay ships.
_EXPECTED_TOOL_NAMES = [
    "weld_brief",
    "weld_callers",
    "weld_context",
    "weld_diff",
    "weld_enrich",
    "weld_export",
    "weld_find",
    "weld_impact",
    "weld_path",
    "weld_query",
    "weld_references",
    "weld_stale",
    "weld_trace",
]

# Per-step timeout (seconds). The handshake should complete in well
# under a second on the happy path; a generous cap here keeps the
# publish pipeline snappy while still tolerating cold-import latency
# on slow CI runners.
_RESPONSE_TIMEOUT_S = 30.0


def _initialize_msg() -> dict:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "release-handshake", "version": "0"},
        },
    }


def _initialized_notification() -> dict:
    return {"jsonrpc": "2.0", "method": "notifications/initialized"}


def _tools_list_msg() -> dict:
    return {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}


def _send(proc: subprocess.Popen, msg: dict) -> None:
    """Write one JSON-RPC line + flush. Raises if the pipe is closed."""
    assert proc.stdin is not None
    proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()


def _read_response_with_id(
    proc: subprocess.Popen,
    expected_id: int,
    captured_lines: list[str],
    timeout_s: float = _RESPONSE_TIMEOUT_S,
) -> dict:
    """Block until a stdout line parses as JSON-RPC with ``id=expected_id``.

    Lines that don't match (notifications, log lines, etc.) are kept in
    ``captured_lines`` for a diagnostic dump on failure. Raises on
    timeout, EOF before the response, or a server crash.
    """
    assert proc.stdout is not None
    result: dict[str, dict] = {}
    error: dict[str, BaseException] = {}

    def reader() -> None:
        try:
            for raw in proc.stdout:  # type: ignore[union-attr]
                line = raw.rstrip("\n")
                captured_lines.append(line)
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    msg = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if msg.get("id") == expected_id:
                    result["msg"] = msg
                    return
        except BaseException as exc:  # noqa: BLE001 -- diagnostic surface
            error["exc"] = exc

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    t.join(timeout=timeout_s)
    if t.is_alive():
        raise TimeoutError(
            f"no response with id={expected_id} within {timeout_s}s"
        )
    if "exc" in error:
        raise error["exc"]
    if "msg" not in result:
        raise RuntimeError(
            f"server stdout closed before id={expected_id} response"
        )
    return result["msg"]


def _format_diagnostic(stdout_lines: list[str], proc: subprocess.Popen) -> str:
    """Render captured output for a failure message."""
    stderr = ""
    if proc.stderr is not None:
        try:
            stderr = proc.stderr.read() or ""
        except Exception:  # noqa: BLE001
            stderr = "<stderr drain failed>"
    return (
        "--- stdout ---\n"
        + "\n".join(stdout_lines)
        + "\n--- stderr ---\n"
        + stderr
    )


def run_handshake(work_dir: str) -> int:
    """Run the handshake against ``python -m weld.mcp_server``.

    Returns the exit code the CLI should propagate.
    """
    expected = sorted(_EXPECTED_TOOL_NAMES)

    proc = subprocess.Popen(
        [sys.executable, "-m", "weld.mcp_server", work_dir],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    captured: list[str] = []
    try:
        # Phase 1: send initialize, wait for id=1 response.
        try:
            _send(proc, _initialize_msg())
        except (BrokenPipeError, OSError) as exc:
            sys.stderr.write(
                f"server pipe closed before initialize was sent: {exc}\n"
                + _format_diagnostic(captured, proc)
                + "\n"
            )
            return 1
        try:
            _read_response_with_id(proc, expected_id=1, captured_lines=captured)
        except (TimeoutError, RuntimeError) as exc:
            sys.stderr.write(
                f"initialize handshake failed: {exc}\n"
                + _format_diagnostic(captured, proc)
                + "\n"
            )
            return 1

        # Phase 2: notifications/initialized + tools/list, wait for id=2.
        try:
            _send(proc, _initialized_notification())
            _send(proc, _tools_list_msg())
        except (BrokenPipeError, OSError) as exc:
            sys.stderr.write(
                f"server pipe closed before tools/list was sent: {exc}\n"
                + _format_diagnostic(captured, proc)
                + "\n"
            )
            return 1
        try:
            resp = _read_response_with_id(
                proc, expected_id=2, captured_lines=captured
            )
        except (TimeoutError, RuntimeError) as exc:
            sys.stderr.write(
                f"tools/list handshake failed: {exc}\n"
                + _format_diagnostic(captured, proc)
                + "\n"
            )
            return 1
    finally:
        # Close stdin to signal end-of-input, then drain. The server
        # will tear down on EOF; communicate() collects whatever is
        # left and reaps the child.
        try:
            if proc.stdin is not None and not proc.stdin.closed:
                proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    names = sorted(t["name"] for t in resp.get("result", {}).get("tools", []))
    if names != expected:
        sys.stderr.write(
            "tools/list wire response disagrees with embedded fixture\n"
            f"  got      -> {names}\n"
            f"  expected -> {expected}\n"
        )
        return 1

    print(f"stdio handshake OK ({len(names)} tools)")
    return 0


def main(argv: list[str]) -> int:
    """CLI entrypoint. Optional argv[1] sets the server's working dir."""
    if len(argv) > 1:
        work_dir = argv[1]
        os.makedirs(work_dir, exist_ok=True)
        return run_handshake(work_dir)

    with tempfile.TemporaryDirectory(prefix="weld-mcp-handshake-") as tmp:
        return run_handshake(tmp)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
