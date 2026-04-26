#!/usr/bin/env python3
"""Config-driven codebase discovery for the connected structure.

Reads ``.weld/discover.yaml`` to determine what to scan, then loads strategy
plugins from ``weld/strategies/`` (bundled) or ``.weld/strategies/`` (project-local)
and dispatches to their ``extract()`` functions.

Incremental mode (ADR 0008): when a state file exists, only re-extract
source entries whose matched files have changed.  Use ``--full`` to force
a complete re-scan.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from weld._discover_empty_guard import (
    EmptyFederatedGraphRefusedError,
    enforce_nonempty_federated_write as _enforce_nonempty_federated_write,
)
from weld._discover_federate import merge_cross_repo_edges
from weld._discover_postprocess import post_process as _post_process
from weld._discover_strategies import (
    load_strategy as _load_strategy,  # noqa: F401 -- re-export for test consumers
    run_external_json as _run_external_json,  # noqa: F401 -- re-export for test consumers
    run_source as _run_source,
)
from weld._query_sidecar import write_sidecar_for_bytes as _write_query_sidecar_bytes
from weld._git import get_git_sha
from weld._yaml import parse_yaml
from weld.contract import SCHEMA_VERSION  # noqa: F401 -- re-export for consumers
from weld.federation_root import build_root_meta_graph
from weld.serializer import dumps_graph as _dumps_graph
from weld.workspace import WorkspaceConfigError
from weld.workspace_state import (WorkspaceLock, WorkspaceLockedError,
                                  build_workspace_state, load_workspace_config,
                                  save_workspace_state)
from weld.discovery_state import (
    DiscoveryState,
    StateDiff,
    build_file_hashes,
    diff_state,
    load_state,
    purge_stale_nodes,
    resolve_source_files,
    save_state,
)
from weld.strategies._helpers import filter_glob_results

# ---------------------------------------------------------------------------
# Discovery orchestrator
# ---------------------------------------------------------------------------


def _persist_query_state_sidecar(weld_dir: Path, graph: dict) -> None:
    """Write the .weld/query_state.bin sidecar for the freshly-built graph.

    ADR 0031: the inverted index, BM25 corpus, and structural-score table
    are pure functions of the graph's node and edge sets and dominate
    the ``wd query`` cold path. Persisting them here makes the next
    cold ``Graph.load`` skip the rebuild. Failures inside the sidecar
    writer are logged and swallowed -- a missing sidecar simply means
    the next cold load rebuilds and writes one itself.
    """
    try:
        from weld.query_state import build_query_state

        nodes = graph.get("nodes", {})
        edges = graph.get("edges", [])
        graph_bytes = _dumps_graph(graph).encode("utf-8")
        state = build_query_state(nodes, edges)
        _write_query_sidecar_bytes(weld_dir, graph_bytes, nodes, edges, state)
    except Exception as exc:  # noqa: BLE001 -- sidecar is best-effort.
        print(
            f"[weld] notice: skipped query-state sidecar write: {exc}",
            file=sys.stderr,
        )

def _discover_single_repo(
    root: Path,
    *,
    incremental: bool | None = None,
    safe: bool = False,
) -> dict:
    """Walk the codebase and build a connected structure from config.

    *incremental*: ``True`` = skip unchanged files, ``False`` = full,
    ``None`` = auto-detect (incremental if state file exists).

    *safe*: when True, refuse project-local strategy overrides and the
    ``external_json`` subprocess adapter (ADR 0024).

    Strategies may share state via ``context`` keys such as
    ``table_to_entity``/``pending_fk_edges`` (sqlalchemy strategy) and
    ``command_texts`` (firstline_md strategy) -- :func:`_post_process`
    consumes them to resolve FKs and to emit agent-invocation edges.
    """
    config_path = root / ".weld" / "discover.yaml"
    config = parse_yaml(config_path.read_text(encoding="utf-8")) if config_path.exists() else {"sources": [], "topology": {}}
    sources = config.get("sources", [])

    # Load the previous graph and snapshot it for `wd diff` -- but only after
    # we've confirmed it parses. A corrupt graph.json must not overwrite the
    # last known-good graph-previous.json.
    graph_path = root / ".weld" / "graph.json"
    prev_path = root / ".weld" / "graph-previous.json"
    existing_graph_bytes: bytes | None = None
    existing_graph: dict | None = None
    if graph_path.is_file():
        try:
            existing_graph_bytes = graph_path.read_bytes()
            existing_graph = json.loads(existing_graph_bytes.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            existing_graph_bytes = None
            existing_graph = None

    if existing_graph_bytes is not None:
        try:
            prev_path.write_bytes(existing_graph_bytes)
        except OSError:
            pass  # best-effort; diff will report "no previous"

    # Resolve all globs -> current file set
    source_file_map = [resolve_source_files(root, s, filter_glob_results) for s in sources]
    current_file_set = sorted({f for files in source_file_map for f in files})

    # State tracking
    old_state = load_state(root)
    if incremental is None:
        incremental = old_state is not None

    if incremental:
        if old_state is None:
            print("[weld] notice: no discovery state file, running full discovery", file=sys.stderr)
            incremental = False
        elif not graph_path.is_file():
            print("[weld] notice: no graph.json found, running full discovery", file=sys.stderr)
            incremental = False
        elif existing_graph is None:
            print("[weld] warning: corrupt graph.json, falling back to full discovery", file=sys.stderr)
            incremental = False

    current_hashes = build_file_hashes(root, current_file_set)
    state_diff = diff_state(old_state, current_hashes) if incremental else StateDiff(added=set(current_hashes.keys()))

    if not incremental:
        # Full discovery
        context: dict = {}
        nodes: dict[str, dict] = {}
        edges: list[dict] = []
        df: list[str] = []
        for s in sources:
            r = _run_source(root, s, context, safe=safe)
            nodes.update(r.nodes)
            edges.extend(r.edges)
            df.extend(r.discovered_from)
        graph = _post_process(nodes, edges, context, config, root, df)
        save_state(root, DiscoveryState(files=current_hashes))
        _persist_query_state_sidecar(root / ".weld", graph)
        return graph

    # --- Incremental path ---
    assert existing_graph is not None and old_state is not None
    dirty = state_diff.dirty
    stale = dirty | state_diff.deleted

    if not state_diff.has_changes:
        print("[weld] notice: no files changed, graph is up to date", file=sys.stderr)
        refreshed = copy.deepcopy(existing_graph)
        refreshed["meta"]["version"] = SCHEMA_VERSION
        refreshed["meta"]["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        sha = get_git_sha(root)
        if sha is not None:
            refreshed["meta"]["git_sha"] = sha
        if not refreshed["meta"].get("discovered_from"):
            refreshed["meta"]["discovered_from"] = current_file_set
        save_state(root, DiscoveryState(files=current_hashes))
        _persist_query_state_sidecar(root / ".weld", refreshed)
        return refreshed

    # Purge stale nodes from existing graph
    ex_nodes, ex_edges = purge_stale_nodes(
        dict(existing_graph.get("nodes", {})),
        list(existing_graph.get("edges", [])),
        stale,
    )

    # Run strategies for source entries with dirty files
    context = {}
    for i, source in enumerate(sources):
        if not set(source_file_map[i]).intersection(dirty):
            continue
        r = _run_source(root, source, context, safe=safe)
        for nid, node in r.nodes.items():
            nf = node.get("props", {}).get("file", "")
            if not nf or nf in dirty:
                ex_nodes[nid] = node
        ex_edges.extend(r.edges)

    # Merge discovered_from
    old_df = [p for p in existing_graph.get("meta", {}).get("discovered_from", []) if p not in state_diff.deleted]
    new_df = [str(p) for files in source_file_map for p in files if p in dirty]
    graph = _post_process(ex_nodes, ex_edges, context, config, root, old_df + new_df)
    save_state(root, DiscoveryState(files=current_hashes))
    _persist_query_state_sidecar(root / ".weld", graph)
    return graph


def discover(
    root: Path,
    *,
    incremental: bool | None = None,
    write_root_graph: bool = False,
    recurse: bool = False,
    output: Path | None = None,
    safe: bool = False,
    allow_empty: bool = False,
) -> dict:
    """Walk the codebase and build a connected structure from config.

    Shared strategy context may include ``table_to_entity``,
    ``pending_fk_edges``, and ``command_texts``. When
    :file:`workspaces.yaml` is present at *root*, discovery emits a
    federation root meta-graph (ADR 0011 sections 4-6). The call is
    guarded by :class:`WorkspaceLock`; with *write_root_graph* the
    meta-graph is written to ``.weld/graph.json`` atomically inside
    the lock before the ledger, so the ledger never points at a graph
    this run failed to commit (ADR 0011 section 8).

    When *output* is provided (ADR 0019), the final canonical graph is
    written to that path atomically via
    :func:`weld.workspace_state.atomic_write_text`. For federated roots
    *output* takes precedence over *write_root_graph* and the meta-graph
    goes to *output* instead of ``.weld/graph.json``; the write still
    happens inside the workspace lock. For single-repo roots *output*
    is handled by the caller (:func:`main`) so this function keeps its
    pure "build and return graph" shape for single-repo callers.

    When *safe* is True, the discovery pipeline refuses project-local
    strategy overrides under ``<root>/.weld/strategies/`` and the
    ``external_json`` subprocess adapter (ADR 0024). Bundled strategies
    still run; partial results are returned for repos that depend on
    refused paths.
    """
    workspace_config = load_workspace_config(root)
    if workspace_config is None:
        return _discover_single_repo(root, incremental=incremental, safe=safe)
    with WorkspaceLock(root):
        state = build_workspace_state(root, workspace_config)
        if recurse:
            from weld._discover_recurse import recurse_children
            recurse_children(
                root, workspace_config, state,
                incremental=incremental, safe=safe,
            )
            state = build_workspace_state(root, workspace_config)
        graph = build_root_meta_graph(root, workspace_config, state)
        # Invoke cross-repo resolvers after the meta-graph is built and
        # after any recurse pass has refreshed each child's graph.json:
        # resolvers consume child graphs by reading those files.
        graph = merge_cross_repo_edges(root, workspace_config, state, graph)
        if output is not None:
            from weld.workspace_state import atomic_write_text

            _enforce_nonempty_federated_write(
                output, graph, state, allow_empty=allow_empty,
            )
            atomic_write_text(output, _dumps_graph(graph))
        elif write_root_graph:
            from weld.workspace_state import atomic_write_text

            target = root / ".weld" / "graph.json"
            _enforce_nonempty_federated_write(
                target, graph, state, allow_empty=allow_empty,
            )
            atomic_write_text(target, _dumps_graph(graph))
        save_workspace_state(root, state)
        return graph


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wd discover",
        description="Run config-driven Weld discovery and emit graph JSON to stdout")
    parser.add_argument("root", nargs="?", default=".", help="Project root directory (default: .)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--incremental", action="store_true", default=False,
        help="Only re-extract changed files (default when state file exists)")
    mode.add_argument("--full", action="store_true", default=False,
        help="Force full discovery, ignoring any previous state")
    parser.add_argument(
        "--write-root-graph",
        action="store_true",
        default=False,
        help="On a federated root, write .weld/graph.json atomically "
             "inside the workspace lock (required for crash-safety).",
    )
    parser.add_argument(
        "--recurse", action="store_true", default=False,
        help="Cascade discovery into each present child before building the root meta-graph.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Atomically write canonical graph JSON to this path "
             "(parent directories are created). When set, stdout is "
             "empty; human status still goes to stderr. (ADR 0019)",
    )
    parser.add_argument(
        "--safe",
        action="store_true",
        default=False,
        help="Refuse project-local strategies under .weld/strategies/ and "
             "the external_json subprocess adapter. Use this when scanning "
             "an untrusted repository. (ADR 0024)",
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        default=False,
        help="Bypass the federated empty-graph guard. By default, a "
             "federated discover that would overwrite a non-empty graph "
             "with a 0-node meta-graph is refused; pass this flag to "
             "intentionally tear the workspace graph down. (ADR 0028)",
    )
    args = parser.parse_args(argv)

    inc = False if args.full else (True if args.incremental else None)
    output_path = Path(args.output) if args.output else None
    root_path = Path(args.root)
    is_federated = load_workspace_config(root_path) is not None
    try:
        result = discover(
            root_path,
            incremental=inc,
            write_root_graph=args.write_root_graph,
            recurse=args.recurse,
            # Federated roots write inside the workspace lock; single-repo
            # roots write here via the same atomic helper.
            output=output_path if is_federated else None,
            safe=args.safe,
            allow_empty=args.allow_empty,
        )
    except (WorkspaceConfigError, WorkspaceLockedError) as exc:
        print(f"[weld] error: {exc}", file=sys.stderr)
        return 2
    except EmptyFederatedGraphRefusedError:
        # The guard already wrote the explanatory stderr message.
        return 3
    if output_path is not None:
        if not is_federated:
            from weld.workspace_state import atomic_write_text

            atomic_write_text(output_path, _dumps_graph(result))
        return 0
    sys.stdout.write(_dumps_graph(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
