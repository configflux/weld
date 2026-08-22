"""Optional-SDK paths of the MCP stdio server: refuse, or start as *what*.

``run_stdio`` refuses to start whenever the optional SDK is unusable, and the
reason decides the remedy:

* no ``mcp`` SDK at all -> install the extra;
* an SDK predating the 2.x handler API weld targets -> upgrade the SDK;
* something else on ``sys.path`` answering to ``mcp`` -> also not an install
  problem, and not a version we can name.

Only the first is an install problem. Reporting the others as one told users
who *had* installed the SDK to install it again, so these tests pin each
branch to the remedy that actually applies to it -- and pin the negative:
no branch may claim an installed SDK is missing.

The remaining case is a usable SDK, where the question stops being whether
the server starts and becomes what it tells clients it is: SDK 2.x reports
``serverInfo.version`` verbatim from the constructor, so an unversioned
``Server`` advertises weld as version ``""`` to every client that logs or
displays server identity.

Every case is simulated through ``sys.modules`` injection rather than through
the ambient environment, so the assertions hold identically whether the
runfiles carry a supported SDK, a pre-2.0 SDK, or none at all. The subprocess
counterpart in ``weld_mcp_smoke_test`` covers the real process exit path for
whichever branch the ambient environment happens to produce, and the real
wire-level ``initialize`` reply wherever a supported SDK exists.
"""

from __future__ import annotations

import contextlib
import io
import sys
import types
import unittest
from unittest import mock

from weld import _mcp_stdio
from weld._version import weld_version

# Every module name run_stdio imports from the optional SDK. The guard is
# only meaningful if all of them are controlled together: leaving a real
# submodule behind would let the ambient install leak into a simulated run.
_MCP_MODULES = ("mcp", "mcp.server", "mcp.server.stdio", "mcp.types")

# mcp.types names imported by run_stdio. A pre-2.0 SDK still exports all of
# them -- that is precisely why the version check cannot be an ImportError.
_MCP_TYPE_NAMES = (
    "CallToolRequestParams",
    "CallToolResult",
    "ListToolsResult",
    "PaginatedRequestParams",
    "TextContent",
    "Tool",
)

_MISSING = object()


@contextlib.contextmanager
def _mcp_modules(entries: dict[str, object | None]):
    """Swap the ``mcp`` module tree for *entries*, then restore it.

    A ``None`` value is the documented way to make ``import <name>`` raise
    ``ImportError`` even when the real package is installed, which is how the
    "SDK absent" branch stays testable on a machine that has the SDK.
    """
    saved = {name: sys.modules.get(name, _MISSING) for name in _MCP_MODULES}
    try:
        for name in _MCP_MODULES:
            sys.modules.pop(name, None)
        sys.modules.update(entries)
        yield
    finally:
        for name in _MCP_MODULES:
            original = saved[name]
            if original is _MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


def _absent_sdk() -> dict[str, object | None]:
    """Module map under which importing ``mcp`` fails."""
    return dict.fromkeys(_MCP_MODULES, None)


def _sdk_module_map(server_cls: type, stdio_server: object) -> dict[str, object]:
    """Assemble an importable ``mcp`` module tree around *server_cls*.

    The installed-SDK fakes differ only in the ``Server`` they expose and in
    what ``stdio_server`` does, so the rest -- the four modules, the
    ``mcp.types`` names ``run_stdio`` imports, the parent/child wiring --
    lives here where it cannot drift between them.
    """
    package = types.ModuleType("mcp")
    server = types.ModuleType("mcp.server")
    stdio = types.ModuleType("mcp.server.stdio")
    sdk_types = types.ModuleType("mcp.types")

    server.Server = server_cls  # type: ignore[attr-defined]
    stdio.stdio_server = stdio_server  # type: ignore[attr-defined]
    for name in _MCP_TYPE_NAMES:
        setattr(sdk_types, name, type(name, (), {}))
    package.server = server  # type: ignore[attr-defined]
    package.types = sdk_types  # type: ignore[attr-defined]
    server.stdio = stdio  # type: ignore[attr-defined]

    return {
        "mcp": package,
        "mcp.server": server,
        "mcp.server.stdio": stdio,
        "mcp.types": sdk_types,
    }


