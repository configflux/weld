"""Smoke test for the weld MCP stdio server.

Pins the public tool surface against the documented tool list in
``docs/mcp.md`` and confirms the server entrypoint actually starts as a
subprocess. Complements :mod:`weld_mcp_server_test` (which exercises the
pure-Python adapter surface) by also covering:

* Module import does not require the optional ``mcp`` SDK (re-verified
  here because agents grepping for "smoke test" will land on this file
  before the broader adapter test, and the no-SDK invariant is the most
  load-bearing precondition for every MCP deployment).
* ``python -m weld.mcp_server`` boots cleanly: with a supported ``mcp``
  SDK it answers a full ``initialize`` + ``tools/list`` JSON-RPC round
  trip and the wire tool list matches the in-process registry; without
  one it emits the hint matching *why* the SDK is unusable (absent ->
  install the extra, pre-2.0 -> upgrade) and exits with status 2. The
  branch logic itself is pinned environment-independently in
  ``weld_mcp_stdio_guard_test``.

This test is intentionally strict: any renamed, added, or removed tool
fails the expected-name-set assertion. Wire-protocol coverage is skipped
(not faked) when no supported SDK is importable -- absent, or older than
the 2.x handler API weld targets -- so the test stays green in the
default bazel environment while still catching any regression the moment
a supported ``mcp`` lands in the runfiles.

A lane that *installs* the SDK sets ``WELD_TEST_REQUIRE_MCP_SDK=1``,
which turns that skip into a failure: there, a skip means the install
landed somewhere the test interpreter cannot see, and a green skip is
how that goes unnoticed for a release. Public CI is such a lane.
"""

from __future__ import annotations

import os
import site
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

_tests_dir = str(Path(__file__).resolve().parent)
if _tests_dir not in sys.path:
    sys.path.insert(0, _tests_dir)

from weld import mcp_server  # noqa: E402
from weld.tests import mcp_stdio_client as _client  # noqa: E402
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
    """Return True if a *supported* optional ``mcp`` SDK can be imported.

    weld targets the SDK 2.x low-level handler API, so a pre-2.0 SDK cannot
    serve the wire protocol: the server degrades to a hint + exit 2 instead.
    Keying both subprocess tests off this single predicate keeps them
    mutually exclusive on every environment.
    """
    try:
        from mcp.server import Server  # type: ignore
    except ImportError:
        return False
    return hasattr(Server, "add_request_handler")


