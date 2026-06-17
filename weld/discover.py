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

import json
import sys
import time
from pathlib import Path

from weld._discover_empty_guard import (
    EmptyFederatedGraphRefusedError,
    enforce_nonempty_federated_write as _enforce_nonempty_federated_write,
)
from weld._discover_empty_warn import warn_if_no_sources as _warn_if_no_sources
from weld._discover_federate import merge_cross_repo_edges, retag_federated_origins_on_disk
from weld._discover_no_change import no_change_refresh as _no_change_refresh
from weld._discover_postprocess import post_process as _post_process
from weld._discover_sidecar import (finalize_single_repo as _finalize_single_repo,
                                    persist_sqlite_sidecar as _persist_sqlite_sidecar)
from weld._discover_state_check import (files_missing_from_graph,
                                         graph_files_with_nodes)
from weld._discover_strategies import (
    IncrementalHint,
    load_strategy as _load_strategy,  # noqa: F401 -- re-export for test consumers
    run_external_json as _run_external_json,  # noqa: F401 -- re-export for test consumers
    run_source as _run_source,
)
from weld._discover_summary import emit_summary as _emit_summary
from weld._yaml import parse_yaml
from weld.contract import SCHEMA_VERSION  # noqa: F401 -- re-export for consumers
from weld.discovery_state import (StateDiff, build_file_hashes, diff_state,
                                   files_missing_strategy_outputs, load_state,
                                   purge_stale_nodes, resolve_source_file_map)
from weld._graph_meta_sidecar import write_graph_with_meta as _write_graph_with_meta
from weld.federation_root import build_root_meta_graph
from weld.serializer import dumps_graph as _dumps_graph
from weld.workspace import WorkspaceConfigError
from weld.workspace_state import (WorkspaceLock, WorkspaceLockedError,
                                  build_workspace_state, load_workspace_config,
                                  save_workspace_state)
from weld.strategies._helpers import filter_glob_results


def _drain_context_warnings(context: dict) -> None:
    """Print unique strategy warnings to stderr, one line each.

    Strategies append diagnostic messages to ``context["_warnings"]``
    when they degrade gracefully (e.g. tree-sitter installed but the
    per-language grammar package missing). Without an explicit drain at
    the end of discovery, those entries die with the in-memory context
    and the user sees a silently-successful ``wd discover`` with zero
    nodes for the affected language.

    Output uses the existing ``[weld] warning:`` prefix established by
    the unsafe-mode strategy warnings so operators can grep for either
    source uniformly. Deduplication is by full message text to keep the
    output bounded when a strategy emits the same line per matched file.
    """
    raw = context.get("_warnings", [])
    if not raw:
        return
    seen: set[str] = set()
    for msg in raw:
        text = str(msg)
        if text in seen:
            continue
        seen.add(text)
        print(f"[weld] warning: {text}", file=sys.stderr)


