"""Stdio entry point for the weld MCP server (optional ``mcp`` SDK).

Split out of :mod:`weld.mcp_server` to keep that module -- the SDK-free tool
adapters and registry -- under the line-count cap. Everything here is the
transport: it imports the optional ``mcp`` SDK lazily so importing
``weld.mcp_server`` never requires it, and every tool call is routed through
:func:`weld._mcp_dispatch.dispatch_to_text_payload` so a corrupt graph or a
bad call can never tear down the long-lived stdio session.

Targets the MCP SDK 2.x low-level ``Server.add_request_handler`` API (the
1.x ``@server.list_tools()`` / ``@server.call_tool()`` decorators were
removed in 2.0.0); the ``mcp`` extra is pinned to ``mcp>=2`` accordingly.

Imports :mod:`weld._mcp_dispatch` -- a sibling leaf, not the composition root
-- at module level; it never imports back, so this is not the cycle ADR 0130
disposition #7 broke. The one thing this module does *not* import, even
lazily, is :mod:`weld.mcp_server` itself: :func:`run_stdio` and :func:`main`
take the live tool registry as an optional *tools_provider* parameter instead,
dependency-injected by that composition root at its one real call site.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

from weld._mcp_dispatch import dispatch_to_text_payload as _dispatch_to_text_payload
from weld._mcp_sdk import HANDLER_API, installed_version, provides_handler_api
from weld._root_resolver import resolve_weld_root
from weld._safe_text import sanitize_terminal_text
from weld._version import weld_version

#: How each launch form spells itself. Both reach this module, and the help
#: each prints is a launch instruction, so neither may advertise the other's
#: spelling.
CONSOLE_PROG = "wd mcp serve"
MODULE_PROG = "python -m weld.mcp_server"

_HELP = """Usage: {prog} [ROOT]

Run the Weld MCP stdio server for ROOT. With ROOT omitted the root is
resolved from the current directory the way `wd` resolves it: the nearest
enclosing directory holding a .weld/, bounded by this git worktree, else
the worktree root. The stdio server requires the optional MCP SDK (mcp>=2,<3):

  pip install 'configflux-weld[mcp]'

The rest of the weld package, including `wd mcp config`, works without that
extra.
{note}"""

# Only the module form carries a note, and only because it is the form with
# a caveat: it is still supported, but it is no longer what clients are
# pointed at, and a reader who arrived here by typing it deserves to know why.
_MODULE_NOTE = """
Point MCP clients at `wd mcp serve` instead: it is the same server, and the
form `wd mcp config` generates. Being a console script, it never places the
directory it is launched in on the module search path, whereas `python -m`
places it there before any weld code can run.

