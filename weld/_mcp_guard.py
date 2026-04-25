"""Missing-graph guard for the weld MCP server.

When ``.weld/graph.json`` is absent, the underlying ``Graph`` silently
constructs an empty in-memory graph and graph-backed read tools return
empty payloads. That is hostile to MCP clients: agents that prompted
``weld_query`` get back an empty match list with no signal that the
graph has not been built. The CLI already handles this via
:func:`weld._graph_cli.ensure_graph_exists` -- this module mirrors that
contract at the MCP boundary so both surfaces emit identical guidance.

Federated workspaces (``.weld/workspaces.yaml`` present at root) are
exempt: the federation loader reports per-child status via
``children_status``. ``weld_find`` is also exempt because it reads the
file index, not the graph (matches CLI behavior).
"""

from __future__ import annotations

from pathlib import Path

from weld.workspace_state import find_workspaces_yaml as _find_workspaces_yaml


def missing_graph_payload(retry_cmd: str = "weld_query / weld_context / ...") -> dict:
    """Structured actionable-error payload for missing graphs.

    Wording mirrors :func:`weld._graph_cli.missing_graph_message`; the
    stable ``error_code`` lets MCP clients render the hint without
    parsing the human-readable message.
    """
    return {
        "error": "No Weld graph found.",
        "error_code": "graph_missing",
        "hint": "Run: wd init (if no config), then wd discover.",
        "retry": f"Then retry: {retry_cmd}.",
    }


def graph_present(root: Path | str) -> bool:
    """Return ``True`` when graph-backed MCP tools can safely load *root*.

    Single-repo root requires ``.weld/graph.json``. A federated root
    (``.weld/workspaces.yaml`` present) is always considered ready --
    the federation layer reports per-child status separately. Callers
    that hit ``False`` should short-circuit with
    :func:`missing_graph_payload`.
    """
    root_path = Path(root)
    if (root_path / ".weld" / "graph.json").exists():
        return True
    if _find_workspaces_yaml(root_path) is not None:
        return True
    return False