def _pre_2_sdk() -> dict[str, object | None]:
    """Module map imitating an installed SDK older than 2.0.

    Everything ``run_stdio`` imports resolves; only the 2.0
    ``Server.add_request_handler`` registration API is absent.
    """

    class _LegacyServer:
        def __init__(self, name: str) -> None:
            self.name = name

    return _sdk_module_map(_LegacyServer, lambda: None)


def _shadowed_sdk() -> dict[str, object | None]:
    """Module map imitating something other than the SDK named ``mcp``.

    ``python -m`` puts the launch directory on ``sys.path``, so a repository
    carrying its own ``mcp/`` package or ``mcp.py`` shadows the real SDK for
    the server process: ``import mcp`` succeeds while ``mcp.server`` does not
    exist.
    """
    package = types.ModuleType("mcp")
    return {"mcp": package, "mcp.server": None, "mcp.server.stdio": None,
            "mcp.types": None}


def _supported_sdk(constructed: list[object]) -> dict[str, object | None]:
    """Module map imitating a supported (2.x) SDK, recording each ``Server``.

    Only the surface ``run_stdio`` touches is modelled: handler registration,
    the initialization options the SDK derives from the constructor, and an
    ``async with stdio_server()`` that hands back a transport pair and
    returns immediately, so the server loop completes instead of blocking on
    a real stdin.
    """

    class _Server:
        def __init__(self, name: str, **kwargs: object) -> None:
            # `name` is positional and everything else keyword-only in SDK
            # 2.x, so a version passed positionally would fail right here.
            self.name = name
            self.kwargs = dict(kwargs)
            self.handlers: dict[str, object] = {}
            constructed.append(self)

        def add_request_handler(
            self, method: str, params_type: object, handler: object
        ) -> None:
            self.handlers[method] = handler

        def create_initialization_options(self) -> dict[str, object]:
            # SDK 2.x builds serverInfo straight from the constructor with
            # no fallback of its own (1.x substituted the SDK's own version
            # here). Mirroring that is what makes the assertion about what
            # clients see rather than about a constructor argument.
            return {
                "server_name": self.name,
                "server_version": self.kwargs.get("version", ""),
            }

        async def run(self, read: object, write: object, options: object) -> None:
            self.ran_with = options

    @contextlib.asynccontextmanager
    async def _stdio_server():
        yield ("read-stream", "write-stream")

    return _sdk_module_map(_Server, _stdio_server)


class StdioSdkGuardTest(unittest.TestCase):
    """Each unusable-SDK state must name its own remedy."""

    def _run(self, entries: dict[str, object | None]) -> tuple[int, str]:
        stderr = io.StringIO()
        with _mcp_modules(entries), contextlib.redirect_stderr(stderr):
            code = _mcp_stdio.run_stdio(".")
        return code, stderr.getvalue()

    def test_absent_sdk_exits_2_and_points_at_the_extra(self) -> None:
        code, stderr = self._run(_absent_sdk())

        self.assertEqual(code, 2, f"expected exit 2, got {code}; stderr={stderr!r}")
        self.assertIn("not installed", stderr)
        self.assertIn('configflux-weld[mcp]', stderr)

    def test_pre_2_sdk_exits_2_and_points_at_an_upgrade(self) -> None:
        code, stderr = self._run(_pre_2_sdk())

        self.assertEqual(code, 2, f"expected exit 2, got {code}; stderr={stderr!r}")
        self.assertIn("mcp>=2", stderr)
        self.assertIn("pip install -U", stderr)

    def test_pre_2_sdk_does_not_claim_the_sdk_is_missing(self) -> None:
        # The regression this issue is about: an installed-but-old SDK was
        # reported as "not installed", sending users to reinstall an extra
        # they already had instead of upgrading the SDK they already had.
        _, stderr = self._run(_pre_2_sdk())

        self.assertNotIn("not installed", stderr)
        self.assertNotIn("is missing.", stderr)

    def test_shadowed_sdk_is_not_reported_as_absent_or_dated(self) -> None:
        # An `mcp/` directory in the server's launch directory shadows the
        # real SDK. The install is present, so "not installed" is wrong; the
        # version is unknowable from here, so asserting one would be too.
        code, stderr = self._run(_shadowed_sdk())

        self.assertEqual(code, 2, f"expected exit 2, got {code}; stderr={stderr!r}")
        self.assertNotIn("not installed", stderr)
        self.assertIn("does not provide", stderr)
        # The tail has to carry what the probe actually saw, since that is the
        # only thing separating a shadowed SDK from an outdated one.
        self.assertIn("mcp.server", stderr)

    def test_both_branches_name_the_sdk_and_the_module(self) -> None:
        for label, entries in (("absent", _absent_sdk()), ("pre-2.0", _pre_2_sdk())):
            with self.subTest(sdk=label):
                code, stderr = self._run(entries)
                self.assertEqual(code, 2)
                self.assertIn("weld.mcp_server:", stderr)
                self.assertIn("'mcp'", stderr)
                self.assertTrue(
                    stderr.endswith("\n"), f"stderr must end in a newline: {stderr!r}"
                )