`python -m weld.mcp_server` remains supported for running from a source
checkout, where it serves the checkout rather than an installed copy.
"""

_ABSENT_HINT = (
    "weld.mcp_server: the 'mcp' Python SDK is not installed. Install the "
    "optional extra with 'pip install \"configflux-weld[mcp]\"' to run the "
    "stdio server (weld requires mcp>=2). Original error: {detail}\n"
)

# Deliberately claims only what the probe observed -- that the API is not
# there -- rather than diagnosing a version. Something other than the SDK
# can still answer to `mcp`: a PYTHONPATH entry, a vendored copy, a partial
# install. (The launch directory is no longer one of them -- see
# weld/_launch_path.py.) Asserting "too old" at someone whose `pip show mcp`
# says 2.x would repeat the very error this message exists to fix.
_UNUSABLE_HINT = (
    "weld.mcp_server: the 'mcp' Python SDK is installed ({version}) but does "
    "not provide the MCP SDK 2.x API weld requires -- an SDK older than 2.0 "
    "is the usual cause. Upgrade it with 'pip install -U \"mcp>=2\"', or "
    "reinstall the extra with 'pip install -U \"configflux-weld[mcp]\"'. "
    "Detail: {detail}\n"
)


#: Reported when weld's own version cannot be resolved (a checkout with
#: neither distribution metadata nor a VERSION file). A version-shaped
#: placeholder beats both alternatives: SDK 2.x types serverInfo.version as
#: a plain ``str`` and copies it verbatim, so "" leaves clients displaying a
#: blank identity, and a word like "unknown" breaks any client or registry
#: that parses the field.
_UNKNOWN_WELD_VERSION = "0.0.0"


def _server_version() -> str:
    """Version weld identifies itself with in the ``initialize`` reply.

    SDK 1.x backfilled an unset version with the *SDK's* own version; 2.0
    removed that fallback and reports the constructor argument verbatim, so
    the server has to supply its own or advertise nothing at all.
    """
    return weld_version() or _UNKNOWN_WELD_VERSION


def _installed_sdk_version() -> str:
    """Best-effort version of the installed ``mcp`` distribution.

    Reads it through :mod:`weld._mcp_sdk`, the same accessor ``wd doctor``
    uses, so neither surface can end up naming a different version than the
    other for one environment.
    """
    return installed_version() or "version unknown"


def _unusable_hint(detail: object) -> str:
    """Render the unusable-SDK hint.

    *detail* is an exception (or a probe message about one), so it carries
    whatever the failing import put in its message -- typically a filesystem
    path. The escape stays at the two write sites rather than here: the
    ``weld._safe_text`` contract puts it at the boundary so a formatter stays a
    pure function of its payload.
    """
    return _UNUSABLE_HINT.format(version=_installed_sdk_version(), detail=detail)


def run_stdio(
    root: Path | str = ".", *, tools_provider: Callable[[], list] | None = None,
) -> int:
    """Run the stdio MCP server loop.

    Imports the ``mcp`` SDK lazily so the rest of the package stays usable
    without it. Absence is probed separately from usability, and the two
    report different remedies: someone who has installed the SDK must never
    be told to install it, because the fix for them is an upgrade.

    *tools_provider* is the live tool registry (``weld.mcp_server.build_tools``),
    dependency-injected by the composition root rather than imported here
    (ADR 0130 disposition #7). It defaults to ``None`` and is validated only
    once the SDK is confirmed usable -- below, right before it is first
    called -- so a caller exercising only the absent/unusable-SDK paths (as
    the guard tests do) never needs to supply one.
    """
    try:
        import mcp  # type: ignore # noqa: F401  (presence probe: absent vs unusable)
    except ImportError as exc:
        sys.stderr.write(sanitize_terminal_text(_ABSENT_HINT.format(detail=exc)))
        return 2

    try:
        from mcp.server import Server  # type: ignore
        from mcp.server.stdio import stdio_server  # type: ignore
        from mcp.types import (  # type: ignore
            CallToolRequestParams,
            CallToolResult,
            ListToolsResult,
            PaginatedRequestParams,
            TextContent,
            Tool as McpTool,
        )
    except ImportError as exc:
        # The package imports but does not expose what weld needs: an old or
        # partial SDK, or something else on sys.path named 'mcp'. Whichever it
        # is, the install is present, so the absent-SDK remedy is the wrong one.
        sys.stderr.write(sanitize_terminal_text(_unusable_hint(exc)))
        return 2

    if not provides_handler_api(Server):
        # An SDK older than 2.0 still exports every type imported above but
        # not the handler-registration API, so it has to be caught by feature
        # probe rather than by ImportError -- and reported as the upgrade it
        # is, instead of an AttributeError mid-startup. The probe lives in
        # weld._mcp_sdk beside the version floor `wd doctor` reads, so the
        # two surfaces cannot drift apart on what "usable" means.
        sys.stderr.write(
            sanitize_terminal_text(
                _unusable_hint(f"Server.{HANDLER_API} is unavailable")
            )
        )
        return 2

    if tools_provider is None:
        # Every real caller supplies this (weld.mcp_server.run_stdio/main
        # inject build_tools); reaching this with none means a caller bug,
        # not a state this function should paper over by importing
        # weld.mcp_server itself -- that import is exactly the cycle ADR
        # 0130 disposition #7 broke.
        raise TypeError(
            "run_stdio() requires tools_provider once a usable SDK is "
            "present: the caller must supply the live tool registry (see "
            "weld.mcp_server.build_tools)."
        )

    import asyncio

    # `version` is keyword-only in SDK 2.x and lands in serverInfo unchanged.
    server: Server = Server("weld", version=_server_version())
    tools = tools_provider()

    async def _list_tools(
        ctx: object, params: PaginatedRequestParams
    ) -> ListToolsResult:  # pragma: no cover - requires sdk
        # Registering "tools/list" is also what advertises the ``tools``
        # capability during initialize, so this handler must stay wired even
        # if the tool registry is ever empty.
        return ListToolsResult(
            tools=[
                McpTool(
                    name=t.name,
                    description=t.description,
                    input_schema=t.input_schema,
                )
                for t in tools
            ]
        )

    async def _call_tool(
        ctx: object, params: CallToolRequestParams
    ) -> CallToolResult:  # pragma: no cover - requires sdk
        # _dispatch_to_text_payload converts every failure -- unknown tool,
        # corrupt/unsupported graph, or any unexpected exception -- into a
        # JSON error payload so a single bad call can never crash the
        # long-lived stdio transport (previously a JSONDecodeError escaped).
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=_dispatch_to_text_payload(
                        params.name, dict(params.arguments or {}), root=root,
                        tools_provider=tools_provider,
                    ),
                )
            ]
        )

    server.add_request_handler("tools/list", PaginatedRequestParams, _list_tools)
    server.add_request_handler("tools/call", CallToolRequestParams, _call_tool)

    async def _main() -> None:  # pragma: no cover - requires sdk
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    asyncio.run(_main())
    return 0


def _help_text(prog: str) -> str:
    """Return the ``--help`` body for the launch form spelled *prog*."""
    return _HELP.format(
        prog=prog, note=_MODULE_NOTE if prog == MODULE_PROG else ""
    )


def main(
    argv: list[str] | None = None, *, prog: str = MODULE_PROG,
    tools_provider: Callable[[], list] | None = None,
) -> int:
    """Entry point shared by both launch forms.

    ``python -m weld.mcp_server`` and ``wd mcp serve`` both reach it through
    :mod:`weld.mcp_server`'s own ``main``/``run_stdio`` wrappers, which pass
    their own *prog* (so the help printed names the command the reader typed)
    and inject *tools_provider* (ADR 0130 disposition #7). Everything below
    the argument handling is identical, because the two differ only in how
    the interpreter was started -- not in what is served.

    The launch root goes through the same core resolver the CLI uses, so a
    server started from a subdirectory serves its *checkout* rather than the
    one directory it happened to start in -- which held no ``.weld/`` and so
    answered nothing. An explicit ROOT argument still wins outright.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in {"-h", "--help"}:
        sys.stdout.write(sanitize_terminal_text(_help_text(prog)))
        return 0
    return run_stdio(
        resolve_weld_root(args[0] if args else None), tools_provider=tools_provider,
    )
