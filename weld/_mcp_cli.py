"""The ``wd mcp`` subcommand namespace.

Two subcommands that have to agree with each other. ``serve`` runs the stdio
MCP server; ``config`` renders the per-client JSON snippet that points a
client at it, and that snippet names ``wd mcp serve``. A namespace that
stopped routing the name the renderer emits would not fail here -- it would
fail later, in a user's client, as a server that never starts. The dispatch
table below and :data:`weld.mcp_config._SERVER_ENTRY` are two halves of one
contract; ``weld_mcp_config_test`` asserts they still line up.

Kept out of :mod:`weld.mcp_config`, which owns the renderer: a config
generator is the wrong home for the server's launch path, and holding both
in one module left neither any room under the line-count cap.

Both subcommands are imported only when asked for. ``wd mcp config`` is
documented as working without the optional MCP SDK, and the transport module
is the one that reaches for it.
"""

from __future__ import annotations

import sys

#: Listed in the order a reader most likely wants them, not alphabetically:
#: ``serve`` is the command, ``config`` is the thing that writes it down.
_SUBCOMMANDS = ("serve", "config")

_USAGE = """Usage: wd mcp <subcommand> [args]

Subcommands:
  serve    Run the Weld MCP stdio server (see wd mcp serve --help)
  config   Generate a per-client MCP config snippet
           (see wd mcp config --help)
"""


def main(argv: list[str]) -> int:
    """Dispatch ``wd mcp <subcommand>``. Returns a process exit code."""
    if not argv or argv[0] in {"-h", "--help"}:
        sys.stdout.write(_USAGE)
        return 0

    sub, rest = argv[0], argv[1:]

    if sub == "serve":
        from weld import mcp_server
        from weld._mcp_stdio import CONSOLE_PROG

        return mcp_server.main(rest, prog=CONSOLE_PROG)

    if sub == "config":
        from weld import mcp_config

        return mcp_config.cli_main(rest)

    names = ", ".join(_SUBCOMMANDS)
    sys.stderr.write(
        f"error: unknown wd mcp subcommand: {sub!r}. "
        f"Supported subcommands: {names}.\n"
    )
    return 2
