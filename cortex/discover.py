#!/usr/bin/env python3
"""Config-driven codebase discovery for the knowledge graph.

Reads ``.cortex/discover.yaml`` to determine what to scan, then loads strategy
plugins from ``cortex/strategies/`` (bundled) or ``.cortex/strategies/`` (project-local)
and dispatches to their ``extract()`` functions.

Incremental mode (ADR 0008): when a state file exists, only re-extract
source entries whose matched files have changed.  Use ``--full`` to force
a complete re-scan.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from cortex._git import get_git_sha
from cortex._yaml import parse_yaml
from cortex.contract import SCHEMA_VERSION
from cortex.discovery_state import (
    DiscoveryState,
    StateDiff,
    build_file_hashes,
    diff_state,
    load_state,
    purge_stale_nodes,
    resolve_source_files,
    save_state,
)
from cortex.strategies._helpers import StrategyResult, filter_glob_results

# ---------------------------------------------------------------------------
# Strategy loader
# ---------------------------------------------------------------------------

def _load_strategy(name: str, root: Path):
    """Load a strategy's ``extract`` function by name."""
    project_local = root / ".cortex" / "strategies" / f"{name}.py"
    bundled = Path(__file__).resolve().parent / "strategies" / f"{name}.py"

    resolved_path: Path | None = None
    is_shadow = False

    if project_local.is_file():
        resolved_path = project_local
        if bundled.is_file():
            is_shadow = True
    elif bundled.is_file():
        resolved_path = bundled

    if resolved_path is None:
        print(f"[cortex] warning: strategy '{name}' not found", file=sys.stderr)
        return None

    if is_shadow:
        print(
            f"[cortex] notice: project-local strategy '{name}' shadows bundled one",
            file=sys.stderr,
        )

    spec = importlib.util.spec_from_file_location(f"kg_strategy_{name}", resolved_path)
    if spec is None or spec.loader is None:
        print(
            f"[cortex] warning: could not load strategy '{name}' from {resolved_path}",
            file=sys.stderr,
        )
        return None

    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    fn = getattr(mod, "extract", None)
    if fn is None:
        print(
            f"[cortex] warning: strategy '{name}' has no extract() function",
            file=sys.stderr,
        )
        return None

    return fn

# ---------------------------------------------------------------------------
# External JSON adapter
# ---------------------------------------------------------------------------

_EXTERNAL_JSON_TIMEOUT: int = 30

def _run_external_json(root: Path, source: dict) -> StrategyResult:
    """Run an external command, validate its JSON stdout as a graph fragment."""
    from cortex.contract import validate_fragment

    empty = StrategyResult(nodes={}, edges=[], discovered_from=[])
    cmd_str = source.get("command", "")
    if not cmd_str:
        print("[cortex] warning: external_json source missing 'command' key", file=sys.stderr)
        return empty

    timeout = int(source.get("timeout", _EXTERNAL_JSON_TIMEOUT))
    try:
        argv = shlex.split(cmd_str)
    except ValueError as exc:
        print(f"[cortex] warning: external_json bad command string: {exc}", file=sys.stderr)
        return empty

    try:
        proc = subprocess.run(argv, capture_output=True, text=True, cwd=str(root), timeout=timeout)
    except FileNotFoundError:
        print(f"[cortex] warning: external_json command not found: {argv[0]}", file=sys.stderr)
        return empty
    except subprocess.TimeoutExpired:
        print(f"[cortex] warning: external_json command timed out after {timeout}s", file=sys.stderr)
        return empty

    if proc.returncode != 0:
        snippet = (proc.stderr or "").strip()[:200]
        print(
            f"[cortex] warning: external_json command exited {proc.returncode}"
            + (f": {snippet}" if snippet else ""),
            file=sys.stderr,
        )
        return empty

    try:
        data = json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"[cortex] warning: external_json command emitted invalid JSON: {exc}", file=sys.stderr)
        return empty

    if not isinstance(data, dict):
        print("[cortex] warning: external_json output must be a JSON object", file=sys.stderr)
        return empty

    label = f"external_json:{cmd_str.split()[0] if cmd_str else '?'}"
    errs = validate_fragment(data, source_label=label, allow_dangling_edges=True)
    if errs:
        for e in errs:
            print(f"[cortex] validation: {e}", file=sys.stderr)
        return empty

    return StrategyResult(
        nodes=data.get("nodes", {}),
        edges=data.get("edges", []),
        discovered_from=data.get("discovered_from", []),
    )

