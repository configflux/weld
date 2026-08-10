"""Stdio entry point for the weld MCP server (optional ``mcp`` SDK).

Split out of :mod:`weld.mcp_server` to keep that module -- the SDK-free tool
adapters and registry -- under the line-count cap. Everything here is the
transport: it imports the optional ``mcp`` SDK lazily so importing
``weld.mcp_server`` never requires it, and every tool call is routed through
:func:`weld.mcp_server.dispatch_to_text_payload` so a corrupt graph or a bad
call can never tear down the long-lived stdio session.

Targets the MCP SDK 2.x low-level ``Server.add_request_handler`` API (the
1.x ``@server.list_tools()`` / ``@server.call_tool()`` decorators were
removed in 2.0.0); the ``mcp`` extra is pinned to ``mcp>=2`` accordingly.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HELP = """Usage: python -m weld.mcp_server [ROOT]

Run the Weld MCP stdio server for ROOT, or the current directory when ROOT
is omitted. The stdio server requires the optional MCP SDK:

  pip install 'configflux-weld[mcp]'

The rest of the weld package, including `wd mcp config`, works without that
extra.
"""


def run_stdio(root: Path | str = ".") -> int:
    """Run the stdio MCP server loop.

    Imports the ``mcp`` SDK lazily so the rest of the package stays usable
    without it.
    """
    from weld.mcp_server import build_tools, dispatch_to_text_payload

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

        if not hasattr(Server, "add_request_handler"):
            # An SDK older than 2.0 still exports every type used above but
            # not the handler-registration API, so surface it as the same
            # install-hint path instead of an AttributeError mid-startup.
            raise ImportError(
                "the installed 'mcp' SDK predates 2.0 "
                "(Server.add_request_handler is missing); weld requires mcp>=2"
            )
    except ImportError as exc:  # pragma: no cover - exercised only with extras
        sys.stderr.write(
            "weld.mcp_server: the 'mcp' Python SDK is not installed. "
            "Install the optional extra with "
            "'pip install \"configflux-weld[mcp]\"' to run the "
            f"stdio server. Original error: {exc}\n"
        )
        return 2

    import asyncio

    server: Server = Server("weld")
    tools = build_tools()

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
        # dispatch_to_text_payload converts every failure -- unknown tool,
        # corrupt/unsupported graph, or any unexpected exception -- into a
        # JSON error payload so a single bad call can never crash the
        # long-lived stdio transport (previously a JSONDecodeError escaped).
        return CallToolResult(
            content=[
                TextContent(
                    type="text",
                    text=dispatch_to_text_payload(
                        params.name, dict(params.arguments or {}), root=root
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


def main(argv: list[str] | None = None) -> int:
    """Module entry point: ``python -m weld.mcp_server``."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in {"-h", "--help"}:
        sys.stdout.write(_HELP)
        return 0
    root = Path(args[0]) if args else Path(".")
    return run_stdio(root)
