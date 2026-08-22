"""Federated CLI dispatch for the graph read commands.

Entered by :func:`weld._graph_cli.main` when ``.weld/workspaces.yaml`` is
present, so a polyrepo root serves every read command across its children:

* ``query`` / ``context`` / ``path`` navigate a :class:`~weld.federation.
  FederatedGraph` (ADR 0011, ADR 0081).
* ``callers`` / ``references`` reuse the established per-child fan-out
  (:mod:`weld.federation_tools`) -- the same helpers the MCP surface uses, so
  ``wd callers --json`` == ``weld_callers`` (ADR 0083).
* ``communities`` runs over the read-time flattened union (ADR 0089).
* ``find`` fans out across every child's file-index (ADR 0089).

Kept out of ``weld/_graph_cli.py`` so that module stays within the 400-line cap;
the ``emit`` writers are passed in rather than imported, so the dependency runs
one way only -- this module never reaches back into the dispatcher that calls it.
"""

from __future__ import annotations

from weld._query_surface import (
    apply_callers_envelope,
    apply_context_envelope,
    apply_references_envelope,
)
from weld._query_surface import apply_query_envelope as _query_envelope
from weld.federation import FederatedGraph

#: Read commands this dispatcher serves at a federated root.
FEDERATED_CLI_COMMANDS = frozenset(
    {"query", "context", "path", "callers", "references", "communities", "find"}
)


def run_federated_cli(cmd, args, *, emit, emit_node_lookup) -> None:
    """Serve one federated read *cmd*. ``emit`` / ``emit_node_lookup`` are the
    renderer-aware writers from :mod:`weld._graph_cli_emit`, injected by
    :func:`weld._graph_cli.main`."""
    if cmd == "find":
        # File-index, not graph -- no FederatedGraph needed.
        from weld._cli_render import render_find
        from weld._federation_find import federated_find

        emit(
            args,
            federated_find(args.root, args.term, limit=args.limit),
            render_find,
        )
        return

    fg = FederatedGraph(args.root)
    if cmd == "query":
        from weld._cli_render import render_query

        emit(args, _query_envelope(args, fg.query(args.term, args.limit)),
             render_query)
    elif cmd == "context":
        from weld._cli_render import render_context

        emit(args, apply_context_envelope(args, fg.context(args.node_id)),
             render_context)
    elif cmd == "path":
        from weld._cli_render import render_path

        emit(args, fg.path(args.from_id, args.to_id), render_path)
    elif cmd == "callers":
        from weld._cli_render import render_callers
        from weld.federation_tools import federated_callers

        emit_node_lookup(
            args,
            apply_callers_envelope(
                args, federated_callers(fg, args.symbol, depth=args.depth),
            ),
            render_callers,
        )
    elif cmd == "references":
        from weld._cli_render import render_references
        from weld.federation_tools import federated_references
        from weld.file_index import find_files, load_file_index

        # Graph matches/callers fan out across children; the ``files`` field
        # uses the root file-index, identical to MCP ``weld_references`` (parity).
        refs = federated_references(fg, args.name)
        index = load_file_index(args.root)
        refs["files"] = find_files(index, args.name).get("files", [])
        emit(args, apply_references_envelope(args, refs), render_references)
    elif cmd == "communities":
        from weld._federation_flatten import flatten_federation
        from weld.graph_communities_cli import run_graph_communities

        # Whole-graph analysis over the read-time flattened union (ADR 0089).
        run_graph_communities(args, flatten_federation(fg, build_index=False))
