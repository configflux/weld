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
import time
from pathlib import Path

from weld._discover_basis import (config_fingerprint,
                                  incremental_basis_valid as _incremental_basis_valid,
                                  sources_needing_retry as _sources_needing_retry,
                                  strategy_fingerprint as _strategy_fingerprint)
from weld._discover_empty_guard import (
    EmptyFederatedGraphRefusedError,
    enforce_nonempty_federated_write as _enforce_nonempty_federated_write,
)
from weld._discover_empty_warn import warn_if_no_sources as _warn_if_no_sources
from weld._discover_federate import merge_cross_repo_edges, retag_federated_origins_on_disk
from weld._discover_incremental_merge import run_incremental_merge
from weld._discover_inputs import plan_delta, stale_directory_marker as _stale_directory_marker
from weld._discover_no_change import no_change_refresh as _no_change_refresh
from weld._discover_node_merge import claim_supersedes
from weld._discover_postprocess import post_process as _post_process
from weld._discover_sidecar import (finalize_single_repo as _finalize_single_repo,
                                    persist_cli_graph as _persist_cli_graph,
                                    persist_sqlite_sidecar as _persist_sqlite_sidecar)
from weld._discover_strategies import (
    load_strategy as _load_strategy,  # noqa: F401 -- re-export for test consumers
    run_external_json as _run_external_json,  # noqa: F401 -- re-export for test consumers
    run_source as _run_source,
)
from weld._discover_summary import (drain_context_warnings as _drain_context_warnings,
                                    emit_summary as _emit_summary)
from weld._federation_basis import publish_root_graph as _publish_root_graph
from weld._yaml import parse_yaml
from weld.contract import SCHEMA_VERSION  # noqa: F401 -- re-export for consumers
from weld.discovery_state import load_state
from weld._source_resolve import resolve_source_file_map
from weld._graph_anchors import (files_missing_from_graph,
                                 files_missing_strategy_outputs,
                                 graph_files_with_nodes)
from weld.federation_root import build_root_meta_graph
from weld.workspace import WorkspaceConfigError
from weld.workspace_state import (WorkspaceLock, WorkspaceLockedError,
                                  build_workspace_state, load_workspace_config,
                                  save_workspace_state)
from weld.glob_match import glob_scope
from weld.repo_boundary import repo_boundary_scope
from weld.strategies._strategy_failure import (drain_source_failures,
                                               drain_strategy_failures)
from weld._notice import emit


