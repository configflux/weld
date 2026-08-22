"""Graph-load guards for the weld MCP server.

When ``.weld/graph.json`` is absent, the underlying ``Graph`` silently
constructs an empty in-memory graph and graph-backed read tools return
empty payloads. That is hostile to MCP clients: agents that prompted
``weld_query`` get back an empty match list with no signal that the
graph has not been built. The CLI already handles this via
:func:`weld._graph_cli.ensure_graph_exists` -- this module mirrors that
contract at the MCP boundary so both surfaces emit identical guidance.

It also owns the boundary that decides *which checkout* a request is answered
from (:func:`resolve_dispatch_root`, ADR 0096 §4) -- the one place where a
request-supplied root, which is untrusted input, is bounded and then seeded
exactly as ``ensure_graph_exists`` seeds the CLI's.

It also owns the boundary that converts a graph that exists but cannot be
loaded -- a corrupt/truncated ``graph.json`` (``json.JSONDecodeError``), a
non-file left at the graph path such as a directory (``IsADirectoryError``),
or one written by a newer Weld (``SchemaVersionError``) -- into the same
shared structured payload instead of letting the exception escape as a
transport crash (:func:`load_error_payload`), and the last-resort serializer
that guarantees the stdio session never dies on a single bad call
(:func:`serialize_dispatch`).

Federated workspaces (``.weld/workspaces.yaml`` present at root) are
exempt: the federation loader reports per-child status via
``children_status``. ``weld_find`` is also exempt because it reads the
file index, not the graph (matches CLI behavior).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from weld._errors import (
    ERROR_HINTS,
    GRAPH_MISSING,
    NODE_NOT_FOUND,
    ROOT_OUT_OF_BOUNDS,
    classify_graph_load_error,
    structured_payload,
)
from weld._safe_text import dumps_safe_json
from weld.workspace_state import find_workspaces_yaml as _find_workspaces_yaml


def missing_graph_payload(retry_cmd: str = "weld_query / weld_context / ...") -> dict:
    """Structured actionable-error payload for missing graphs.

    Single-sourced from :func:`weld._errors.structured_payload` so the
    ``error_code`` / ``hint`` vocabulary stays identical to the CLI surface
    and to the other graph-load failure codes (``graph_corrupt`` /
    ``schema_mismatch``). Wording mirrors
    :func:`weld._graph_cli.missing_graph_message`; the stable ``error_code``
    lets MCP clients render the hint without parsing the human-readable
    message.
    """
    return structured_payload(GRAPH_MISSING, retry_cmd=retry_cmd)


def resolve_dispatch_root(
    tool_name: str, args: dict, server_root: Path | str,
) -> tuple[Path | None, dict | None]:
    """Take a request's ``root`` out of *args* and decide where to answer from.

    Returns ``(resolved_root, None)`` when the request may be served, or
    ``(None, payload)`` when it may not -- the caller returns *payload*
    unchanged. Two results rather than an exception because a refusal is a
    normal answer on this surface: the stdio session must keep running.

    The pop is not incidental. Every tool handler already declares ``root``
    as a keyword parameter, so leaving it in the argument mapping makes the
    call ``handler(root=..., root=...)`` -- a ``TypeError`` that would read
    to a client as "this tool is broken" rather than "that argument moved".
    *args* is mutated in place, so callers pass the copy they own.

    A ``root`` sent to a tool in
    :data:`weld._mcp_tools.ROOTLESS_TOOLS` is refused rather than honoured.
    Those two tools *write*, and their schemas already decline the argument
    -- but schema validation happens in the client, so without this check a
    caller that skips it (or declines to) could steer a graph mutation at a
    checkout the operator never pointed the server at. The bound below would
    still confine the damage to the same repository; that is not a good
    enough reason to let a documented invariant depend on someone else's
    validator.

    Bounding is delegated wholesale to
    :func:`weld._root_resolver.resolve_request_root`: the requested path must
    be an existing directory sharing a ``--git-common-dir`` with
    *server_root*, normalized through ``realpath`` (which is what defeats
    ``..``). ``None`` -- the ordinary case -- means the server's own root.

    The refusal payload is a **constant**. It names no path, and it is the
    same for a directory outside the repository, a regular file, and a path
    that does not exist, so a caller cannot use the difference to learn what
    exists on the server's filesystem.

    Seeding then runs on the accepted root, mirroring
    :func:`weld._graph_cli.ensure_graph_exists`: this is the MCP surface's
    single funnel, so it is where a fresh checkout gets the ``.weld/`` state
    its tracked graph arrived without (ADR 0096 §2). It is safe to repeat --
    a warm root costs two ``stat`` calls -- and it degrades to a no-op rather
    than raising, so a bootstrap that cannot run never fails a read.
    """
    from weld._mcp_tools import ROOTLESS_TOOLS
    from weld._root_resolver import RootOutOfBoundsError, resolve_request_root
    from weld._worktree_seed import ensure_seeded

    requested = args.pop("root", None)
    if requested is not None and tool_name in ROOTLESS_TOOLS:
        return None, structured_payload(
            ROOT_OUT_OF_BOUNDS,
            detail=(
                f"{tool_name} writes, so it does not accept root; it acts on "
                "the root the server was started against."
            ),
        )
    try:
        resolved = resolve_request_root(requested, server_root)
    except RootOutOfBoundsError:
        return None, structured_payload(ROOT_OUT_OF_BOUNDS)
    ensure_seeded(resolved)
    return resolved, None


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


def load_error_payload(exc: BaseException, root: Path | str) -> dict | None:
    """Return a structured payload for a graph-load exception, else ``None``.

    A ``json.JSONDecodeError`` (corrupt/truncated graph) or a
    ``SchemaVersionError`` (graph from a newer Weld) raised from a tool
    handler becomes the shared ``error_code`` + ``hint`` payload. Any other
    exception returns ``None`` so the caller re-raises it unchanged -- an
    unrelated bug must not be mislabeled as a graph problem.
    """
    code, detail = classify_graph_load_error(exc, Path(root))
    if code is None:
        return None
    return structured_payload(code, detail=detail)


def stamp_node_not_found(result: dict) -> dict:
    """Add the shared ``node_not_found`` ``error_code`` + ``hint`` in place.

    ``Graph.context`` / ``Graph.callers`` (and the federated variants) report
    an unknown node id as ``{"error": "node not found: <id>", ...}`` with no
    machine-readable code. This mirrors :func:`weld._graph_cli_errors.\
emit_node_lookup` -- the CLI's ``context`` / ``callers`` commands stamp the
    same :data:`weld._errors.NODE_NOT_FOUND` code -- so both surfaces emit an
    identical code an agent can branch on.

    The predicate is the CLI's: a dict whose ``error`` is a string containing
    ``"not found"``. That deliberately excludes ``weld_path`` (which signals a
    miss via ``{"path": None, "reason": ...}`` and is *not* CLI-stamped) and
    the federated ``child not available`` error, keeping exact parity. The
    existing human-readable ``error`` text and any sibling fields (``symbol``,
    ``callers``, ``edges``) are preserved -- the code/hint are purely additive,
    and the only id echoed is the caller-supplied one already in ``error``.
    Already-stamped or non-error payloads pass through untouched.
    """
    if not isinstance(result, dict) or "error_code" in result:
        return result
    error = result.get("error")
    if isinstance(error, str) and "not found" in error:
        result["error_code"] = NODE_NOT_FOUND
        result["hint"] = ERROR_HINTS[NODE_NOT_FOUND]
    return result


def serialize_dispatch(
    dispatch: Callable[..., dict],
    tool_name: str,
    arguments: dict | None,
    root: Path | str,
) -> str:
    """Run *dispatch* and serialize the result (or error) to a JSON string.

    SDK-free seam for the stdio ``_call_tool`` handler. It guarantees the
    transport never crashes: graph-load failures are already converted to a
    structured payload upstream, an unknown tool name (``KeyError``) becomes
    an ``{"error": ...}`` payload, and any other unexpected exception is also
    serialized rather than torn through the long-lived stdio session.

    Serialization goes through :func:`weld._safe_text.dumps_safe_json`, the
    same emitter the CLI's ``--json`` writers use. That is what keeps the two
    surfaces byte-identical: the DEL/C1 escape had to move on both at once or
    not at all, since a divergence here would break the ADR 0083 parity
    contract. The escape is encoding-only, so the value an agent parses out is
    unchanged.
    """
    try:
        result: Any = dispatch(tool_name, arguments, root=root)
    except KeyError as exc:
        result = {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - last-resort transport guard
        result = {"error": f"{type(exc).__name__}: {exc}"}
    return dumps_safe_json(result)