# ---------------------------------------------------------------------------
# Source runner
# ---------------------------------------------------------------------------

def _run_source(root: Path, source: dict, context: dict) -> StrategyResult:
    """Run a single source entry through its strategy."""
    name = source.get("strategy", "")
    if name == "external_json":
        return _run_external_json(root, source)
    extract_fn = _load_strategy(name, root)
    if not extract_fn:
        return StrategyResult(nodes={}, edges=[], discovered_from=[])
    return extract_fn(root, source, context)

# ---------------------------------------------------------------------------
# Post-processing
# ---------------------------------------------------------------------------

def _post_process(nodes, edges, context, config, root, discovered_from) -> dict:
    """Run post-processing and build the final graph dict."""
    # FK resolution
    table_to_entity = context.get("table_to_entity", {})
    for e in context.get("pending_fk_edges", []):
        to_id = e["to"]
        if to_id.startswith("__table__:"):
            real = table_to_entity.get(to_id.split(":", 1)[1])
            if real:
                edges.append({**e, "to": real})
        else:
            edges.append(e)

    # Agent invocation detection
    agent_names = [nid.split(":", 1)[1] for nid in nodes if nid.startswith("agent:")]
    for cmd_nid, text in context.get("command_texts", {}).items():
        for aname in agent_names:
            if aname.lower() in text.lower():
                edges.append({
                    "from": cmd_nid, "to": f"agent:{aname}", "type": "invokes",
                    "props": {"source_strategy": "post_processing", "confidence": "inferred"},
                })

    # Topology overlay
    topology = config.get("topology", {})
    for sn in topology.get("nodes", []):
        nid = sn["id"]
        if nid not in nodes:
            props = dict(sn.get("props", {})) if isinstance(sn.get("props"), dict) else {}
            if "path" in props and not (root / props["path"]).is_dir():
                continue
            props.setdefault("source_strategy", "topology")
            props.setdefault("authority", "manual")
            props.setdefault("confidence", "definite")
            nodes[nid] = {"type": sn["type"], "label": sn.get("label", nid), "props": props}

    for se in topology.get("edges", []):
        ep = dict(se.get("props", {})) if isinstance(se.get("props"), dict) else {}
        ep.setdefault("source_strategy", "topology")
        ep.setdefault("confidence", "definite")
        edges.append({"from": se["from"], "to": se["to"], "type": se["type"], "props": ep})

    for mapping in (topology.get("entity_packages") or []):
        pkg_id, modules = mapping.get("package", ""), mapping.get("modules", [])
        if isinstance(modules, list):
            for nid, n in list(nodes.items()):
                if n["type"] == "entity" and n["props"].get("module") in modules:
                    edges.append({
                        "from": pkg_id, "to": nid, "type": "contains",
                        "props": {"source_strategy": "topology", "confidence": "definite"},
                    })

    # Clean + dedup edges
    edges = [e for e in edges if e["from"] in nodes and e["to"] in nodes]
    seen: set[str] = set()
    deduped: list[dict] = []
    for e in edges:
        key = f"{e['from']}|{e['to']}|{e['type']}"
        if key not in seen:
            seen.add(key)
            deduped.append(e)

    # Dedup discovered_from
    seen_p: set[str] = set()
    unique_from = [p for p in discovered_from if p not in seen_p and not seen_p.add(p)]  # type: ignore[func-returns-value]

    meta: dict = {
        "version": SCHEMA_VERSION,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "discovered_from": unique_from,
    }
    sha = get_git_sha(root)
    if sha is not None:
        meta["git_sha"] = sha

    return {"meta": meta, "nodes": nodes, "edges": deduped}