# bd jbpb: one run observes one repo-boundary snapshot, taken here and dropped
# on return -- the warm-refresh entry (ADR 0074) runs this per read in a
# long-lived host, which must never inherit an earlier run's file listing. The
# scope is a ContextDecorator; see repo_boundary.repo_boundary_scope.
@repo_boundary_scope()
@glob_scope()  # bd cjij: walk each distinct glob once per run, same lifetime.
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
    # Fingerprinted once, from the mapping this run actually ran (bd 4fpj):
    # both the basis gate below and the state this run writes must name the
    # same config, or the state would vouch for a config nothing ran under.
    config_fp = config_fingerprint(config)
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
    source_file_map = resolve_source_file_map(root, sources)
    current_file_set = sorted({f for files in source_file_map for f in files})

    # State tracking
    old_state = load_state(root)
    if incremental is None:
        incremental = old_state is not None

    if incremental and not _incremental_basis_valid(
        old_state, graph_path, existing_graph, config_fp,
        _strategy_fingerprint(root),
    ):
        incremental = False

    # The delta covers every file the graph reads, not only the glob-resolved
    # ones (a loaded ``.bzl`` is an input no source entry resolves), and a dirty
    # path no entry claims re-decides *incremental* (weld._discover_inputs).
    current_hashes, state_diff, incremental = plan_delta(
        root, existing_graph, current_file_set, old_state, incremental,
    )
    if not incremental:
        # Full discovery
        context, nodes, edges, df = {}, {}, [], []  # type: dict, dict[str, dict], list[dict], list[str]
        for i, s in enumerate(sources):
            r = _run_source(root, s, context, safe=safe,
                            source_files=source_file_map[i])
            # ADR 0103: not ``nodes.update`` -- a later entry's evidence-free
            # stub must not overwrite the definite, file-bearing definition an
            # earlier entry walked, or graph_closure loses the anchor it
            # derives ``contains`` from (bd 4ux4).
            for nid, node in r.nodes.items():
                if claim_supersedes(nodes.get(nid), node):
                    nodes[nid] = node
            edges.extend(r.edges)
            df.extend(r.discovered_from)
        graph = _post_process(
            nodes, edges, context, config, root, df,
            previous_graph=existing_graph,
        )
        _finalize_single_repo(
            root, current_hashes, graph, with_sqlite, write_graph,
            config_fingerprint=config_fp,
            strategy_failed=drain_strategy_failures(context),
            source_failed=drain_source_failures(context),
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
    # Entry-keyed counterpart to the two audits above, for source entries no
    # file-hash delta can ever mark dirty because they resolve no files at
    # all (a command-only ``external_json`` adapter, bd um00).
    retry_entry_ids = _sources_needing_retry(sources, old_state)

    if (not state_diff.has_changes and not missing_outputs
            and not missing_per_file and not retry_entry_ids):
        # No content change: refresh volatile meta only, no strategy re-run.
        # The loaded ``existing_graph`` stays byte-pristine (see
        # discover_no_changes_does_not_mutate_loaded_graph_test).
        return _no_change_refresh(
            root, existing_graph, existing_graph_bytes,
            current_file_set, current_hashes,
            with_sqlite=with_sqlite, write_graph=write_graph,
            config_fingerprint=config_fp,
        )

    # Purge stale nodes/edges and re-run dirty sources; widen once for any
    # surviving edge a purge left dangling -- a clean file's edge into a node
    # the purge removed and the dirty pass never re-minted (a deleted import
    # target, or a symbol edited away without reusing its id). ADR 0074
    # fourth amendment, bd znzu.
    ex_nodes, ex_edges, context, dirty, ran_df = run_incremental_merge(
        root, sources, source_file_map, existing_graph, dirty, stale, safe=safe,
        retry_entry_ids=retry_entry_ids,
    )

    # Merge discovered_from: union of what survives from the previous graph
    # (minus deleted files) with what every re-run source just reported.
    # ADR 0008 section 5.6's original contract is "the union of all files
    # that contributed to the current graph" -- ``ran_df`` is collected
    # straight from each source's ``StrategyResult``, not re-derived from
    # ``source_file_map``, so a footprint-less source (bd um00) or a
    # directory-anchored provenance entry (ADR 0017's amendment) is never
    # silently dropped just because it has no membership in a glob-resolved
    # file list (bd 8084). ``state_diff.deleted`` only ever holds literal
    # FILE paths (keys of ``old_state.files``), so a directory-provenance
    # marker (``python_package``'s ``"weld/strategies/"`` shape) is never a
    # member of it even when every file under that directory is gone --
    # ``stale_directory_marker`` catches that case by checking the
    # directory itself against disk (bd 0t5p).
    old_df = [
        p for p in existing_graph.get("meta", {}).get("discovered_from", [])
        if p not in state_diff.deleted and not _stale_directory_marker(root, p)
    ]
    graph = _post_process(
        ex_nodes, ex_edges, context, config, root, old_df + ran_df,
        previous_graph=existing_graph,
    )
    # The drained failure set is complete even though only dirty sources ran:
    # a file recorded as failed is exempt from nothing ``files_missing_from_graph``
    # consults, so it is in ``missing_per_file`` above, so it is dirty, so its
    # source is among the ones this loop just re-ran (bd hch4).
    _finalize_single_repo(
        root, current_hashes, graph, with_sqlite, write_graph,
        config_fingerprint=config_fp,
        strategy_failed=drain_strategy_failures(context),
        source_failed=drain_source_failures(context),
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
        # One write shape: *output* names the target, else the canonical name
        # when asked for it. The two branches this replaces differed only in
        # that name and in an ADR 0058 test the canonical target satisfies.
        target = output if output is not None else (
            root / ".weld" / "graph.json" if write_root_graph else None
        )
        if target is not None:
            _enforce_nonempty_federated_write(
                target, graph, state, allow_empty=allow_empty,
            )
            # ADR 0065 paired write + the ADR 0141 D1 basis, one call so a
            # published root graph cannot lose the inventory vouching for
            # what it read (M1). The sqlite sidecar pairs by name (ADR 0058)
            # and the basis follows the same rule: --output elsewhere, neither.
            _publish_root_graph(root, graph, target)
            if with_sqlite and target.name == "graph.json":
                _persist_sqlite_sidecar(target.parent, graph)
        save_workspace_state(root, state)
        return graph


def _emit_compile_db_stub_main(root: Path) -> int:
    """Write the libclang compile-db stub and return an exit code."""
    from weld._doctor_cpp import emit_compile_db_stub
    try:
        json_p, readme_p = emit_compile_db_stub(root)
    except FileExistsError as exc:
        emit(f"[weld] error: {exc}")
        return 2
    emit(f"[weld] wrote stub {json_p} and {readme_p}")
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
        emit(f"[weld] error: {exc}")
        return 2
    except EmptyFederatedGraphRefusedError:
        # The guard already wrote the explanatory stderr message.
        return 3
    # Persist + echo the graph (ADR 0019). A single-repo root writes
    # .weld/graph.json even without --output so reads resolve after a bare
    # discover (bd ck0w); federated writes land inside discover()'s workspace
    # lock. Sibling helper keeps the write-shape logic out of main().
    _persist_cli_graph(
        root_path, output_path, result,
        is_federated=is_federated, no_sqlite=args.no_sqlite,
    )
    # Summary names the canonical target whenever we persisted one.
    sp = output_path or ((root_path / ".weld" / "graph.json")
                         if not is_federated or args.write_root_graph else None)
    _emit_summary(result, sp, time.monotonic() - started, quiet=args.quiet)
    # ADR 0052: first-run enrichment policy. Federated roots are
    # scoped out today.
    if not is_federated:
        from weld._first_run_enrich import maybe_propose_enrichment
        maybe_propose_enrichment(
            root_path, result, safe=args.safe, no_enrich_flag=args.no_enrich,
        )
    return 0
