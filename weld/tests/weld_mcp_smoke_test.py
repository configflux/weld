"""Smoke test for the weld MCP stdio server.

Pins the public tool surface against the documented tool list in
``docs/mcp.md`` and confirms the server entrypoint actually starts as a
subprocess. Complements :mod:`weld_mcp_server_test` (which exercises the
pure-Python adapter surface) by also covering:

* Module import does not require the optional ``mcp`` SDK (re-verified
  here because agents grepping for "smoke test" will land on this file
  before the broader adapter test, and the no-SDK invariant is the most
  load-bearing precondition for every MCP deployment).
* ``python -m weld.mcp_server`` boots cleanly: with the ``mcp`` SDK it
  answers a full ``initialize`` + ``tools/list`` JSON-RPC round trip and
  the wire tool list matches the in-process registry; without the SDK it
  emits the documented install hint on stderr and exits with status 2.

This test is intentionally strict: any renamed, added, or removed tool
fails the expected-name-set assertion. Wire-protocol coverage is skipped
(not faked) when the optional SDK is absent, so the test stays green in
the default bazel environment while still catching any regression the
moment ``mcp`` lands in the runfiles.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)
_tests_dir = str(Path(__file__).resolve().parent)
if _tests_dir not in sys.path:
    sys.path.insert(0, _tests_dir)

from weld import mcp_server  # noqa: E402
from mcp_expected_tools import EXPECTED_TOOL_NAMES as _EXPECTED_TOOL_NAMES  # noqa: E402

# ---------------------------------------------------------------------------
# Expected tool set -- source of truth is docs/mcp.md
# ---------------------------------------------------------------------------
#
# The ``_EXPECTED_TOOL_NAMES`` set is consolidated in
# ``weld/tests/mcp_expected_tools.py`` and shared with the other MCP tests.
# When adding or renaming a tool, update:
#   1. weld/_mcp_tools.py::build_tools
#   2. docs/mcp.md (the "Exposed tools" table)
#   3. weld/tests/mcp_expected_tools.py::EXPECTED_TOOL_NAMES
# A delta in any one of those three places must be reflected in the other
# two, which is the whole point of pinning the set here.


def _mcp_sdk_available() -> bool:
    """Return True if the optional ``mcp`` SDK can be imported."""
    try:
        import mcp  # type: ignore  # noqa: F401
    except ImportError:
        return False
    return True


class WeldMcpModuleLoadTest(unittest.TestCase):
    """The module must import and expose its public surface without the SDK."""

    def test_module_exposes_public_entry_points(self) -> None:
        for attr in ("build_tools", "dispatch", "run_stdio", "main"):
            self.assertTrue(
                hasattr(mcp_server, attr),
                f"weld.mcp_server is missing public attribute {attr!r}",
            )

    def test_build_tools_returns_non_empty_list(self) -> None:
        tools = mcp_server.build_tools()
        self.assertIsInstance(tools, list)
        self.assertGreater(len(tools), 0)


class WeldMcpExpectedToolListTest(unittest.TestCase):
    """Pin the registered tool set exactly.

    Fails if any tool is added, removed, or renamed. This is the core of
    the smoke test: it is what catches an accidental tool disappearance.
    """

    def test_registry_matches_expected_name_set(self) -> None:
        tools = mcp_server.build_tools()
        names = frozenset(t.name for t in tools)
        self.assertEqual(
            names,
            _EXPECTED_TOOL_NAMES,
            (
                "MCP tool list drift.\n"
                f"  unexpected: {sorted(names - _EXPECTED_TOOL_NAMES)}\n"
                f"  missing:    {sorted(_EXPECTED_TOOL_NAMES - names)}\n"
                "If this is intentional, update _EXPECTED_TOOL_NAMES in "
                "this test AND the 'Exposed tools' table in docs/mcp.md."
            ),
        )

    def test_registry_length_matches_expected(self) -> None:
        tools = mcp_server.build_tools()
        self.assertEqual(
            len(tools),
            len(_EXPECTED_TOOL_NAMES),
            "Duplicate or missing tool in build_tools() registry.",
        )

    def test_every_tool_has_description_and_schema(self) -> None:
        for tool in mcp_server.build_tools():
            self.assertTrue(
                tool.description and tool.description.strip(),
                f"tool {tool.name!r} has empty description",
            )
            self.assertIsInstance(
                tool.input_schema, dict,
                f"tool {tool.name!r} input_schema must be dict",
            )
            self.assertEqual(
                tool.input_schema.get("type"),
                "object",
                f"tool {tool.name!r} input_schema.type must be 'object'",
            )
            self.assertIn(
                "properties",
                tool.input_schema,
                f"tool {tool.name!r} input_schema missing 'properties'",
            )


class WeldMcpSubprocessSmokeTest(unittest.TestCase):
    """Boot ``python -m weld.mcp_server`` in a child process.

    Two modes:
    * With ``mcp`` SDK: exchange an ``initialize`` + ``tools/list``
      JSON-RPC pair over stdio and compare the advertised names to the
      in-process registry.
    * Without the SDK: confirm the documented graceful-degrade path --
      exit status 2 with an install hint on stderr. This keeps the test
      green in the default bazel runfiles while still asserting the
      server actually ran.
    """

    def _server_env(self) -> dict:
        env = os.environ.copy()
        # Make sure the child process can import weld from this checkout.
        repo = str(Path(__file__).resolve().parent.parent.parent)
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = repo + (os.pathsep + existing if existing else "")
        return env

    def test_subprocess_without_sdk_exits_with_install_hint(self) -> None:
        if _mcp_sdk_available():
            self.skipTest("mcp SDK installed; SDK-absent path is not exercised here")

        proc = subprocess.run(
            [sys.executable, "-m", "weld.mcp_server"],
            input=b"",
            capture_output=True,
            env=self._server_env(),
            timeout=30,
        )
        # Documented behavior in weld/mcp_server.py::run_stdio when mcp is
        # missing: stderr hint + exit code 2. Failing this check means the
        # optional-dependency story is broken.
        self.assertEqual(
            proc.returncode,
            2,
            f"expected exit 2 without mcp SDK, got {proc.returncode}; "
            f"stderr={proc.stderr!r}",
        )
        stderr = proc.stderr.decode("utf-8", errors="replace")
        self.assertIn("mcp", stderr.lower())
        self.assertIn("install", stderr.lower())
        self.assertIn("configflux-weld[mcp]", stderr)

    def test_help_does_not_require_sdk(self) -> None:
        proc = subprocess.run(
            [sys.executable, "-m", "weld.mcp_server", "--help"],
            capture_output=True,
            env=self._server_env(),
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Usage: python -m weld.mcp_server", proc.stdout)
        self.assertIn("configflux-weld[mcp]", proc.stdout)

    def test_subprocess_with_sdk_lists_expected_tools(self) -> None:
        if not _mcp_sdk_available():
            self.skipTest("mcp SDK not installed; wire-protocol path is not exercisable")

        proc = subprocess.Popen(
            [sys.executable, "-m", "weld.mcp_server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self._server_env(),
            bufsize=0,
        )
        try:
            names = self._list_tools_over_stdio(proc)
        finally:
            try:
                proc.stdin.close()  # type: ignore[union-attr]
            except Exception:
                pass
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)

        self.assertEqual(
            names,
            _EXPECTED_TOOL_NAMES,
            (
                "Wire-protocol tool list did not match expected.\n"
                f"  wire:     {sorted(names)}\n"
                f"  expected: {sorted(_EXPECTED_TOOL_NAMES)}"
            ),
        )

    # ------------------------------------------------------------------
    # Minimal JSON-RPC client for the MCP stdio framing
    # ------------------------------------------------------------------

    def _send(self, proc: subprocess.Popen, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8") + b"\n"
        assert proc.stdin is not None
        proc.stdin.write(data)
        proc.stdin.flush()

    def _recv(self, proc: subprocess.Popen) -> dict:
        assert proc.stdout is not None
        line = proc.stdout.readline()
        if not line:
            stderr = b""
            if proc.stderr is not None:
                try:
                    stderr = proc.stderr.read()
                except Exception:
                    stderr = b""
            raise AssertionError(
                f"MCP server closed stdout without a reply; stderr={stderr!r}"
            )
        return json.loads(line.decode("utf-8"))

    def _list_tools_over_stdio(self, proc: subprocess.Popen) -> frozenset[str]:
        """Drive the minimum JSON-RPC handshake and return advertised names."""
        self._send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "weld-smoke-test", "version": "1.0"},
                },
            },
        )
        init_reply = self._recv(proc)
        self.assertEqual(init_reply.get("id"), 1, f"bad initialize reply: {init_reply}")

        # Notify initialized. Notifications take no reply.
        self._send(
            proc,
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
        )

        self._send(
            proc,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        tools_reply = self._recv(proc)
        self.assertEqual(tools_reply.get("id"), 2, f"bad tools/list reply: {tools_reply}")
        result = tools_reply.get("result") or {}
        tools = result.get("tools") or []
        return frozenset(t.get("name") for t in tools if t.get("name"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
