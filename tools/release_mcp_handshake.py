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
import time

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


def _build_messages() -> list[dict]:
    """Return the JSON-RPC messages used by the handshake, in send order."""
    return [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "release-handshake", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]


def _find_tools_list_response(stdout: str) -> dict | None:
    """Return the JSON-RPC response with ``id == 2`` (tools/list), if any."""
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("id") == 2:
            return msg
    return None


# Inter-message pause that gives the mcp stdio server time to drain each
# JSON-RPC line through its anyio reader/writer task pair before we feed
# it the next one (or signal EOF). 200ms is comfortably above the
# observed processing latency on every supported interpreter
# (3.10-3.13) while still keeping the publish-pipeline handshake under
# a second on the happy path. Without this pause the wrapper is racy:
# `subprocess.run(..., input=payload)` writes all three messages and
# closes stdin in one shot, and the server's task group can tear down
# on EOF before the third line is delivered to the request loop -- the
# observed CI symptom on Python 3.11 and reproducible locally on 3.10
# and 3.12 with mcp 1.27.0.
_MESSAGE_PAUSE_S = 0.2


def run_handshake(work_dir: str) -> int:
    """Run the handshake against ``python -m weld.mcp_server``.

    Returns the exit code the CLI should propagate.

    Implementation note: messages are sent through ``Popen.stdin`` one
    at a time with a small flush+pause between each so the mcp stdio
    server has a chance to consume every line before we close stdin.
    The previous ``subprocess.run(..., input=...)`` form wrote the full
    payload and EOF'd in a single syscall, which raced the server's
    anyio task group and left ``tools/list`` (id=2) unanswered.
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
    assert proc.stdin is not None  # guaranteed by stdin=PIPE
    try:
        for msg in _build_messages():
            try:
                proc.stdin.write(json.dumps(msg) + "\n")
                proc.stdin.flush()
            except (BrokenPipeError, OSError):
                # Server exited (e.g., crashed) before we finished
                # sending. Stop writing and let the diagnostic block
                # below report the non-zero return code via
                # ``communicate()``.
                break
            time.sleep(_MESSAGE_PAUSE_S)
        # ``communicate()`` will close stdin and drain stdout/stderr.
        try:
            stdout, stderr = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            sys.stderr.write(
                "server did not exit within 30s of EOF\n"
                f"--- stderr ---\n{stderr}\n"
                f"--- stdout ---\n{stdout}\n"
            )
            return 1
    finally:
        # Defensive: kill if still alive (shouldn't happen after
        # communicate, but keeps a stray child from outliving us).
        if proc.poll() is None:
            proc.kill()

    if proc.returncode not in (0, None):
        sys.stderr.write(
            f"server exited rc={proc.returncode}\n"
            f"--- stderr ---\n{stderr}\n"
            f"--- stdout ---\n{stdout}\n"
        )
        return 1

    resp = _find_tools_list_response(stdout)
    if resp is None:
        sys.stderr.write(
            "no tools/list response in stdout\n"
            f"--- stdout ---\n{stdout}\n"
            f"--- stderr ---\n{stderr}\n"
        )
        return 1

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
