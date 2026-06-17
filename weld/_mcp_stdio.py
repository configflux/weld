"""Stdio entry point for the weld MCP server (optional ``mcp`` SDK).

Split out of :mod:`weld.mcp_server` to keep that module -- the SDK-free tool
adapters and registry -- under the line-count cap. Everything here is the
transport: it imports the optional ``mcp`` SDK lazily so importing
``weld.mcp_server`` never requires it, and every tool call is routed through
:func:`weld.mcp_server.dispatch_to_text_payload` so a corrupt graph or a bad
call can never tear down the long-lived stdio session.
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
        from mcp.types import TextContent, Tool as McpTool  # type: ignore
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

    @server.list_tools()  # type: ignore[misc]
    async def _list_tools() -> list[McpTool]:  # pragma: no cover - requires sdk
        return [
            McpTool(
                name=t.name,
                description=t.description,
                inputSchema=t.input_schema,
            )
            for t in tools
        ]

    @server.call_tool()  # type: ignore[misc]
    async def _call_tool(
        name: str, arguments: dict | None
    ) -> list[TextContent]:  # pragma: no cover - requires sdk
        # dispatch_to_text_payload converts every failure -- unknown tool,
        # corrupt/unsupported graph, or any unexpected exception -- into a
        # JSON error payload so a single bad call can never crash the
        # long-lived stdio transport (previously a JSONDecodeError escaped).
        return [
            TextContent(
                type="text",
                text=dispatch_to_text_payload(name, arguments, root=root),
            )
        ]

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