def _no_tools() -> list:
    """Fake ``tools_provider`` for tests below the SDK-usability gate.

    Every case in this class asserts on server identity, version, or handler
    *registration* -- never on tool content -- so an empty registry is a
    faithful stand-in for the real ``weld.mcp_server.build_tools`` these
    tests deliberately avoid depending on (ADR 0130 disposition #7).
    """
    return []


class StdioServerIdentityTest(unittest.TestCase):
    """A server that starts must say which weld it is."""

    def _start(self) -> list:
        constructed: list = []
        with _mcp_modules(_supported_sdk(constructed)):
            code = _mcp_stdio.run_stdio(".", tools_provider=_no_tools)
        self.assertEqual(code, 0, "run_stdio must succeed on a supported SDK")
        self.assertEqual(len(constructed), 1, "expected exactly one Server")
        return constructed

    def test_server_is_constructed_with_a_non_empty_version(self) -> None:
        # The regression: SDK 2.x dropped the fallback that used to fill this
        # in, so an unversioned Server advertises weld as version "".
        server = self._start()[0]

        self.assertEqual(server.name, "weld")
        self.assertIn(
            "version",
            server.kwargs,
            "Server must be given an explicit version; SDK 2.x supplies none",
        )
        version = server.kwargs["version"]
        self.assertIsInstance(version, str)
        self.assertTrue(version.strip(), "serverInfo.version must not be blank")

    def test_advertised_version_is_the_resolved_weld_version(self) -> None:
        # Identity has to match what every other weld surface reports; a
        # version invented here would be worse than the blank one.
        server = self._start()[0]

        self.assertEqual(server.kwargs["version"], weld_version() or "0.0.0")

    def test_initialization_options_carry_the_version(self) -> None:
        # End of the chain the client actually reads: constructor -> options
        # -> serverInfo in the initialize reply.
        server = self._start()[0]

        options = server.create_initialization_options()

        self.assertEqual(options["server_name"], "weld")
        self.assertTrue(str(options["server_version"]).strip())

    def test_unresolvable_version_still_starts_with_a_parseable_placeholder(
        self,
    ) -> None:
        # A partial checkout has neither distribution metadata nor a VERSION
        # file. Refusing to start would be a far worse outcome than a
        # placeholder, and the placeholder still has to parse as a version
        # for clients that compare them.
        constructed: list = []
        with mock.patch.object(_mcp_stdio, "weld_version", return_value=None):
            with _mcp_modules(_supported_sdk(constructed)):
                code = _mcp_stdio.run_stdio(".", tools_provider=_no_tools)

        self.assertEqual(code, 0)
        self.assertEqual(constructed[0].kwargs["version"], "0.0.0")

    def test_both_tool_handlers_are_registered(self) -> None:
        # Registering "tools/list" is what advertises the tools capability
        # during initialize; losing it while gaining a version would trade
        # one identity bug for a worse one.
        server = self._start()[0]

        self.assertEqual(set(server.handlers), {"tools/list", "tools/call"})


class RunStdioRequiresToolsProviderTest(unittest.TestCase):
    """A caller past the SDK gate that forgets ``tools_provider`` fails
    loudly and by name, not with a bare ``NoneType is not callable`` -- and
    not by silently reaching back into :mod:`weld.mcp_server` for
    ``build_tools``, which is exactly the import ADR 0130 disposition #7
    replaced with this parameter."""

    def test_missing_tools_provider_raises_past_the_sdk_gate(self) -> None:
        with _mcp_modules(_supported_sdk([])):
            with self.assertRaises(TypeError) as ctx:
                _mcp_stdio.run_stdio(".")
        self.assertIn("tools_provider", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