# ---------------------------------------------------------------------------
# Discovery orchestrator
# ---------------------------------------------------------------------------

def discover(root: Path, *, incremental: bool | None = None) -> dict:
    """Walk the codebase and build a knowledge graph from config.

    *incremental*: ``True`` = skip unchanged files, ``False`` = full,
    ``None`` = auto-detect (incremental if state file exists).

    Known context keys (producers -> consumers):

    - ``table_to_entity`` (dict[str, str]):
        Producer: sqlalchemy strategy.  Maps SQL table names to entity
        node IDs.

    - ``pending_fk_edges`` (list[dict]):
        Producer: sqlalchemy strategy.  Edges with ``__table__:``
        placeholder targets awaiting resolution.

    - ``command_texts`` (dict[str, str]):
        Producer: firstline_md strategy.  Maps command node IDs to their
        full markdown text for agent name matching.
    """
    config_path = root / ".cortex" / "discover.yaml"
    config = parse_yaml(config_path.read_text(encoding="utf-8")) if config_path.exists() else {"sources": [], "topology": {}}
    sources = config.get("sources", [])

    # Snapshot previous graph before overwriting (for `cortex diff`)
    graph_path_snap = root / ".cortex" / "graph.json"
    prev_path = root / ".cortex" / "graph-previous.json"
    if graph_path_snap.is_file():
        try:
            prev_path.write_bytes(graph_path_snap.read_bytes())
        except OSError:
            pass  # best-effort; diff will report "no previous"

    # Resolve all globs -> current file set
    source_file_map = [resolve_source_files(root, s, filter_glob_results) for s in sources]
    current_file_set = sorted({f for files in source_file_map for f in files})

    # State tracking
    old_state = load_state(root)
    if incremental is None:
        incremental = old_state is not None

    graph_path = root / ".cortex" / "graph.json"
    existing_graph: dict | None = None

    if incremental:
        if old_state is None:
            print("[cortex] notice: no discovery state file, running full discovery", file=sys.stderr)
            incremental = False
        elif not graph_path.is_file():
            print("[cortex] notice: no graph.json found, running full discovery", file=sys.stderr)
            incremental = False
        else:
            try:
                existing_graph = json.loads(graph_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                print("[cortex] warning: corrupt graph.json, falling back to full discovery", file=sys.stderr)
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
            r = _run_source(root, s, context)
            nodes.update(r.nodes)
            edges.extend(r.edges)
            df.extend(r.discovered_from)
        graph = _post_process(nodes, edges, context, config, root, df)
        save_state(root, DiscoveryState(files=current_hashes))
        return graph

    # --- Incremental path ---
    assert existing_graph is not None and old_state is not None
    dirty = state_diff.dirty
    stale = dirty | state_diff.deleted

    if not state_diff.has_changes:
        print("[cortex] notice: no files changed, graph is up to date", file=sys.stderr)
        existing_graph["meta"]["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        sha = get_git_sha(root)
        if sha is not None:
            existing_graph["meta"]["git_sha"] = sha
        # Ensure discovered_from is populated from current file set
        if not existing_graph["meta"].get("discovered_from"):
            existing_graph["meta"]["discovered_from"] = current_file_set
        save_state(root, DiscoveryState(files=current_hashes))
        return existing_graph

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
        r = _run_source(root, source, context)
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
    return graph


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="cortex discover",
        description="Run config-driven Cortex discovery and emit graph JSON to stdout")
    parser.add_argument("root", nargs="?", default=".", help="Project root directory (default: .)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--incremental", action="store_true", default=False,
        help="Only re-extract changed files (default when state file exists)")
    mode.add_argument("--full", action="store_true", default=False,
        help="Force full discovery, ignoring any previous state")
    args = parser.parse_args(argv)

    inc = False if args.full else (True if args.incremental else None)
    result = discover(Path(args.root), incremental=inc)
    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")

if __name__ == "__main__":
    main()
