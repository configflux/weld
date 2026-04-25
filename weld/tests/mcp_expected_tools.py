"""Single source of truth for the expected weld MCP tool name set in tests.

Three test modules pin the public MCP tool surface:

* ``weld_mcp_smoke_test`` -- registry + wire-protocol smoke
* ``weld_mcp_server_test`` -- in-process adapter registry
* ``weld_mcp_interaction_test`` -- registry count after the ``weld_enrich`` add

Before this fixture, each test embedded its own copy of the 13-name set, so
adding or renaming a tool meant editing three identical literals. They are
consolidated here so a tool surface change only needs:

  1. ``weld/_mcp_tools.py::build_tools``
  2. ``docs/mcp.md`` (the "Exposed tools" table)
  3. this constant

The constant is a ``frozenset`` so it is hashable and cheap to compare with
equality, and immutable so a test cannot mutate it across modules.
"""

from __future__ import annotations

EXPECTED_TOOL_NAMES: frozenset[str] = frozenset(
    {
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
    }
)
"""The 13 tool names exposed by the weld MCP server registry."""
