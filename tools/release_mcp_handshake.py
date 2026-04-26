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


def _build_payload() -> str:
    """Return newline-delimited JSON-RPC messages for the handshake."""
    msgs = [
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
    return "".join(json.dumps(m) + "\n" for m in msgs)


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


def run_handshake(work_dir: str) -> int:
    """Run the handshake against ``python -m weld.mcp_server``.

    Returns the exit code the CLI should propagate.
    """
    expected = sorted(_EXPECTED_TOOL_NAMES)

    proc = subprocess.run(
        [sys.executable, "-m", "weld.mcp_server", work_dir],
        input=_build_payload(),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode not in (0, None):
        sys.stderr.write(
            f"server exited rc={proc.returncode}\n"
            f"--- stderr ---\n{proc.stderr}\n"
            f"--- stdout ---\n{proc.stdout}\n"
        )
        return 1

    resp = _find_tools_list_response(proc.stdout)
    if resp is None:
        sys.stderr.write(
            "no tools/list response in stdout\n"
            f"--- stdout ---\n{proc.stdout}\n"
            f"--- stderr ---\n{proc.stderr}\n"
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