def _discover_single_repo(
    root: Path,
    *,
    incremental: bool | None = None,
    safe: bool = False,
    with_sqlite: bool = True,
    write_graph: bool = False,
) -> dict:
    """Walk the codebase and build a connected structure from config.

    *incremental*: ``True`` = skip unchanged files, ``False`` = full,
    ``None`` = auto-detect (incremental if state file exists).

    *safe*: when True, refuse project-local strategy overrides and the
    ``external_json`` subprocess adapter (ADR 0024).

    *write_graph*: when True, write ``.weld/graph.json`` (+ ADR 0065
    sidecar) here reusing the bytes already serialized for the sidecars,
    skipping a second ~900 ms serialization (bd 85tb.2). Auto-refresh sets
    this; ``discover()`` / ``main()`` leave it ``False`` -- they own a
    configurable ``--output`` and keep the pure build-and-return shape.

    Strategies may share state via ``context`` keys such as
    ``table_to_entity``/``pending_fk_edges`` (sqlalchemy strategy) and
    ``command_texts`` (firstline_md strategy) -- :func:`_post_process`
    consumes them to resolve FKs and to emit agent-invocation edges.
    """
    config_path = root / ".weld" / "discover.yaml"
    config = parse_yaml(config_path.read_text(encoding="utf-8")) if config_path.exists() else {"sources": [], "topology": {}}
    sources = config.get("sources", [])
    _warn_if_no_sources(root, sources)  # loud signal for a forgotten `wd init`

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

    # Resolve all globs -> current file set (duplicate globs walk once).
    source_file_map = resolve_source_file_map(root, sources, filter_glob_results)
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
        context, nodes, edges, df = {}, {}, [], []  # type: dict, dict[str, dict], list[dict], list[str]
        for s in sources:
            r = _run_source(root, s, context, safe=safe)
            nodes.update(r.nodes)
            edges.extend(r.edges)
            df.extend(r.discovered_from)
        graph = _post_process(nodes, edges, context, config, root, df)
        _finalize_single_repo(
            root, current_hashes, graph, with_sqlite, write_graph,
        )
        _drain_context_warnings(context)
        return graph

    # --- Incremental path ---
    assert existing_graph is not None and old_state is not None
    # Pass ``files_with_no_nodes`` so legitimately node-less sources (e.g.
    # concept_from_bd) stop perpetually re-triggering the slow path
    # (bd 85tb.2); see files_missing_strategy_outputs for the full rationale.
    missing_outputs = files_missing_strategy_outputs(
        existing_graph, source_file_map, old_state.files_with_no_nodes,
    )
    # Per-file audit: catches files that are state-disk-consistent
    # but absent from a graph that predates them, even when sibling
    # files in the same source still have nodes (which satisfies the
    # source-level audit above). ``state.files_with_no_nodes`` exempts
    # legitimate empty-output files from re-running.
    missing_per_file = files_missing_from_graph(
        old_state, set(current_hashes.keys()),
        graph_files_with_nodes(existing_graph),
    )
    dirty = state_diff.dirty | missing_outputs | missing_per_file
    stale = dirty | state_diff.deleted

    if not state_diff.has_changes and not missing_outputs and not missing_per_file:
        # No content change: refresh volatile meta only, no strategy re-run.
        # The loaded ``existing_graph`` stays byte-pristine (see
        # discover_no_changes_does_not_mutate_loaded_graph_test).
        return _no_change_refresh(
            root, existing_graph, existing_graph_bytes,
            current_file_set, current_hashes,
            with_sqlite=with_sqlite, write_graph=write_graph,
        )

    # Purge stale nodes; edges purge by ADR 0074 provenance (clean-caller
    # inbound edges into dirty-file symbols survive, dirty re-parse re-mints
    # the endpoint) so parse-only-dirty stays byte-identical to a full run.
    ex_nodes, ex_edges = purge_stale_nodes(
        dict(existing_graph.get("nodes", {})),
        list(existing_graph.get("edges", [])),
        stale,
    )

    # ADR 0074: hand python_callgraph the dirty scope + POST-PURGE prior node
    # set (snapshot before dirty re-runs mutate ``ex_nodes``) so it parses
    # only dirty files and reconstructs cross-glob ``project_modules`` from
    # surviving prior symbols instead of re-globbing every sibling.
    incremental_hint = IncrementalHint(
        dirty_files=frozenset(dirty), prior_nodes=dict(ex_nodes),
    )

    # Run strategies for source entries with dirty files
    context = {}
    for i, source in enumerate(sources):
        if not set(source_file_map[i]).intersection(dirty):
            continue
        r = _run_source(
            root, source, context, safe=safe, incremental_hint=incremental_hint,
        )
        for nid, node in r.nodes.items():
            nf = node.get("props", {}).get("file", "")
            if not nf or nf in dirty:
                ex_nodes[nid] = node
        ex_edges.extend(r.edges)

    # Merge discovered_from
    old_df = [p for p in existing_graph.get("meta", {}).get("discovered_from", []) if p not in state_diff.deleted]
    new_df = [str(p) for files in source_file_map for p in files if p in dirty]
    graph = _post_process(ex_nodes, ex_edges, context, config, root, old_df + new_df)
    _finalize_single_repo(
        root, current_hashes, graph, with_sqlite, write_graph,
    )
    _drain_context_warnings(context)
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
    with_sqlite: bool = True,
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
        return _discover_single_repo(
            root, incremental=incremental, safe=safe, with_sqlite=with_sqlite,
        )
    with WorkspaceLock(root):
        state = build_workspace_state(root, workspace_config)
        if recurse:
            from weld._discover_recurse import recurse_children
            recurse_children(
                root, workspace_config, state,
                incremental=incremental, safe=safe,
            )
            state = build_workspace_state(root, workspace_config)
        # ADR 0042 §Federation: re-tag cross-child Python targets that
        # each child's python_callgraph saw as origin="external" because
        # it only knew its own glob.
        retag_federated_origins_on_disk(root, workspace_config, state)
        graph = build_root_meta_graph(root, workspace_config, state)
        # Invoke cross-repo resolvers after the meta-graph is built and
        # after any recurse pass has refreshed each child's graph.json:
        # resolvers consume child graphs by reading those files.
        graph = merge_cross_repo_edges(root, workspace_config, state, graph)
        if output is not None:
            _enforce_nonempty_federated_write(
                output, graph, state, allow_empty=allow_empty,
            )
            # ADR 0065: write graph.json (volatile meta stripped) plus the
            # graph-meta.json sidecar when the target is the canonical name.
            _write_graph_with_meta(output, graph)
            if with_sqlite and output.name == "graph.json":  # ADR 0058: sidecar pairs by name
                _persist_sqlite_sidecar(output.parent, graph)
        elif write_root_graph:
            target = root / ".weld" / "graph.json"
            _enforce_nonempty_federated_write(
                target, graph, state, allow_empty=allow_empty,
            )
            _write_graph_with_meta(target, graph)  # ADR 0065
            if with_sqlite:
                _persist_sqlite_sidecar(target.parent, graph)
        save_workspace_state(root, state)
        return graph