def _mcp_sdk_installed() -> bool:
    """Return True if any ``mcp`` SDK is importable, supported or not.

    The degrade path is not one message but two, and this predicate is what
    separates them: an absent SDK is told to install the extra, an installed
    but pre-2.0 SDK is told to upgrade. Telling the second group to install
    what they already have is the regression this distinction guards.
    """
    try:
        import mcp  # type: ignore # noqa: F401
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

    def test_subprocess_without_usable_sdk_exits_with_matching_hint(self) -> None:
        if _mcp_sdk_available():
            self.skipTest(
                "supported mcp SDK installed; the degrade path is not exercised here"
            )

        proc = subprocess.run(
            [sys.executable, "-m", "weld.mcp_server"],
            input=b"",
            capture_output=True,
            env=_client.server_env(),
            timeout=30,
        )
        # Documented behavior in weld/_mcp_stdio.py::run_stdio when the SDK is
        # unusable: stderr hint + exit code 2. Failing this check means the
        # optional-dependency story is broken.
        self.assertEqual(
            proc.returncode,
            2,
            f"expected exit 2 without a usable mcp SDK, got {proc.returncode}; "
            f"stderr={proc.stderr!r}",
        )
        stderr = proc.stderr.decode("utf-8", errors="replace")
        self.assertIn("mcp", stderr.lower())
        self.assertIn("configflux-weld[mcp]", stderr)
        if _mcp_sdk_installed():
            # Pre-2.0 SDK: the remedy is an upgrade, and claiming the SDK is
            # absent would send the user to reinstall what they already have.
            self.assertIn("mcp>=2", stderr)
            self.assertIn("pip install -U", stderr)
            self.assertNotIn("not installed", stderr)
        else:
            self.assertIn("not installed", stderr)
            self.assertIn("install", stderr.lower())

    def test_help_does_not_require_sdk(self) -> None:
        proc = subprocess.run(
            [sys.executable, "-m", "weld.mcp_server", "--help"],
            capture_output=True,
            env=_client.server_env(),
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Usage: python -m weld.mcp_server", proc.stdout)
        self.assertIn("configflux-weld[mcp]", proc.stdout)

    def _skip_or_fail_without_sdk(self) -> None:
        """Skip -- unless the lane promised an SDK, in which case fail.

        Same idiom as ``WELD_LINT_REQUIRE_MARKDOWNLINT``. The interpreter
        named below is the one that has to see the SDK: under Bazel that is
        the hermetic toolchain, not the runner's python that ran pip.
        """
        reason = "mcp SDK >= 2 not installed; wire-protocol path is not exercisable"
        if os.environ.get("WELD_TEST_REQUIRE_MCP_SDK", "") != "1":
            self.skipTest(reason)
        version = f"{sys.version_info.major}.{sys.version_info.minor}"
        self.fail(
            f"{reason} -- failing per WELD_TEST_REQUIRE_MCP_SDK=1. Install a "
            f"2.x SDK where THIS interpreter reads it: {sys.executable} "
            f"(python {version}, user site {site.getusersitepackages()})."
        )

    def test_subprocess_with_sdk_lists_expected_tools(self) -> None:
        if not _mcp_sdk_available():
            self._skip_or_fail_without_sdk()

        # The context manager owns the fd lifecycle and the bounded shutdown
        # -- see mcp_stdio_client, and mcp_stdio_client_test for the pin.
        with _client.server_process() as proc:
            names = self._list_tools_over_stdio(proc)

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
    # The handshake this test asserts against. Spawn, teardown, and the
    # newline JSON-RPC framing itself live in mcp_stdio_client.
    # ------------------------------------------------------------------

    def _list_tools_over_stdio(self, proc: subprocess.Popen) -> frozenset[str]:
        """Drive the minimum JSON-RPC handshake and return advertised names."""
        _client.send(
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
        init_reply = _client.recv(proc)
        self.assertEqual(init_reply.get("id"), 1, f"bad initialize reply: {init_reply}")
        init_result = init_reply.get("result") or {}
        # Server identity on the wire. SDK 2.x reports the constructor
        # argument verbatim -- it dropped the 1.x fallback that quietly
        # filled this in -- so an unversioned server reaches clients as
        # weld version "". This is the one place that sees the real reply.
        server_info = init_result.get("serverInfo") or {}
        self.assertEqual(
            server_info.get("name"), "weld", f"bad serverInfo: {init_result}"
        )
        self.assertTrue(
            str(server_info.get("version") or "").strip(),
            f"initialize reply advertises a blank server version: {init_result}",
        )
        # The server must advertise the tools capability during initialize;
        # a client that does not see it will never send tools/list at all.
        capabilities = init_result.get("capabilities") or {}
        self.assertIn(
            "tools",
            capabilities,
            f"initialize reply does not advertise the tools capability: {init_reply}",
        )

        # Notify initialized. Notifications take no reply.
        _client.send(
            proc,
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
        )

        _client.send(
            proc,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        tools_reply = _client.recv(proc)
        self.assertEqual(tools_reply.get("id"), 2, f"bad tools/list reply: {tools_reply}")
        result = tools_reply.get("result") or {}
        tools = result.get("tools") or []
        return frozenset(t.get("name") for t in tools if t.get("name"))


class WeldMcpRequireSdkFlagTest(unittest.TestCase):
    """Pin both polarities of the require flag on every environment.

    The flag only changes behaviour on a host *without* a usable SDK --
    the one host that cannot also prove the flag still works. So drive the
    branch directly instead of waiting for an environment to supply it: a
    broken diagnostic would otherwise surface only when it is needed.
    """

    _CASE = "test_subprocess_with_sdk_lists_expected_tools"  # the gated test

    def _gate(self) -> None:
        WeldMcpSubprocessSmokeTest(self._CASE)._skip_or_fail_without_sdk()

    def test_unset_flag_skips(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WELD_TEST_REQUIRE_MCP_SDK", None)
            with self.assertRaises(unittest.SkipTest):
                self._gate()

    def test_set_flag_fails_with_a_diagnostic(self) -> None:
        with mock.patch.dict(os.environ, {"WELD_TEST_REQUIRE_MCP_SDK": "1"}):
            with self.assertRaises(AssertionError) as caught:
                self._gate()
        message = str(caught.exception)
        self.assertIn("WELD_TEST_REQUIRE_MCP_SDK=1", message)
        # The whole point of the diagnostic: name the interpreter that has
        # to see the SDK, since it is not the one that ran pip.
        self.assertIn(sys.executable, message)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