def _emit_compile_db_stub_main(root: Path) -> int:
    """Write the libclang compile-db stub and return an exit code."""
    from weld._doctor_cpp import emit_compile_db_stub
    try:
        json_p, readme_p = emit_compile_db_stub(root)
    except FileExistsError as exc:
        print(f"[weld] error: {exc}", file=sys.stderr)
        return 2
    print(f"[weld] wrote stub {json_p} and {readme_p}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    from weld._discover_cli import build_parser
    args = build_parser().parse_args(argv)
    if args.emit_compile_db_stub:
        return _emit_compile_db_stub_main(Path(args.root))

    inc = False if args.full else (True if args.incremental else None)
    output_path = Path(args.output) if args.output else None
    root_path = Path(args.root)
    is_federated = load_workspace_config(root_path) is not None
    started = time.monotonic()
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
            with_sqlite=not args.no_sqlite,
        )
    except (WorkspaceConfigError, WorkspaceLockedError) as exc:
        print(f"[weld] error: {exc}", file=sys.stderr)
        return 2
    except EmptyFederatedGraphRefusedError:
        # The guard already wrote the explanatory stderr message.
        return 3
    if output_path is not None and not is_federated:
        # ADR 0065: write graph.json (volatile meta stripped) plus the
        # graph-meta.json sidecar when the output is the canonical name.
        _write_graph_with_meta(output_path, result)
        if not args.no_sqlite and output_path.name == "graph.json":  # ADR 0058: sidecar pairs by name
            _persist_sqlite_sidecar(output_path.parent, result)
    elif output_path is None:
        sys.stdout.write(_dumps_graph(result))
    sp = output_path or ((root_path / ".weld" / "graph.json")
                         if args.write_root_graph and is_federated else None)
    _emit_summary(result, sp, time.monotonic() - started, quiet=args.quiet)
    # ADR 0052: first-run enrichment policy. Federated roots are
    # scoped out today.
    if not is_federated:
        from weld._first_run_enrich import maybe_propose_enrichment
        maybe_propose_enrichment(
            root_path, result, safe=args.safe, no_enrich_flag=args.no_enrich,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
